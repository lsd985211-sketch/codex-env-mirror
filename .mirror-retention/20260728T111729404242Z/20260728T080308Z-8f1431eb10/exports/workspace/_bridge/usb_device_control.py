#!/usr/bin/env python3
"""Guard narrowly scoped Windows USB device state operations.

Ownership: deterministic control plans, target identity and policy checks,
explicit-confirmation execution, post-action acceptance, operation receipts,
and rollback of owner-created disable operations.
Non-goals: driver install/removal, device removal, firmware, storage writes,
format/eject, policy changes, arbitrary shell/PowerShell/PnPUtil arguments,
ADB/Fastboot commands, or control of core USB, input, storage, or network nodes.
State behavior: plan/doctor/validate/status are read-only. Apply can rescan PnP,
restart an eligible leaf, or disable a tightly allowlisted leaf; rollback can
only re-enable a device disabled by a validated owner receipt.
Caller context: direct, explicitly approved hardware maintenance after the
read-only usb_device_owner has identified an exact target.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


def _windows_directory() -> Path:
    if os.name != "nt":
        return Path("/")
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise RuntimeError("unable to resolve the Windows directory")
    return Path(buffer.value)


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from bounded_output import governed_cli_payload  # noqa: E402
from shared.json_cli import configure_utf8_stdio  # noqa: E402


configure_utf8_stdio()

SCHEMA = "usb_device_control.v1"
PLATFORM_SCOPE = "windows_host"
POLICY_VERSION = "2026-07-17.phase2.1"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
WINDOWS_DIRECTORY = _windows_directory()
SYSTEM32 = WINDOWS_DIRECTORY / "System32"
PNPUTIL = SYSTEM32 / "pnputil.exe"
POWERSHELL = SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
RUNTIME_ROOT = BRIDGE / "runtime" / "usb_device_control"
RECEIPT_ROOT = RUNTIME_ROOT / "receipts"
MUTEX_NAME = "Global\\CodexUsbDeviceControl"
OPERATION_ID_RE = re.compile(r"^[0-9a-f]{24}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")
POST_STATE_ATTEMPTS = 6
POST_STATE_INTERVAL_SECONDS = 0.5

ALLOWED_ACTIONS = ("rescan", "restart", "disable")
ALLOWED_COMMANDS = {
    "rescan": ("/scan-devices",),
    "restart": ("/restart-device", "<exact-instance-id>"),
    "disable": ("/disable-device", "<exact-instance-id>"),
    "rollback_disable": ("/enable-device", "<receipt-instance-id>"),
}
PROTECTED_CLASSES = {
    "bluetooth",
    "diskdrive",
    "display",
    "hidclass",
    "keyboard",
    "mouse",
    "net",
    "scsiadapter",
    "system",
    "usb",
    "volume",
    "volumesnapshot",
    "wpd",
}
DISABLE_ALLOWED_CLASSES = {"camera", "image", "media", "ports", "printer", "usbdevice"}
RESTART_ALLOWED_CLASSES = DISABLE_ALLOWED_CLASSES | {"sensor"}
PROTECTED_USB_ID_MARKERS = ("USB\\CLASS_03", "USB\\CLASS_08", "USB\\CLASS_09", "USB\\CLASS_E0")
PROTECTED_NAME_MARKERS = (
    "host controller",
    "root hub",
    "composite device",
    "keyboard",
    "mouse",
    "storage",
    "disk",
    "volume",
    "network",
    "bluetooth",
)

TARGET_QUERY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$target=[string]$env:CODEX_USB_INSTANCE_ID
$devices=@(Get-PnpDevice -InstanceId $target -ErrorAction Stop)
$keys=@(
  'DEVPKEY_Device_Parent',
  'DEVPKEY_Device_ContainerId',
  'DEVPKEY_Device_LocationPaths',
  'DEVPKEY_Device_HardwareIds',
  'DEVPKEY_Device_CompatibleIds',
  'DEVPKEY_Device_Children',
  'DEVPKEY_Device_ProblemCode',
  'DEVPKEY_Device_IsPresent',
  'DEVPKEY_Device_DriverVersion',
  'DEVPKEY_Device_DriverInfPath'
)
$rows=@()
foreach($device in $devices){
  $props=@{}
  Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName $keys -ErrorAction SilentlyContinue | ForEach-Object {
    $props[[string]$_.KeyName]=$_.Data
  }
  function Strings($value){if($null -eq $value){return @()}; return @($value | ForEach-Object {[string]$_} | Where-Object {$_})}
  $rows += [pscustomobject]@{
    instance_id=[string]$device.InstanceId
    class=[string]$device.Class
    friendly_name=[string]$device.FriendlyName
    status=[string]$device.Status
    problem_code=if($null -ne $props['DEVPKEY_Device_ProblemCode']){[int]$props['DEVPKEY_Device_ProblemCode']}else{0}
    present=if($null -ne $props['DEVPKEY_Device_IsPresent']){[bool]$props['DEVPKEY_Device_IsPresent']}else{$true}
    parent_instance_id=[string]$props['DEVPKEY_Device_Parent']
    container_id=[string]$props['DEVPKEY_Device_ContainerId']
    location_paths=@(Strings $props['DEVPKEY_Device_LocationPaths'])
    hardware_ids=@(Strings $props['DEVPKEY_Device_HardwareIds'])
    compatible_ids=@(Strings $props['DEVPKEY_Device_CompatibleIds'])
    child_instance_ids=@(Strings $props['DEVPKEY_Device_Children'])
    child_topology_known=[bool]$props.ContainsKey('DEVPKEY_Device_Children')
    driver_version=[string]$props['DEVPKEY_Device_DriverVersion']
    driver_inf=[string]$props['DEVPKEY_Device_DriverInfPath']
  }
}
$rows | ConvertTo-Json -Compress -Depth 6
"""


class ControlError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deferred_platform_payload(action: str) -> dict[str, Any]:
    """Avoid treating unavailable Windows control tools as a WSL defect."""

    return {
        "schema": f"{SCHEMA}.{action}",
        "ok": True,
        "read_only": True,
        "deferred": True,
        "platform_scope": PLATFORM_SCOPE,
        "execution_platform": "windows_host" if os.name == "nt" else "wsl_or_linux",
        "owner_status": "deferred_to_platform_owner",
        "diagnostics": {
            "reason": "owner_deferred_platform_scope",
            "required_platform": PLATFORM_SCOPE,
            "next_action": "run this validator or control plan on the Windows host",
        },
    }


def _json_id(payload: dict[str, Any], length: int = 24) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _validate_instance_id(instance_id: str) -> str:
    value = str(instance_id or "").strip()
    if not value or len(value) > 512 or any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("instance_id must be a bounded single-line value")
    if not value.upper().startswith(("USB\\", "HID\\", "UCM\\")):
        raise ValueError("only USB/HID/UCM instance IDs are accepted")
    return value


def _run_powershell(script: str, *, env: dict[str, str], timeout: int = 20) -> Any:
    completed = subprocess.run(
        [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW,
        env=env,
    )
    if completed.returncode != 0:
        raise ControlError((completed.stderr or completed.stdout or "target query failed").strip()[-2000:])
    return json.loads(completed.stdout.strip() or "null")


def query_target(instance_id: str) -> dict[str, Any]:
    target = _validate_instance_id(instance_id)
    env = dict(os.environ)
    env["CODEX_USB_INSTANCE_ID"] = target
    raw = _run_powershell(TARGET_QUERY_SCRIPT, env=env)
    rows = [item for item in _listify(raw) if isinstance(item, dict)]
    exact = [item for item in rows if str(item.get("instance_id") or "").casefold() == target.casefold()]
    if len(exact) != 1:
        raise ControlError(f"exact target resolution returned {len(exact)} devices")
    row = dict(exact[0])
    row["location_paths"] = [str(item) for item in _listify(row.get("location_paths")) if str(item)]
    row["hardware_ids"] = [str(item) for item in _listify(row.get("hardware_ids")) if str(item)]
    row["compatible_ids"] = [str(item) for item in _listify(row.get("compatible_ids")) if str(item)]
    row["child_instance_ids"] = [str(item) for item in _listify(row.get("child_instance_ids")) if str(item)]
    row["child_topology_known"] = bool(row.get("child_topology_known"))
    row["problem_code"] = int(row.get("problem_code") or 0)
    row["present"] = bool(row.get("present"))
    return row


def target_fingerprint(device: dict[str, Any]) -> str:
    identity = {
        "instance_id": str(device.get("instance_id") or ""),
        "class": str(device.get("class") or ""),
        "container_id": str(device.get("container_id") or ""),
        "parent_instance_id": str(device.get("parent_instance_id") or ""),
        "location_paths": sorted(str(item) for item in _listify(device.get("location_paths")) if str(item)),
        "hardware_ids": sorted(str(item) for item in _listify(device.get("hardware_ids")) if str(item)),
        "compatible_ids": sorted(str(item) for item in _listify(device.get("compatible_ids")) if str(item)),
        "child_instance_ids": sorted(str(item) for item in _listify(device.get("child_instance_ids")) if str(item)),
        "child_topology_known": bool(device.get("child_topology_known")),
        "driver_version": str(device.get("driver_version") or ""),
        "driver_inf": str(device.get("driver_inf") or ""),
    }
    return _json_id(identity, 32)


def _eligibility(action: str, device: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    blockers: list[dict[str, str]] = []
    protections: list[str] = []
    device_class = str(device.get("class") or "").casefold()
    name = str(device.get("friendly_name") or "").casefold()
    instance_id = str(device.get("instance_id") or "").upper()
    device_ids = [str(item).upper() for key in ("hardware_ids", "compatible_ids") for item in _listify(device.get(key))]
    if not bool(device.get("present")):
        blockers.append({"code": "target_not_present", "detail": "Only currently present devices can be planned."})
    if not instance_id.startswith(("USB\\", "HID\\", "UCM\\")):
        blockers.append({"code": "target_not_usb_related", "detail": "Target is outside the USB/HID/UCM boundary."})
    if device_class in PROTECTED_CLASSES:
        blockers.append({"code": "protected_device_class", "detail": device_class})
    marker = next((item for item in PROTECTED_NAME_MARKERS if item in name), "")
    if marker:
        blockers.append({"code": "protected_device_role", "detail": marker})
    protected_id = next((marker for marker in PROTECTED_USB_ID_MARKERS if any(marker in item for item in device_ids)), "")
    if protected_id:
        blockers.append({"code": "protected_usb_interface_class", "detail": protected_id})
    if not bool(device.get("child_topology_known")):
        blockers.append({"code": "child_topology_unknown", "detail": "Leaf status could not be verified."})
    if _listify(device.get("child_instance_ids")):
        blockers.append({"code": "target_is_not_leaf_device", "detail": "Target has child PnP devices."})
    if action == "restart" and device_class not in RESTART_ALLOWED_CLASSES:
        blockers.append({"code": "restart_class_not_allowlisted", "detail": device_class})
    if action == "disable":
        if device_class not in DISABLE_ALLOWED_CLASSES:
            blockers.append({"code": "disable_class_not_allowlisted", "detail": device_class})
        if str(device.get("status") or "").casefold() != "ok" or int(device.get("problem_code") or 0) != 0:
            blockers.append({"code": "disable_requires_healthy_target", "detail": str(device.get("status") or "")})
        protections.append("rollback_receipt_required")
    if action == "restart":
        protections.append("post_restart_healthy_state_required")
    protections.extend(["exact_instance_identity", "fingerprint_recheck", "single_operation_mutex", "explicit_confirmation"])
    return blockers, protections


def build_plan(
    action: str,
    instance_id: str = "",
    *,
    query: Callable[[str], dict[str, Any]] = query_target,
    admin: bool | None = None,
) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in ALLOWED_ACTIONS:
        return {"schema": f"{SCHEMA}.plan", "ok": False, "read_only": True, "blockers": [{"code": "unsupported_action", "detail": action}]}
    device: dict[str, Any] = {}
    blockers: list[dict[str, str]] = []
    protections = ["fixed_pnputil_vector", "no_force", "no_reboot_flag"]
    fingerprint = ""
    if action == "rescan":
        if instance_id:
            blockers.append({"code": "rescan_does_not_accept_target", "detail": "Use a global PnP rescan without an arbitrary selector."})
    else:
        try:
            target = _validate_instance_id(instance_id)
            device = query(target)
            fingerprint = target_fingerprint(device)
            policy_blockers, policy_protections = _eligibility(action, device)
            blockers.extend(policy_blockers)
            protections.extend(policy_protections)
        except Exception as exc:
            blockers.append({"code": "target_resolution_failed", "detail": str(exc)})
    identity = {"policy_version": POLICY_VERSION, "action": action, "target_fingerprint": fingerprint}
    plan_id = _json_id(identity)
    confirm_token = "RESCAN-PNP-DEVICES" if action == "rescan" else f"{action.upper()}-USB:{plan_id}"
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "read_only": True,
        "generated_at": now_iso(),
        "policy_version": POLICY_VERSION,
        "action": action,
        "risk": "L1" if action == "rescan" else "L2" if action == "restart" else "L3",
        "plan_id": plan_id,
        "target_fingerprint": fingerprint,
        "target": device,
        "command_contract": list(ALLOWED_COMMANDS[action]),
        "confirmation_required": confirm_token,
        "admin_required": True,
        "admin_current": is_admin() if admin is None else bool(admin),
        "protections": sorted(set(protections)),
        "rollback": {"available": action == "disable", "owner_receipt_required": action == "disable"},
        "blockers": blockers,
        "next_action": "run apply with the exact fingerprint and confirmation" if not blockers else "resolve blockers without bypassing policy",
    }


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_pnputil(arguments: list[str], *, timeout: int = 45) -> dict[str, Any]:
    allowed_vectors = {
        ("/scan-devices",),
        ("/restart-device", "target"),
        ("/disable-device", "target"),
        ("/enable-device", "target"),
    }
    normalized = tuple([arguments[0], "target"] if len(arguments) == 2 else arguments)
    if normalized not in allowed_vectors:
        raise ControlError("PnPUtil argument vector is not allowlisted")
    completed = subprocess.run(
        [str(PNPUTIL), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW,
    )
    return {
        "returncode": completed.returncode,
        "stdout_tail": str(completed.stdout or "")[-1200:],
        "stderr_tail": str(completed.stderr or "")[-1200:],
        "command": [PNPUTIL.name, *arguments],
    }


@contextlib.contextmanager
def operation_mutex() -> Iterator[None]:
    if os.name != "nt":
        raise ControlError("USB device control is Windows-only")
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ControlError("failed to create USB control mutex")
    acquired = False
    try:
        wait = kernel32.WaitForSingleObject(handle, 0)
        if wait not in (0x00000000, 0x00000080):
            raise ControlError("usb_control_operation_busy")
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _healthy(device: dict[str, Any]) -> bool:
    return bool(device.get("present")) and str(device.get("status") or "").casefold() == "ok" and int(device.get("problem_code") or 0) == 0


def _owner_disabled(device: dict[str, Any]) -> bool:
    return bool(device.get("present")) and int(device.get("problem_code") or 0) == 22


def _safe_query(query: Callable[[str], dict[str, Any]], instance_id: str) -> dict[str, Any]:
    try:
        return query(instance_id)
    except Exception as exc:
        return {"query_error": f"{type(exc).__name__}: {exc}", "instance_id": instance_id}


def _observe_target(
    query: Callable[[str], dict[str, Any]],
    instance_id: str,
    *,
    accept: Callable[[dict[str, Any]], bool],
    terminal: Callable[[dict[str, Any]], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(POST_STATE_ATTEMPTS):
        last = _safe_query(query, instance_id)
        if accept(last) or (terminal is not None and terminal(last)):
            break
        if attempt + 1 < POST_STATE_ATTEMPTS:
            sleep(POST_STATE_INTERVAL_SECONDS)
    return last


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}-{time.time_ns()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _receipt_path(operation_id: str, *, receipt_root: Path = RECEIPT_ROOT) -> Path:
    if not OPERATION_ID_RE.fullmatch(operation_id):
        raise ValueError("operation_id must be 24 lowercase hex characters")
    return receipt_root / f"{operation_id}.json"


def _load_operation_receipt(path: Path, operation_id: str, *, require_disable: bool = False) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError(f"invalid_operation_receipt: {type(exc).__name__}") from exc
    if not isinstance(receipt, dict):
        raise ControlError("invalid_operation_receipt: object_required")
    before = receipt.get("before")
    rollback = receipt.get("rollback")
    action = str(receipt.get("action") or "")
    fingerprint = str(receipt.get("target_fingerprint") or "")
    required = {
        "schema": receipt.get("schema") == f"{SCHEMA}.operation_receipt",
        "operation_id": bool(OPERATION_ID_RE.fullmatch(operation_id)) and receipt.get("operation_id") == operation_id,
        "policy_version": isinstance(receipt.get("policy_version"), str) and bool(receipt.get("policy_version")),
        "action": action in ALLOWED_ACTIONS and (not require_disable or action == "disable"),
        "plan_id": bool(OPERATION_ID_RE.fullmatch(str(receipt.get("plan_id") or ""))),
        "target_fingerprint": fingerprint == "" if action == "rescan" else bool(FINGERPRINT_RE.fullmatch(fingerprint)),
        "before": isinstance(before, dict),
        "rollback": isinstance(rollback, dict),
    }
    if not all(required.values()):
        failed = ",".join(name for name, ok in required.items() if not ok)
        raise ControlError(f"invalid_operation_receipt: {failed}")
    if action != "rescan":
        instance_id = _validate_instance_id(str(before.get("instance_id") or ""))
        if target_fingerprint(before) != fingerprint:
            raise ControlError("invalid_operation_receipt: before_fingerprint_mismatch")
        receipt["before"]["instance_id"] = instance_id
    return receipt


def _execution_error(exc: Exception) -> dict[str, Any]:
    return {
        "returncode": -1,
        "error_class": type(exc).__name__,
        "error": str(exc)[-1200:],
        "command": [],
    }


def apply_control(
    *,
    action: str,
    instance_id: str,
    expected_fingerprint: str,
    confirm: str,
    query: Callable[[str], dict[str, Any]] = query_target,
    execute: Callable[[list[str]], dict[str, Any]] | None = None,
    mutex_factory: Callable[[], contextlib.AbstractContextManager[None]] = operation_mutex,
    admin: bool | None = None,
    receipt_root: Path = RECEIPT_ROOT,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    execute = execute or (lambda arguments: _run_pnputil(arguments))
    action = str(action or "").strip().lower()
    plan = build_plan(action, instance_id, query=query, admin=admin)
    if not plan.get("ok"):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "applied": False, "reason": "control_plan_blocked", "plan": plan}
    if not bool(plan.get("admin_current")):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "applied": False, "reason": "administrator_token_required", "plan_id": plan.get("plan_id")}
    if action != "rescan" and expected_fingerprint != plan.get("target_fingerprint"):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "applied": False, "reason": "target_fingerprint_mismatch", "plan_id": plan.get("plan_id")}
    if confirm != plan.get("confirmation_required"):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "applied": False, "reason": "confirmation_mismatch", "plan_id": plan.get("plan_id")}

    operation_id = secrets.token_hex(12)
    before = dict(plan.get("target") or {})
    recovery: dict[str, Any] = {"attempted": False}
    with mutex_factory():
        if action != "rescan":
            before = query(str(before.get("instance_id") or instance_id))
            if target_fingerprint(before) != plan.get("target_fingerprint"):
                return {"schema": f"{SCHEMA}.apply", "ok": False, "applied": False, "reason": "target_fingerprint_changed_before_execution", "plan_id": plan.get("plan_id")}
            lock_blockers, _ = _eligibility(action, before)
            if lock_blockers:
                return {
                    "schema": f"{SCHEMA}.apply",
                    "ok": False,
                    "applied": False,
                    "reason": "target_policy_changed_before_execution",
                    "plan_id": plan.get("plan_id"),
                    "blockers": lock_blockers,
                }
        arguments = ["/scan-devices"] if action == "rescan" else [f"/{action}-device", str(before.get("instance_id") or instance_id)]
        receipt_path = _receipt_path(operation_id, receipt_root=receipt_root)
        receipt = {
            "schema": f"{SCHEMA}.operation_receipt",
            "ok": False,
            "applied": False,
            "operation_id": operation_id,
            "created_at": now_iso(),
            "policy_version": POLICY_VERSION,
            "action": action,
            "plan_id": plan["plan_id"],
            "target_fingerprint": plan.get("target_fingerprint", ""),
            "before": before,
            "execution": {"status": "pending", "command": [PNPUTIL.name, *arguments]},
            "after": {},
            "acceptance": {"satisfied": False, "rule": "owner post-state, not localized PnPUtil text, determines success"},
            "recovery": recovery,
            "rollback": {
                "available": action == "disable",
                "confirmation_required": f"ROLLBACK-USB:{operation_id}" if action == "disable" else "",
                "status": "pending" if action == "disable" else "not_applicable",
                "attempt_count": 0,
            },
        }
        _write_json_atomic(receipt_path, receipt)
        try:
            execution = execute(arguments)
        except Exception as exc:
            execution = _execution_error(exc)
        target_id = str(before.get("instance_id") or instance_id)
        if action == "rescan":
            after = {}
        elif action == "restart":
            after = _observe_target(
                query,
                target_id,
                accept=lambda row: _healthy(row) and target_fingerprint(row) == plan.get("target_fingerprint"),
                terminal=_owner_disabled,
                sleep=sleep,
            )
        else:
            after = _observe_target(
                query,
                target_id,
                accept=lambda row: _owner_disabled(row) and target_fingerprint(row) == plan.get("target_fingerprint"),
                sleep=sleep,
            )
        if action == "rescan":
            accepted = int(execution.get("returncode", 1)) == 0
        elif action == "restart":
            accepted = int(execution.get("returncode", 1)) == 0 and _healthy(after) and target_fingerprint(after) == plan.get("target_fingerprint")
        else:
            accepted = int(execution.get("returncode", 1)) == 0 and _owner_disabled(after) and target_fingerprint(after) == plan.get("target_fingerprint")
        same_identity = target_fingerprint(after) == plan.get("target_fingerprint")
        if action in {"restart", "disable"} and _healthy(before) and not accepted and _owner_disabled(after) and same_identity:
            try:
                recovery_execution = execute(["/enable-device", str(before.get("instance_id") or instance_id)])
            except Exception as exc:
                recovery_execution = _execution_error(exc)
            recovery_after = _observe_target(
                query,
                target_id,
                accept=lambda row: _healthy(row) and target_fingerprint(row) == plan.get("target_fingerprint"),
                sleep=sleep,
            )
            recovery = {
                "attempted": True,
                "execution": recovery_execution,
                "after": recovery_after,
                "accepted": int(recovery_execution.get("returncode", 1)) == 0 and _healthy(recovery_after),
            }
        rollback_status = "not_applicable"
        if action == "disable":
            if accepted:
                rollback_status = "available"
            elif not recovery.get("accepted"):
                rollback_status = "pending"
        receipt.update(
            {
                "ok": accepted,
                "applied": True,
                "execution": execution,
                "after": after,
                "acceptance": {"satisfied": accepted, "rule": "owner post-state, not localized PnPUtil text, determines success"},
                "recovery": recovery,
                "rollback": {
                    **receipt["rollback"],
                    "available": rollback_status in {"pending", "available"},
                    "status": rollback_status,
                },
            }
        )
        _write_json_atomic(receipt_path, receipt)
    return receipt


def rollback_control(
    *,
    operation_id: str,
    confirm: str,
    query: Callable[[str], dict[str, Any]] = query_target,
    execute: Callable[[list[str]], dict[str, Any]] | None = None,
    mutex_factory: Callable[[], contextlib.AbstractContextManager[None]] = operation_mutex,
    admin: bool | None = None,
    receipt_root: Path = RECEIPT_ROOT,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    execute = execute or (lambda arguments: _run_pnputil(arguments))
    try:
        path = _receipt_path(operation_id, receipt_root=receipt_root)
    except ValueError:
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "operation_receipt_not_found"}
    if confirm != f"ROLLBACK-USB:{operation_id}":
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "confirmation_mismatch"}
    if not (is_admin() if admin is None else bool(admin)):
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "administrator_token_required"}
    with mutex_factory():
        if not path.is_file():
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "operation_receipt_not_found"}
        try:
            receipt = _load_operation_receipt(path, operation_id, require_disable=True)
        except (ControlError, ValueError) as exc:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": str(exc)}
        rollback = receipt["rollback"]
        if rollback.get("status") not in {"pending", "available"}:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "rollback_not_available"}
        instance_id = str(receipt["before"]["instance_id"])
        current = _safe_query(query, instance_id)
        if target_fingerprint(current) != receipt.get("target_fingerprint"):
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "target_fingerprint_mismatch"}
        if _healthy(current):
            accepted = True
            execution = {"status": "not_needed", "command": []}
            after = current
            applied = False
        elif _owner_disabled(current):
            try:
                execution = execute(["/enable-device", instance_id])
            except Exception as exc:
                execution = _execution_error(exc)
            after = (
                _observe_target(
                    query,
                    instance_id,
                    accept=lambda row: _healthy(row) and target_fingerprint(row) == receipt.get("target_fingerprint"),
                    sleep=sleep,
                )
                if int(execution.get("returncode", 1)) == 0
                else _safe_query(query, instance_id)
            )
            accepted = int(execution.get("returncode", 1)) == 0 and _healthy(after)
            applied = True
        else:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "applied": False, "reason": "target_not_in_owner_disabled_state"}
        result = {
            "schema": f"{SCHEMA}.rollback",
            "ok": accepted,
            "applied": applied,
            "operation_id": operation_id,
            "rolled_back_at": now_iso(),
            "execution": execution,
            "after": after,
            "acceptance": {"satisfied": accepted, "rule": "target present, status OK, problem code 0"},
        }
        receipt["rollback"] = {
            **rollback,
            "status": "completed" if accepted else "available",
            "attempt_count": int(rollback.get("attempt_count") or 0) + 1,
            "last_result": result,
        }
        _write_json_atomic(path, receipt)
    return result


def operation_status(operation_id: str, *, receipt_root: Path = RECEIPT_ROOT) -> dict[str, Any]:
    if not OPERATION_ID_RE.fullmatch(operation_id):
        return {"schema": f"{SCHEMA}.status", "ok": False, "reason": "invalid_operation_id"}
    path = receipt_root / f"{operation_id}.json"
    if not path.is_file():
        return {"schema": f"{SCHEMA}.status", "ok": False, "reason": "operation_receipt_not_found"}
    try:
        receipt = _load_operation_receipt(path, operation_id)
    except (ControlError, ValueError) as exc:
        return {"schema": f"{SCHEMA}.status", "ok": False, "reason": str(exc)}
    return {"schema": f"{SCHEMA}.status", "ok": True, "operation": receipt}


def doctor(*, receipt_root: Path = RECEIPT_ROOT) -> dict[str, Any]:
    if os.name != "nt":
        return deferred_platform_payload("doctor")
    receipts = list(receipt_root.glob("*.json")) if receipt_root.is_dir() else []
    invalid_receipts: list[str] = []
    for path in receipts:
        try:
            _load_operation_receipt(path, path.stem)
        except (ControlError, ValueError):
            invalid_receipts.append(path.name)
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": PNPUTIL.is_file() and not invalid_receipts,
        "read_only": True,
        "pnputil_available": PNPUTIL.is_file(),
        "administrator": is_admin(),
        "receipt_count": len(receipts),
        "invalid_receipts": invalid_receipts[:20],
        "allowed_actions": list(ALLOWED_ACTIONS),
        "blocked_capabilities": ["driver changes", "device removal", "firmware", "storage mutation", "format/eject", "arbitrary command", "ADB/Fastboot"],
    }


def validate() -> dict[str, Any]:
    if os.name != "nt":
        return deferred_platform_payload("validate")
    fake_core = {
        "instance_id": "USB\\ROOT_HUB30\\TEST",
        "class": "USB",
        "friendly_name": "USB Root Hub (USB 3.0)",
        "status": "OK",
        "problem_code": 0,
        "present": True,
        "parent_instance_id": "PCI\\TEST",
        "container_id": "{test}",
        "location_paths": [],
        "hardware_ids": [],
        "compatible_ids": [],
        "child_instance_ids": [],
        "child_topology_known": True,
        "driver_version": "1",
        "driver_inf": "usbhub3.inf",
    }
    checks = [
        {"name": "fixed_system_tools", "ok": PNPUTIL.is_file() and POWERSHELL.is_file(), "pnputil": str(PNPUTIL), "powershell": str(POWERSHELL)},
        {"name": "action_allowlist", "ok": set(ALLOWED_ACTIONS) == {"rescan", "restart", "disable"}, "actions": list(ALLOWED_ACTIONS)},
        {"name": "cross_session_mutex", "ok": MUTEX_NAME.startswith("Global\\"), "mutex": MUTEX_NAME},
        {"name": "bounded_post_state", "ok": 1 < POST_STATE_ATTEMPTS <= 10 and 0 < POST_STATE_INTERVAL_SECONDS <= 1},
        {
            "name": "command_vectors",
            "ok": all(not any(token in " ".join(vector).lower() for token in ("remove", "delete", "add-driver", "force", "reboot", "flash", "format")) for vector in ALLOWED_COMMANDS.values()),
            "vectors": ALLOWED_COMMANDS,
        },
        {"name": "rescan_plan", "ok": bool(build_plan("rescan", admin=True).get("ok"))},
        {
            "name": "core_device_negative_guard",
            "ok": not bool(build_plan("restart", fake_core["instance_id"], query=lambda _: fake_core, admin=True).get("ok")),
        },
        {"name": "receipt_root_is_runtime", "ok": RUNTIME_ROOT.parent == BRIDGE / "runtime", "path": str(RECEIPT_ROOT)},
        {"name": "facade_boundary", "ok": True, "detail": "apply/rollback remain direct owner commands; workflow maintenance facade stays read-only"},
    ]
    issues = [{"severity": "risk", "code": "validation_check_failed", "check": item["name"]} for item in checks if not item.get("ok")]
    return {"schema": f"{SCHEMA}.validate", "ok": not issues, "read_only": True, "generated_at": now_iso(), "checks": checks, "issues": issues}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded Windows USB device control owner")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--action", choices=ALLOWED_ACTIONS, required=True)
    plan.add_argument("--instance-id", default="")
    apply = sub.add_parser("apply")
    apply.add_argument("--action", choices=ALLOWED_ACTIONS, required=True)
    apply.add_argument("--instance-id", default="")
    apply.add_argument("--expected-fingerprint", default="")
    apply.add_argument("--confirm", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--operation-id", required=True)
    rollback.add_argument("--confirm", required=True)
    status = sub.add_parser("status")
    status.add_argument("--operation-id", required=True)
    sub.add_parser("doctor")
    sub.add_parser("validate")
    for command in sub.choices.values():
        command.add_argument("--full", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "plan":
            payload = build_plan(args.action, args.instance_id)
        elif args.command == "apply":
            payload = apply_control(action=args.action, instance_id=args.instance_id, expected_fingerprint=args.expected_fingerprint, confirm=args.confirm)
        elif args.command == "rollback":
            payload = rollback_control(operation_id=args.operation_id, confirm=args.confirm)
        elif args.command == "status":
            payload = operation_status(args.operation_id)
        elif args.command == "doctor":
            payload = doctor()
        else:
            payload = validate()
    except Exception as exc:
        payload = {"schema": f"{SCHEMA}.{args.command}", "ok": False, "applied": False, "error_class": type(exc).__name__, "reason": str(exc)}
    projected = governed_cli_payload(payload, full=bool(args.full), full_result_ref=f"python _bridge\\usb_device_control.py {args.command} --full", max_success_bytes=16 * 1024)
    print(json.dumps(projected, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
