#!/usr/bin/env python3
"""Read-only routing facade for the Windows and WSL hardware owners.

Ownership: select the authoritative platform owner, expose the hardware system
capability map, and merge already-owned evidence without changing permissions.
Non-goals: hardware discovery logic, cross-platform command tunneling, device
control, package installation, or treating a WSL projection as Windows truth.
State behavior: read-only and stateless; unsupported platform work is deferred.
Caller context: workflow, skills, and operators needing one stable hardware
entrypoint across the Windows host and WSL execution environments.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import windows_hardware_owner
import wsl_hardware_owner
from bounded_output import governed_cli_payload
from shared.json_cli import configure_utf8_stdio


configure_utf8_stdio()
SCHEMA = "hardware_system_owner.v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_platform() -> str:
    if os.name == "nt":
        return "windows_host"
    if wsl_hardware_owner.is_wsl():
        return "wsl_host"
    return "unsupported"


def capability_map() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.routes",
        "ok": True,
        "read_only": True,
        "current_platform": current_platform(),
        "authority": {
            "windows_host_truth": "windows_hardware_owner",
            "wsl_visible_projection": "wsl_hardware_owner",
            "usb_diagnostics": "usb_device_owner",
            "usb_control": "usb_device_control",
            "mtp_public_media": "mtp_media_archive_owner",
        },
        "rules": [
            "windows_host_is_authoritative_for_physical_devices_drivers_storage_bluetooth_battery_and_displays",
            "wsl_owner_reports_only_linux_visible_virtual_or_forwarded_devices_and_gpu_projection",
            "read_only_facade_never_inherits_usb_control_permissions",
            "cross_platform_completion_requires_each_requested_platform_receipt",
        ],
    }


def deferred(platform_scope: str, command: str) -> dict[str, Any]:
    module = "windows_hardware_owner.py" if platform_scope == "windows_host" else "wsl_hardware_owner.py"
    return {
        "schema": f"{SCHEMA}.platform_receipt",
        "ok": True,
        "read_only": True,
        "accepted": False,
        "deferred": True,
        "reason": "deferred_to_platform_owner",
        "platform_scope": platform_scope,
        "owner_command": f"python _bridge/{module} {command}",
    }


def platform_snapshot(
    platform_scope: str,
    *,
    windows_snapshot: Callable[[], dict[str, Any]] = windows_hardware_owner.collect_snapshot,
    wsl_snapshot: Callable[[], dict[str, Any]] = wsl_hardware_owner.collect_snapshot,
) -> dict[str, Any]:
    here = current_platform()
    if platform_scope != here:
        return deferred(platform_scope, "snapshot")
    return windows_snapshot() if platform_scope == "windows_host" else wsl_snapshot()


def snapshot(requested: str = "auto") -> dict[str, Any]:
    here = current_platform()
    platforms = [here] if requested == "auto" else ["windows_host", "wsl_host"] if requested == "all" else [requested]
    receipts = {name: platform_snapshot(name) for name in platforms if name in {"windows_host", "wsl_host"}}
    pending = [name for name, receipt in receipts.items() if receipt.get("deferred")]
    accepted = [name for name, receipt in receipts.items() if receipt.get("ok") and not receipt.get("deferred")]
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": bool(receipts) and all(receipt.get("ok") for receipt in receipts.values()),
        "read_only": True,
        "generated_at": now_iso(),
        "requested": requested,
        "current_platform": here,
        "complete": not pending and len(accepted) == len(receipts),
        "accepted_platforms": accepted,
        "pending_platforms": pending,
        "platform_receipts": receipts,
        "capability_map": capability_map()["authority"],
    }


def validate() -> dict[str, Any]:
    routes = capability_map()
    current = current_platform()
    owner_result = wsl_hardware_owner.validate() if current == "wsl_host" else windows_hardware_owner.validate() if current == "windows_host" else {"ok": False}
    checks = [
        {"name": "supported_platform", "ok": current in {"windows_host", "wsl_host"}},
        {"name": "current_owner_valid", "ok": bool(owner_result.get("ok")) and not owner_result.get("deferred")},
        {"name": "separate_host_and_projection_authority", "ok": routes["authority"]["windows_host_truth"] != routes["authority"]["wsl_visible_projection"]},
        {"name": "control_owner_not_facade", "ok": routes["authority"]["usb_control"] == "usb_device_control"},
    ]
    issues = [{"severity": "risk", "code": "validation_check_failed", "check": item["name"]} for item in checks if not item["ok"]]
    return {"schema": f"{SCHEMA}.validate", "ok": not issues, "read_only": True, "generated_at": now_iso(), "checks": checks, "issues": issues}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-platform read-only hardware system facade")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("routes")
    snap = sub.add_parser("snapshot")
    snap.add_argument("--platform", choices=("auto", "all", "windows_host", "wsl_host"), default="auto")
    sub.add_parser("validate")
    for child in sub.choices.values():
        child.add_argument("--full", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = capability_map() if args.command == "routes" else snapshot(args.platform) if args.command == "snapshot" else validate()
    ref = f"python _bridge/hardware_system_owner.py {args.command}" + (f" --platform {args.platform}" if args.command == "snapshot" else "") + " --full"
    projected = governed_cli_payload(payload, full=bool(args.full), full_result_ref=ref, max_success_bytes=128 * 1024 if args.full else 24 * 1024)
    print(json.dumps(projected, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
