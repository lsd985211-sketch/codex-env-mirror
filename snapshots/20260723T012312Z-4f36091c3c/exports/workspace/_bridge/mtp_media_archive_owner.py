#!/usr/bin/env python3
"""Read-only MTP media archive admission and planning owner.

Ownership: bounded Windows Shell MTP snapshots and archive-plan contracts.
Non-goals: USB/PnP/usbipd control, arbitrary PowerShell, device writes,
WeChat private-database access, chat-history restore, or data copying.
Caller context: content-transfer workflows consume this owner before any
separately approved materialization operation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from typing import Any

from shared.windows_powershell import encoded_command_arguments


SCHEMA = "mtp_media_archive_owner.v1"
MAX_TOP_LEVEL_FOLDERS = 200
PUBLIC_WECHAT_ROOTS = ("Tencent/MicroMsg", "Android/media/com.tencent.mm")
KNOWN_PUBLIC_ROOTS = ("Tencent", "Android", "DCIM", "Pictures", "Movies")
VIDEO_ARCHIVE_SCHEMA = "mtp_video_archive_plan.v1"
VIDEO_ARCHIVE_BLOCKED_REASON = "headless_shell_copyhere_backend_forbidden"
VIDEO_EXTENSIONS = (".3gp", ".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".m2ts", ".ts", ".webm", ".wmv")
VIDEO_PUBLIC_ROOTS = ("DCIM", "Movies", "Pictures", "Download", "Tencent", "Android/media")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def windows_powershell() -> str:
    return shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def snapshot_script(device_name: str) -> str:
    literal = json.dumps(device_name, ensure_ascii=False)
    return f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Get-MtpItems([object]$folder) {{
  @($folder.Items() | ForEach-Object {{ [void]$_.Name; $_ }})
}}
$shell = New-Object -ComObject Shell.Application
$device = @(Get-MtpItems ($shell.Namespace(17)) | Where-Object {{ $_.Name -eq {literal} }} | Select-Object -First 1)[0]
if ($null -eq $device) {{ throw 'mtp_device_not_found' }}
$volumes = @(Get-MtpItems ($device.GetFolder()) | Where-Object {{ $_.IsFolder }})
if ($volumes.Count -ne 1) {{ throw "mtp_storage_volume_ambiguous:$($volumes.Count)" }}
$storage = $volumes[0]
$roots = @()
foreach ($rootName in @({",".join(json.dumps(item) for item in KNOWN_PUBLIC_ROOTS)})) {{
  $item = $storage.GetFolder().ParseName($rootName)
  if ($null -ne $item -and $item.IsFolder) {{ $roots += [string]$item.Name }}
}}
[pscustomobject]@{{
  schema = '{SCHEMA}.snapshot'
  device_name = [string]$device.Name
  storage_name = [string]$storage.Name
  known_public_roots = $roots
  known_public_root_count = $roots.Count
  top_level_enumerated = $false
  writes_device = $false
  writes_files = $false
}} | ConvertTo-Json -Compress -Depth 4
"""


def run_snapshot(device_name: str, *, timeout: int = 45) -> dict[str, Any]:
    if not device_name.strip():
        return {"schema": f"{SCHEMA}.snapshot", "ok": False, "reason": "device_name_required"}
    command = [windows_powershell(), *encoded_command_arguments(snapshot_script(device_name))]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=max(1, timeout), check=False)
    except subprocess.TimeoutExpired:
        return {
            "schema": f"{SCHEMA}.snapshot",
            "ok": False,
            "reason": "mtp_snapshot_timeout",
            "timeout_seconds": max(1, timeout),
        }
    except OSError as exc:
        return {"schema": f"{SCHEMA}.snapshot", "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        return {"schema": f"{SCHEMA}.snapshot", "ok": False, "reason": "mtp_snapshot_failed", "detail": stderr[:2000]}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"schema": f"{SCHEMA}.snapshot", "ok": False, "reason": "mtp_snapshot_invalid_json", "detail": str(exc)}
    if not isinstance(payload, dict):
        return {"schema": f"{SCHEMA}.snapshot", "ok": False, "reason": "mtp_snapshot_invalid_root"}
    payload["ok"] = True
    payload["generated_at"] = now_iso()
    return payload


def normalize_public_root(value: str) -> str:
    candidate = value.replace("\\", "/").strip("/")
    if candidate not in PUBLIC_WECHAT_ROOTS:
        raise ValueError("source_root_not_allowlisted")
    return candidate


def archive_plan(device_name: str, source_root: str, destination_root: str) -> dict[str, Any]:
    try:
        allowed_root = normalize_public_root(source_root)
    except ValueError as exc:
        return {"schema": f"{SCHEMA}.archive_plan", "ok": False, "reason": str(exc)}
    destination = PureWindowsPath(destination_root)
    if not destination.is_absolute() or any(part in {".", ".."} for part in destination.parts):
        return {"schema": f"{SCHEMA}.archive_plan", "ok": False, "reason": "destination_must_be_absolute_without_traversal"}
    return {
        "schema": f"{SCHEMA}.archive_plan",
        "ok": True,
        "generated_at": now_iso(),
        "device_name": device_name,
        "source_root": allowed_root,
        "destination_root": str(destination),
        "required_preconditions": [
            "MTP snapshot identifies the exact device and its storage volume",
            "destination Unicode parent chain is read back before copying",
            "a source manifest records relative path and byte size",
            "copy acceptance requires equal source and destination file counts and bytes",
        ],
        "wechat_boundary": {
            "public_media_archive": True,
            "wechat_chat_history_restore": False,
            "private_database_access": False,
        },
        "writes_files": False,
        "writes_device": False,
        "apply_available": False,
    }


def video_archive_plan(device_name: str, destination_root: str) -> dict[str, Any]:
    """Plan a video archive without activating an unsafe copy backend."""
    destination = PureWindowsPath(destination_root)
    if not device_name.strip():
        return {"schema": VIDEO_ARCHIVE_SCHEMA, "ok": False, "reason": "device_name_required"}
    if not destination.is_absolute() or any(part in {".", ".."} for part in destination.parts):
        return {"schema": VIDEO_ARCHIVE_SCHEMA, "ok": False, "reason": "destination_must_be_absolute_without_traversal"}
    parts = tuple(part.casefold() for part in destination.parts)
    required = ("desktop", "codex资源库", "视频")
    if not all(part in parts for part in required):
        return {"schema": VIDEO_ARCHIVE_SCHEMA, "ok": False, "reason": "destination_not_resource_library"}
    return {
        "schema": VIDEO_ARCHIVE_SCHEMA,
        "ok": True,
        "generated_at": now_iso(),
        "device_name": device_name,
        "destination_root": str(destination),
        "source_roots": list(VIDEO_PUBLIC_ROOTS),
        "extensions": list(VIDEO_EXTENSIONS),
        "source_read_only": True,
        "overwrite": False,
        "apply_available": False,
        "blocked_backend_reason": VIDEO_ARCHIVE_BLOCKED_REASON,
        "required_backend": "bounded Windows Portable Devices API owner with cancellation, progress, atomic temporary files, and byte-count receipts",
        "required_acceptance": [
            "backend_result_has_no_failed_or_conflict_records",
            "every_verified_destination_size_equals_source_size",
        ],
    }


def validate() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": True,
        "allowed_public_roots": list(PUBLIC_WECHAT_ROOTS),
        "writes_files": False,
        "writes_device": False,
        "wechat_chat_history_restore": False,
        "video_archive_apply_available": False,
        "video_archive_blocked_backend_reason": VIDEO_ARCHIVE_BLOCKED_REASON,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only MTP media archive owner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--device-name", required=True)
    plan = subparsers.add_parser("archive-plan")
    plan.add_argument("--device-name", required=True)
    plan.add_argument("--source-root", required=True)
    plan.add_argument("--destination-root", required=True)
    video = subparsers.add_parser("video-archive-plan")
    video.add_argument("--device-name", required=True)
    video.add_argument("--destination-root", required=True)
    subparsers.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = run_snapshot(args.device_name)
    elif args.command == "archive-plan":
        payload = archive_plan(args.device_name, args.source_root, args.destination_root)
    elif args.command == "video-archive-plan":
        payload = video_archive_plan(args.device_name, args.destination_root)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
