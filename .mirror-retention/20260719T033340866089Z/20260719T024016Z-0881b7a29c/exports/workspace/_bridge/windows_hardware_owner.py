#!/usr/bin/env python3
"""Read-only Windows Plug and Play hardware inventory and diagnostics.

Ownership: all-device PnP snapshots, exact device detail, problem summaries,
driver/service/topology metadata, fixed hardware event evidence, and snapshot
diffs.
Non-goals: device, driver, service, firmware, storage, power-policy, registry,
or operating-system policy mutation; arbitrary PowerShell, shell, DeviceIoControl,
WinUSB, HID report, serial, smart-card, or vendor command execution.
State behavior: every command is read-only and process-bounded. No resident
watcher, startup entry, service, driver, package, or MCP is created.
Caller context: global hardware diagnosis. USB-specific backends remain in
usb_device_owner.py; guarded USB state changes remain in usb_device_control.py.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from bounded_output import governed_cli_payload  # noqa: E402
from shared.json_cli import configure_utf8_stdio  # noqa: E402


configure_utf8_stdio()

SCHEMA = "windows_hardware_owner.v1"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_EVENT_HOURS = 24 * 30
MAX_EVENT_LIMIT = 500
EVENT_LOGS = (
    ("System", "Microsoft-Windows-Kernel-PnP"),
    ("System", "Microsoft-Windows-UserPnp"),
    ("Microsoft-Windows-DeviceSetupManager/Admin", ""),
    ("Microsoft-Windows-DriverFrameworks-UserMode/Operational", ""),
)
ALLOWED_EXTERNAL_ACTIONS = ("powershell_snapshot", "powershell_device", "powershell_events")
MUTATING_SCRIPT_TOKENS = (
    "disable-pnpdevice",
    "enable-pnpdevice",
    "remove-pnpdevice",
    "restart-computer",
    "restart-service",
    "set-pnpdevice",
    "stop-service",
    "start-service",
    "pnputil",
    "deviceioctl",
    "set-ciminstance",
    "remove-ciminstance",
    "new-ciminstance",
    "invoke-cimmethod",
    "set-itemproperty",
    "new-itemproperty",
    "remove-itemproperty",
    "start-process",
)


def _windows_directory() -> Path:
    if os.name != "nt":
        return Path("/")
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise RuntimeError("unable to resolve the Windows directory")
    return Path(buffer.value)


POWERSHELL = _windows_directory() / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

SNAPSHOT_QUERY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$entityProperties=@(
  'PNPDeviceID',
  'PNPClass',
  'ClassGuid',
  'Name',
  'Status',
  'ConfigManagerErrorCode',
  'Present',
  'Service',
  'Manufacturer'
)
$driverProperties=@(
  'DeviceID',
  'DriverVersion',
  'DriverDate',
  'DriverProviderName',
  'InfName'
)
$entities=@(Get-CimInstance -ClassName Win32_PnPEntity -Property $entityProperties -ErrorAction Stop)
$drivers=@(Get-CimInstance -ClassName Win32_PnPSignedDriver -Property $driverProperties -ErrorAction Stop)
$driverMap=@{}
foreach($driver in $drivers){
  $id=[string]$driver.DeviceID
  if($id -and -not $driverMap.ContainsKey($id)){$driverMap[$id]=$driver}
}
$rows=@($entities | ForEach-Object {
  $entity=$_
  $id=[string]$entity.PNPDeviceID
  $driver=if($driverMap.ContainsKey($id)){$driverMap[$id]}else{$null}
  [pscustomobject]@{
    instance_id=$id
    class=[string]$entity.PNPClass
    class_guid=[string]$entity.ClassGuid
    friendly_name=[string]$entity.Name
    status=[string]$entity.Status
    problem_code=if($null -ne $entity.ConfigManagerErrorCode){[int]$entity.ConfigManagerErrorCode}else{0}
    present=if($null -ne $entity.Present){[bool]$entity.Present}else{$true}
    parent_instance_id=''
    child_instance_ids=@()
    child_topology_known=$false
    container_id=''
    location_paths=@()
    hardware_ids=@()
    compatible_ids=@()
    service=[string]$entity.Service
    enumerator=if($id){[string](($id -split '\\',2)[0])}else{''}
    bus_type_guid=''
    bus_reported_description=''
    manufacturer=[string]$entity.Manufacturer
    driver_version=if($null -ne $driver){[string]$driver.DriverVersion}else{''}
    driver_date=if($null -ne $driver -and $null -ne $driver.DriverDate){([datetime]$driver.DriverDate).ToUniversalTime().ToString('o')}else{''}
    driver_provider=if($null -ne $driver){[string]$driver.DriverProviderName}else{''}
    driver_inf=if($null -ne $driver){[string]$driver.InfName}else{''}
    safe_removal_required=$false
    safe_removal_known=$false
    removal_policy=0
  }
})
[pscustomobject]@{
  machine=[pscustomobject]@{
    computer_name=[string]$env:COMPUTERNAME
    os_version=[string][Environment]::OSVersion.Version
    architecture=[string]$env:PROCESSOR_ARCHITECTURE
  }
  devices=$rows
} | ConvertTo-Json -Compress -Depth 8
"""


DETAIL_QUERY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$target=[string]$env:CODEX_HARDWARE_INSTANCE_ID
$devices=@(Get-PnpDevice -InstanceId $target -ErrorAction Stop)
$detailKeys=@(
  'DEVPKEY_Device_ClassGuid',
  'DEVPKEY_Device_Parent',
  'DEVPKEY_Device_Children',
  'DEVPKEY_Device_ContainerId',
  'DEVPKEY_Device_LocationPaths',
  'DEVPKEY_Device_HardwareIds',
  'DEVPKEY_Device_CompatibleIds',
  'DEVPKEY_Device_ProblemCode',
  'DEVPKEY_Device_IsPresent',
  'DEVPKEY_Device_Service',
  'DEVPKEY_Device_EnumeratorName',
  'DEVPKEY_Device_BusTypeGuid',
  'DEVPKEY_Device_BusReportedDeviceDesc',
  'DEVPKEY_Device_Manufacturer',
  'DEVPKEY_Device_DriverVersion',
  'DEVPKEY_Device_DriverDate',
  'DEVPKEY_Device_DriverProvider',
  'DEVPKEY_Device_DriverInfPath',
  'DEVPKEY_Device_SafeRemovalRequired',
  'DEVPKEY_Device_RemovalPolicy'
)
function Strings($value){if($null -eq $value){return @()}; return @($value | ForEach-Object {[string]$_} | Where-Object {$_})}
$propertyMap=@{}
$devices | Get-PnpDeviceProperty -KeyName $detailKeys -ErrorAction SilentlyContinue | ForEach-Object {
  $id=[string]$_.InstanceId
  if(-not $propertyMap.ContainsKey($id)){$propertyMap[$id]=@{}}
  $propertyMap[$id][[string]$_.KeyName]=$_.Data
}
$rows=@()
foreach($device in $devices){
  $props=if($propertyMap.ContainsKey([string]$device.InstanceId)){$propertyMap[[string]$device.InstanceId]}else{@{}}
  $rows += [pscustomobject]@{
    instance_id=[string]$device.InstanceId
    class=[string]$device.Class
    class_guid=[string]$props['DEVPKEY_Device_ClassGuid']
    friendly_name=[string]$device.FriendlyName
    status=[string]$device.Status
    problem_code=if($null -ne $props['DEVPKEY_Device_ProblemCode']){[int]$props['DEVPKEY_Device_ProblemCode']}else{0}
    present=if($null -ne $props['DEVPKEY_Device_IsPresent']){[bool]$props['DEVPKEY_Device_IsPresent']}else{$true}
    parent_instance_id=[string]$props['DEVPKEY_Device_Parent']
    child_instance_ids=@(Strings $props['DEVPKEY_Device_Children'])
    child_topology_known=[bool]$props.ContainsKey('DEVPKEY_Device_Children')
    container_id=[string]$props['DEVPKEY_Device_ContainerId']
    location_paths=@(Strings $props['DEVPKEY_Device_LocationPaths'])
    hardware_ids=@(Strings $props['DEVPKEY_Device_HardwareIds'])
    compatible_ids=@(Strings $props['DEVPKEY_Device_CompatibleIds'])
    service=[string]$props['DEVPKEY_Device_Service']
    enumerator=[string]$props['DEVPKEY_Device_EnumeratorName']
    bus_type_guid=[string]$props['DEVPKEY_Device_BusTypeGuid']
    bus_reported_description=[string]$props['DEVPKEY_Device_BusReportedDeviceDesc']
    manufacturer=[string]$props['DEVPKEY_Device_Manufacturer']
    driver_version=[string]$props['DEVPKEY_Device_DriverVersion']
    driver_date=if($null -ne $props['DEVPKEY_Device_DriverDate']){([datetime]$props['DEVPKEY_Device_DriverDate']).ToUniversalTime().ToString('o')}else{''}
    driver_provider=[string]$props['DEVPKEY_Device_DriverProvider']
    driver_inf=[string]$props['DEVPKEY_Device_DriverInfPath']
    safe_removal_required=if($null -ne $props['DEVPKEY_Device_SafeRemovalRequired']){[bool]$props['DEVPKEY_Device_SafeRemovalRequired']}else{$false}
    safe_removal_known=[bool]$props.ContainsKey('DEVPKEY_Device_SafeRemovalRequired')
    removal_policy=if($null -ne $props['DEVPKEY_Device_RemovalPolicy']){[int]$props['DEVPKEY_Device_RemovalPolicy']}else{0}
  }
}
[pscustomobject]@{
  machine=[pscustomobject]@{
    computer_name=[string]$env:COMPUTERNAME
    os_version=[string][Environment]::OSVersion.Version
    architecture=[string]$env:PROCESSOR_ARCHITECTURE
  }
  devices=$rows
} | ConvertTo-Json -Compress -Depth 8
"""


def _events_script(hours: int, limit: int) -> str:
    if not 1 <= hours <= MAX_EVENT_HOURS:
        raise ValueError(f"hours must be between 1 and {MAX_EVENT_HOURS}")
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_EVENT_LIMIT}")
    sources = json.dumps([{"log": log, "provider": provider} for log, provider in EVENT_LOGS])
    return rf"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$start=(Get-Date).AddHours(-{hours})
$sources=ConvertFrom-Json @'
{sources}
'@
$rows=@()
foreach($source in $sources){{
  try{{
    $filter=@{{LogName=[string]$source.log;StartTime=$start}}
    if([string]$source.provider){{$filter.ProviderName=[string]$source.provider}}
    Get-WinEvent -FilterHashtable $filter -MaxEvents {limit} -ErrorAction Stop | ForEach-Object {{
      $rows += [pscustomobject]@{{
        time=$_.TimeCreated.ToUniversalTime().ToString('o')
        event_id=[int]$_.Id
        level=[string]$_.LevelDisplayName
        provider=[string]$_.ProviderName
        log_name=[string]$_.LogName
        record_id=[long]$_.RecordId
        message=([string]$_.Message).Substring(0,[Math]::Min(2000,([string]$_.Message).Length))
      }}
    }}
  }}catch{{}}
}}
@($rows | Sort-Object time -Descending | Select-Object -First {limit}) | ConvertTo-Json -Compress -Depth 5
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _listify(value) if str(item)]


def _validate_instance_id(instance_id: str) -> str:
    value = str(instance_id or "").strip()
    if not value or len(value) > 512 or any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("instance_id must be a bounded single-line value")
    if "\\" not in value:
        raise ValueError("instance_id must include a PnP enumerator prefix")
    return value


def _run_powershell(script: str, *, env: dict[str, str] | None = None, timeout: int = 90) -> Any:
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
        raise RuntimeError((completed.stderr or completed.stdout or "PowerShell query failed").strip()[-2000:])
    return json.loads(completed.stdout.strip() or "null")


def normalize_device(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("child_instance_ids", "location_paths", "hardware_ids", "compatible_ids"):
        result[key] = _string_list(result.get(key))
    result["problem_code"] = int(result.get("problem_code") or 0)
    result["present"] = bool(result.get("present"))
    result["child_topology_known"] = bool(result.get("child_topology_known"))
    result["safe_removal_required"] = bool(result.get("safe_removal_required"))
    result["safe_removal_known"] = bool(result.get("safe_removal_known"))
    result["removal_policy"] = int(result.get("removal_policy") or 0)
    return result


def query_devices(instance_id: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target = _validate_instance_id(instance_id) if instance_id else ""
    env = dict(os.environ)
    env["CODEX_HARDWARE_INSTANCE_ID"] = target
    started = time.monotonic()
    raw = _run_powershell(DETAIL_QUERY_SCRIPT if target else SNAPSHOT_QUERY_SCRIPT, env=env)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if not isinstance(raw, dict):
        raise RuntimeError("PnP query did not return an object")
    rows = [normalize_device(item) for item in _listify(raw.get("devices")) if isinstance(item, dict)]
    if target:
        rows = [item for item in rows if str(item.get("instance_id") or "").casefold() == target.casefold()]
        if len(rows) != 1:
            raise RuntimeError(f"exact target resolution returned {len(rows)} devices")
    machine = dict(raw.get("machine") or {})
    machine["query_mode"] = "exact_device" if target else "fast_inventory"
    machine["query_elapsed_ms"] = elapsed_ms
    return machine, rows


def stable_device_fingerprint(device: dict[str, Any]) -> str:
    identity = {
        "instance_id": str(device.get("instance_id") or "").casefold(),
        "class_guid": str(device.get("class_guid") or "").casefold(),
        "container_id": str(device.get("container_id") or "").casefold(),
        "parent_instance_id": str(device.get("parent_instance_id") or "").casefold(),
        "hardware_ids": sorted(item.casefold() for item in _string_list(device.get("hardware_ids"))),
        "compatible_ids": sorted(item.casefold() for item in _string_list(device.get("compatible_ids"))),
        "location_paths": sorted(item.casefold() for item in _string_list(device.get("location_paths"))),
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_problem(device: dict[str, Any]) -> bool:
    status = str(device.get("status") or "").casefold()
    return int(device.get("problem_code") or 0) != 0 or status not in {"ok", "unknown"} or not bool(device.get("present"))


def build_summary(devices: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(devices)
    class_counts = Counter(str(item.get("class") or "(unclassified)") for item in rows)
    status_counts = Counter(str(item.get("status") or "(unknown)") for item in rows)
    enumerator_counts = Counter(str(item.get("enumerator") or "(unknown)") for item in rows)
    problem_counts = Counter(str(int(item.get("problem_code") or 0)) for item in rows if int(item.get("problem_code") or 0))
    return {
        "device_count": len(rows),
        "problem_device_count": sum(1 for item in rows if is_problem(item)),
        "class_count": len(class_counts),
        "class_counts": dict(sorted(class_counts.items(), key=lambda item: (-item[1], item[0].casefold()))),
        "status_counts": dict(sorted(status_counts.items(), key=lambda item: (-item[1], item[0].casefold()))),
        "enumerator_counts": dict(sorted(enumerator_counts.items(), key=lambda item: (-item[1], item[0].casefold()))),
        "problem_code_counts": dict(sorted(problem_counts.items(), key=lambda item: int(item[0]))),
    }


def collect_snapshot(*, query: Callable[[str], tuple[dict[str, Any], list[dict[str, Any]]]] = query_devices) -> dict[str, Any]:
    machine, devices = query("")
    devices = sorted(devices, key=lambda item: (str(item.get("class") or "").casefold(), str(item.get("friendly_name") or "").casefold(), str(item.get("instance_id") or "").casefold()))
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "read_only": True,
        "captured_at": now_iso(),
        "detail_level": "fast_inventory",
        "topology_scope": "exact_device_only",
        "machine": machine,
        "summary": build_summary(devices),
        "problems": [item for item in devices if is_problem(item)],
        "devices": devices,
        "safety": {
            "device_writes_supported": False,
            "driver_changes_supported": False,
            "service_changes_supported": False,
            "resident_watch_supported": False,
            "arbitrary_command_supported": False,
        },
    }


def snapshot_view(snapshot: dict[str, Any], *, full: bool) -> dict[str, Any]:
    if full:
        return snapshot
    return {
        **{
            key: snapshot.get(key)
            for key in (
                "schema",
                "ok",
                "read_only",
                "captured_at",
                "detail_level",
                "topology_scope",
                "machine",
                "summary",
                "problems",
                "safety",
            )
        },
        "device_sample": list(snapshot.get("devices") or [])[:30],
        "device_sample_limit": 30,
        "full_result_ref": "python _bridge\\windows_hardware_owner.py snapshot --full",
    }


def exact_device(instance_id: str, *, query: Callable[[str], tuple[dict[str, Any], list[dict[str, Any]]]] = query_devices) -> dict[str, Any]:
    _, rows = query(_validate_instance_id(instance_id))
    device = rows[0]
    return {
        "schema": f"{SCHEMA}.device",
        "ok": True,
        "read_only": True,
        "detail_level": "exact_device",
        "device": device,
        "stable_fingerprint": stable_device_fingerprint(device),
        "problem": is_problem(device),
    }


def problem_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.problems",
        "ok": True,
        "read_only": True,
        "captured_at": snapshot.get("captured_at"),
        "problem_count": len(snapshot.get("problems") or []),
        "problems": list(snapshot.get("problems") or []),
    }


def class_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for device in snapshot.get("devices") or []:
        groups.setdefault(str(device.get("class") or "(unclassified)"), []).append(device)
    return {
        "schema": f"{SCHEMA}.classes",
        "ok": True,
        "read_only": True,
        "class_count": len(groups),
        "classes": [
            {
                "class": name,
                "device_count": len(rows),
                "problem_count": sum(1 for item in rows if is_problem(item)),
                "sample_instance_ids": [str(item.get("instance_id") or "") for item in rows[:5]],
            }
            for name, rows in sorted(groups.items(), key=lambda item: item[0].casefold())
        ],
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("devices"), list):
        raise ValueError("snapshot must be a JSON object with a devices list")
    return payload


def _device_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("instance_id") or "").casefold(): item for item in snapshot.get("devices") or [] if isinstance(item, dict) and item.get("instance_id")}


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = _device_map(before)
    after_map = _device_map(after)
    added = [after_map[key] for key in sorted(after_map.keys() - before_map.keys())]
    removed = [before_map[key] for key in sorted(before_map.keys() - after_map.keys())]
    changed: list[dict[str, Any]] = []
    for key in sorted(before_map.keys() & after_map.keys()):
        old = before_map[key]
        new = after_map[key]
        fields = [name for name in ("class", "friendly_name", "status", "problem_code", "present", "manufacturer", "service", "driver_version", "driver_inf") if old.get(name) != new.get(name)]
        if fields:
            changed.append({"instance_id": new.get("instance_id"), "changed_fields": fields, "before": old, "after": new})
    return {
        "schema": f"{SCHEMA}.diff",
        "ok": True,
        "read_only": True,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def collect_events(*, hours: int, limit: int) -> dict[str, Any]:
    rows = [item for item in _listify(_run_powershell(_events_script(hours, limit), timeout=60)) if isinstance(item, dict)]
    return {
        "schema": f"{SCHEMA}.events",
        "ok": True,
        "read_only": True,
        "hours": hours,
        "limit": limit,
        "event_count": len(rows),
        "sources": [{"log": log, "provider": provider} for log, provider in EVENT_LOGS],
        "events": rows,
    }


def doctor(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    current = snapshot or collect_snapshot()
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": bool(current.get("ok")) and POWERSHELL.is_file(),
        "read_only": True,
        "powershell_available": POWERSHELL.is_file(),
        "device_count": current.get("summary", {}).get("device_count", 0),
        "problem_device_count": current.get("summary", {}).get("problem_device_count", 0),
        "problem_devices_are_advisory": True,
        "issues": [],
    }


def validate(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    current = snapshot or collect_snapshot()
    text = (SNAPSHOT_QUERY_SCRIPT + DETAIL_QUERY_SCRIPT + _events_script(1, 1)).casefold()
    checks = [
        {"name": "fixed_powershell", "ok": POWERSHELL.is_file(), "path": str(POWERSHELL)},
        {"name": "read_only_script_contract", "ok": not any(token in text for token in MUTATING_SCRIPT_TOKENS)},
        {"name": "fixed_event_sources", "ok": len(EVENT_LOGS) == 4 and all(log for log, _ in EVENT_LOGS)},
        {"name": "bounded_events", "ok": MAX_EVENT_HOURS == 720 and MAX_EVENT_LIMIT == 500},
        {
            "name": "fast_snapshot_avoids_bulk_property_queries",
            "ok": "get-ciminstance" in SNAPSHOT_QUERY_SCRIPT.casefold()
            and "get-pnpdeviceproperty" not in SNAPSHOT_QUERY_SCRIPT.casefold(),
        },
        {
            "name": "exact_detail_owns_expensive_properties",
            "ok": "get-pnpdeviceproperty" in DETAIL_QUERY_SCRIPT.casefold()
            and "devpkey_device_children" in DETAIL_QUERY_SCRIPT.casefold(),
        },
        {"name": "live_or_supplied_snapshot", "ok": bool(current.get("ok")) and int(current.get("summary", {}).get("device_count", 0)) > 0},
        {"name": "mutation_capabilities_absent", "ok": not any(bool(current.get("safety", {}).get(key)) for key in current.get("safety", {}))},
    ]
    issues = [{"severity": "risk", "code": "validation_check_failed", "check": item["name"]} for item in checks if not item.get("ok")]
    return {"schema": f"{SCHEMA}.validate", "ok": not issues, "read_only": True, "generated_at": now_iso(), "checks": checks, "issues": issues}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Windows hardware PnP owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    device = sub.add_parser("device")
    device.add_argument("--instance-id", required=True)
    sub.add_parser("problems")
    sub.add_parser("classes")
    events = sub.add_parser("events")
    events.add_argument("--hours", type=int, default=24)
    events.add_argument("--limit", type=int, default=100)
    diff = sub.add_parser("diff")
    diff.add_argument("--before", required=True)
    diff.add_argument("--after", required=True)
    sub.add_parser("doctor")
    sub.add_parser("validate")
    for command in sub.choices.values():
        command.add_argument("--full", action="store_true")
    return parser


def _full_result_ref(args: argparse.Namespace) -> str:
    command = f"python _bridge\\windows_hardware_owner.py {args.command}"
    if args.command == "device":
        command += f" --instance-id {_powershell_quote(args.instance_id)}"
    elif args.command == "events":
        command += f" --hours {args.hours} --limit {args.limit}"
    elif args.command == "diff":
        command += f" --before {_powershell_quote(args.before)} --after {_powershell_quote(args.after)}"
    return f"{command} --full"


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "snapshot":
            payload = snapshot_view(collect_snapshot(), full=bool(args.full))
        elif args.command == "device":
            payload = exact_device(args.instance_id)
        elif args.command == "problems":
            payload = problem_report(collect_snapshot())
        elif args.command == "classes":
            payload = class_report(collect_snapshot())
        elif args.command == "events":
            payload = collect_events(hours=args.hours, limit=args.limit)
        elif args.command == "diff":
            payload = diff_snapshots(load_snapshot(Path(args.before)), load_snapshot(Path(args.after)))
        elif args.command == "doctor":
            payload = doctor()
        else:
            payload = validate()
    except Exception as exc:
        payload = {"schema": f"{SCHEMA}.{args.command}", "ok": False, "read_only": True, "error_class": type(exc).__name__, "reason": str(exc)}
    projected = governed_cli_payload(
        payload,
        full=bool(args.full),
        full_result_ref=_full_result_ref(args),
        max_success_bytes=128 * 1024 if args.full else 24 * 1024,
    )
    print(json.dumps(projected, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
