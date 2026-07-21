#!/usr/bin/env python3
"""Read-only inventory for hardware actually visible inside WSL.

Ownership: WSL kernel, block, forwarded USB, exposed PCI, and NVIDIA GPU
projection evidence plus bounded tool health.
Non-goals: Windows host truth, device control, driver or firmware changes,
mounting, storage mutation, arbitrary commands, or resident monitoring.
State behavior: read-only, stateless, process-bounded, and optional-tool aware.
Caller context: hardware_system_owner and WSL diagnostics that must distinguish
Linux-visible projections from Windows host devices.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounded_output import governed_cli_payload
from shared.json_cli import configure_utf8_stdio


configure_utf8_stdio()

SCHEMA = "wsl_hardware_owner.v1"
PLATFORM_SCOPE = "wsl_host"
COMMAND_TIMEOUT = 20
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
USB_RE = re.compile(
    r"^Bus (?P<bus>\d+) Device (?P<device>\d+): ID "
    r"(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})(?: (?P<name>.*))?$"
)
TOOL_SPECS = {
    "lsblk": {"required": True, "candidates": ("lsblk",), "version_args": ("--version",)},
    "udevadm": {"required": True, "candidates": ("udevadm",), "version_args": ("--version",)},
    "lsusb": {"required": False, "candidates": ("lsusb",), "version_args": ("--version",)},
    "lspci": {"required": False, "candidates": ("lspci",), "version_args": ("--version",)},
    "nvidia-smi": {
        "required": False,
        "candidates": ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"),
        "version_args": ("--query-gpu=driver_version", "--format=csv,noheader"),
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_wsl() -> bool:
    release = platform.release().casefold()
    return "microsoft" in release or "wsl" in release or bool(os.environ.get("WSL_INTEROP"))


def resolve_tool(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


def run_fixed(argv: list[str], *, timeout: int = COMMAND_TIMEOUT) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    stdout = (proc.stdout or "")[:MAX_OUTPUT_BYTES]
    stderr = (proc.stderr or "")[:8192]
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": stdout, "stderr": stderr}


def tool_health() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, spec in TOOL_SPECS.items():
        path = resolve_tool(spec["candidates"])
        probe = run_fixed([path, *spec["version_args"]]) if path else {"ok": False, "stdout": "", "stderr": "not_found"}
        version_lines = str(probe.get("stdout") or probe.get("stderr") or "").strip().splitlines()
        result[name] = {
            "available": bool(path and probe.get("ok")),
            "required": bool(spec["required"]),
            "path": path,
            "version": version_lines[0][:300] if version_lines else "",
        }
    return result


def distro_identity() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path("/etc/os-release")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"ID", "VERSION_ID", "PRETTY_NAME"}:
                values[key.casefold()] = value.strip().strip('"')
    return values


def collect_block(path: str) -> dict[str, Any]:
    fields = "NAME,KNAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,FSTYPE,MOUNTPOINTS,RO,RM"
    probe = run_fixed([path, "--json", "--bytes", "--output", fields])
    if not probe["ok"]:
        return {"ok": False, "reason": probe["stderr"][-1000:], "devices": []}
    try:
        payload = json.loads(probe["stdout"])
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"json_decode_failed: {exc}", "devices": []}
    return {"ok": True, "devices": list(payload.get("blockdevices") or [])}


def collect_usb(path: str) -> dict[str, Any]:
    probe = run_fixed([path])
    rows = []
    if probe["ok"]:
        for line in probe["stdout"].splitlines():
            match = USB_RE.match(line.strip())
            if match:
                rows.append({**match.groupdict(), "vid": match.group("vid").lower(), "pid": match.group("pid").lower()})
    return {"ok": bool(probe["ok"]), "devices": rows, "reason": "" if probe["ok"] else probe["stderr"][-1000:]}


def collect_pci(path: str) -> dict[str, Any]:
    probe = run_fixed([path, "-mm"])
    lines = [line for line in probe["stdout"].splitlines() if line.strip()]
    return {"ok": bool(probe["ok"]), "device_count": len(lines), "devices": lines[:500], "reason": "" if probe["ok"] else probe["stderr"][-1000:]}


def collect_gpu(path: str) -> dict[str, Any]:
    fields = "name,uuid,driver_version,memory.total,compute_cap"
    probe = run_fixed([path, f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
    rows = []
    if probe["ok"]:
        for values in csv.reader(io.StringIO(probe["stdout"])):
            if len(values) == 5:
                rows.append(dict(zip(("name", "uuid", "driver_version", "memory_total_mib", "compute_capability"), (item.strip() for item in values))))
    return {"ok": bool(probe["ok"]), "gpus": rows, "reason": "" if probe["ok"] else probe["stderr"][-1000:]}


def collect_snapshot(*, tools: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    health = tools or tool_health()
    required_ok = all(item["available"] for item in health.values() if item["required"])
    current_wsl = is_wsl()
    block = collect_block(health["lsblk"]["path"]) if health["lsblk"]["available"] else {"ok": False, "devices": [], "reason": "lsblk_not_found"}
    usb = collect_usb(health["lsusb"]["path"]) if health["lsusb"]["available"] else {"ok": False, "devices": [], "reason": "lsusb_not_found"}
    pci = collect_pci(health["lspci"]["path"]) if health["lspci"]["available"] else {"ok": False, "devices": [], "device_count": 0, "reason": "lspci_not_found"}
    gpu = collect_gpu(health["nvidia-smi"]["path"]) if health["nvidia-smi"]["available"] else {"ok": False, "gpus": [], "reason": "nvidia_smi_not_found"}
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": bool(current_wsl and required_ok and block.get("ok")),
        "read_only": True,
        "captured_at": now_iso(),
        "platform": {"scope": PLATFORM_SCOPE, "is_wsl": current_wsl, "kernel_release": platform.release(), "distro": distro_identity()},
        "authority": {"host_hardware_truth": False, "linux_visible_projection_only": True, "windows_owner": "windows_hardware_owner"},
        "tools": health,
        "block": block,
        "usb": usb,
        "pci": pci,
        "gpu": gpu,
        "safety": {"device_writes_supported": False, "mount_changes_supported": False, "driver_changes_supported": False, "arbitrary_command_supported": False},
    }


def doctor(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    current = snapshot or collect_snapshot()
    optional_missing = [name for name, item in current["tools"].items() if not item["required"] and not item["available"]]
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": bool(current.get("ok")),
        "read_only": True,
        "required_tools_ok": all(item["available"] for item in current["tools"].values() if item["required"]),
        "optional_missing": optional_missing,
        "optional_missing_is_advisory": True,
        "gpu_projection_available": bool(current.get("gpu", {}).get("gpus")),
        "usb_projection_available": bool(current.get("usb", {}).get("ok")),
        "issues": [] if current.get("ok") else [{"severity": "risk", "code": "wsl_hardware_snapshot_failed"}],
    }


def validate(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    current = snapshot or collect_snapshot()
    checks = [
        {"name": "wsl_platform", "ok": bool(current.get("platform", {}).get("is_wsl"))},
        {"name": "required_tools", "ok": all(item["available"] for item in current.get("tools", {}).values() if item["required"])},
        {"name": "block_inventory", "ok": bool(current.get("block", {}).get("ok"))},
        {"name": "projection_not_host_truth", "ok": current.get("authority", {}).get("host_hardware_truth") is False},
        {"name": "mutation_capabilities_absent", "ok": not any(current.get("safety", {}).values())},
    ]
    issues = [{"severity": "risk", "code": "validation_check_failed", "check": item["name"]} for item in checks if not item["ok"]]
    return {"schema": f"{SCHEMA}.validate", "ok": not issues, "read_only": True, "generated_at": now_iso(), "checks": checks, "issues": issues}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only WSL-visible hardware owner")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "doctor", "validate"):
        child = sub.add_parser(command)
        child.add_argument("--full", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = collect_snapshot() if args.command == "snapshot" else doctor() if args.command == "doctor" else validate()
    projected = governed_cli_payload(payload, full=bool(args.full), full_result_ref=f"python _bridge/wsl_hardware_owner.py {args.command} --full", max_success_bytes=128 * 1024 if args.full else 24 * 1024)
    print(json.dumps(projected, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
