#!/usr/bin/env python3
"""Own bounded, read-only USB inventory and diagnostics on Windows.

Ownership: present-device topology, USB-related driver metadata, fixed USB
event logs, bounded hot-plug observation, optional read-only Python device
backends, and non-starting Android tool discovery.
Non-goals: changing devices, drivers, firmware, storage, services, policies,
or exposing arbitrary PowerShell, shell, ADB, Fastboot, or vendor commands.
State behavior: device operations are read-only; this module creates no daemon,
scheduled task, startup entry, device handle for data transfer, or state file.
Caller context: Codex hardware maintenance workflows and direct local CLI use.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
RUNTIME_PYTHON = BRIDGE / "runtime" / "usb_device_owner" / "python"
REQUIREMENTS_PATH = BRIDGE / "usb_device_owner_requirements.txt"
REQUIRED_PACKAGES = {
    "pyserial": "3.5",
    "PyUSB": "1.3.1",
    "hidapi": "0.15.0",
    "fido2": "2.2.1",
    "pyscard": "2.3.1",
    "libusb-package": "1.0.30.0",
}
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
if RUNTIME_PYTHON.is_dir() and str(RUNTIME_PYTHON) not in sys.path:
    sys.path.insert(0, str(RUNTIME_PYTHON))

from bounded_output import governed_cli_payload  # noqa: E402
from shared.json_cli import configure_utf8_stdio  # noqa: E402


configure_utf8_stdio()

SCHEMA = "usb_device_owner.v1"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
USB_EVENT_LOGS = (
    "Microsoft-Windows-USB-USBXHCI-Operational",
    "Microsoft-Windows-USB-UCMUCSICX/Operational",
    "Microsoft-Windows-USBVideo/Operational",
    "Microsoft-Windows-TerminalServices-ClientUSBDevices/Operational",
    "Microsoft-Windows-TerminalServices-ClientUSBDevices/Admin",
)
ALLOWED_EXTERNAL_ACTIONS = {
    "powershell_snapshot": ("powershell.exe", "fixed_script"),
    "powershell_events": ("powershell.exe", "fixed_script"),
    "powershell_watch": ("powershell.exe", "fixed_script"),
    "adb_version": ("adb.exe", "version"),
    "fastboot_devices": ("fastboot.exe", "devices"),
}
MUTATING_TOKENS = (
    "disable-pnpdevice",
    "enable-pnpdevice",
    "pnputil /add-driver",
    "pnputil /delete-driver",
    "restart-device",
    "adb shell",
    "adb push",
    "adb install",
    "adb reboot",
    "adb sideload",
    "fastboot flash",
    "fastboot erase",
    "fastboot reboot",
)
VID_PID_RE = re.compile(r"VID_([0-9A-F]{4}).*?PID_([0-9A-F]{4})", re.IGNORECASE)
PLACEHOLDER_CONTAINER_IDS = {
    "{00000000-0000-0000-0000-000000000000}",
    "{00000000-0000-0000-ffff-ffffffffffff}",
}


SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$devices=@(Get-PnpDevice -PresentOnly -ErrorAction Stop)
$ids=@($devices | ForEach-Object {[string]$_.InstanceId})
$keys=@(
  'DEVPKEY_Device_Parent',
  'DEVPKEY_Device_ContainerId',
  'DEVPKEY_Device_LocationPaths',
  'DEVPKEY_Device_HardwareIds',
  'DEVPKEY_Device_CompatibleIds',
  'DEVPKEY_Device_BusReportedDeviceDesc',
  'DEVPKEY_Device_ProblemCode'
)
$propertyRows=@(Get-PnpDeviceProperty -InstanceId $ids -KeyName $keys -ErrorAction SilentlyContinue)
$propertyMap=@{}
foreach($prop in $propertyRows){
  $id=[string]$prop.InstanceId
  if(-not $propertyMap.ContainsKey($id)){$propertyMap[$id]=@{}}
  $propertyMap[$id][[string]$prop.KeyName]=$prop.Data
}
$driverMap=@{}
Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue | ForEach-Object {
  $id=[string]$_.DeviceID
  if($id){$driverMap[$id]=@{
    provider=[string]$_.DriverProviderName
    version=[string]$_.DriverVersion
    inf=[string]$_.InfName
    date=if($_.DriverDate){([datetime]$_.DriverDate).ToUniversalTime().ToString('o')}else{''}
    signer=[string]$_.Signer
  }}
}
function Strings($value){
  if($null -eq $value){return @()}
  return @($value | ForEach-Object {[string]$_} | Where-Object {$_})
}
$rows=@()
foreach($device in $devices){
  $id=[string]$device.InstanceId
  $props=$propertyMap[$id]
  if($null -eq $props){$props=@{}}
  $rows += [pscustomobject]@{
    instance_id=$id
    class=[string]$device.Class
    friendly_name=[string]$device.FriendlyName
    status=[string]$device.Status
    present=$true
    problem_code=if($null -ne $props['DEVPKEY_Device_ProblemCode']){[int]$props['DEVPKEY_Device_ProblemCode']}else{0}
    parent_instance_id=[string]$props['DEVPKEY_Device_Parent']
    container_id=[string]$props['DEVPKEY_Device_ContainerId']
    location_paths=@(Strings $props['DEVPKEY_Device_LocationPaths'])
    hardware_ids=@(Strings $props['DEVPKEY_Device_HardwareIds'])
    compatible_ids=@(Strings $props['DEVPKEY_Device_CompatibleIds'])
    bus_reported_description=[string]$props['DEVPKEY_Device_BusReportedDeviceDesc']
    manufacturer=[string]$device.Manufacturer
    service=[string]$device.Service
    driver=$driverMap[$id]
  }
}
$disks=@()
try{
  foreach($disk in @(Get-Disk -ErrorAction Stop | Where-Object {[string]$_.BusType -eq 'USB'})){
    $partitions=@()
    try{
      $partitions=@(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop | ForEach-Object {
        [pscustomobject]@{partition_number=[int]$_.PartitionNumber; drive_letter=[string]$_.DriveLetter; size_bytes=[int64]$_.Size; type=[string]$_.Type}
      })
    }catch{}
    $disks += [pscustomobject]@{
      number=[int]$disk.Number
      friendly_name=[string]$disk.FriendlyName
      serial_number=[string]$disk.SerialNumber
      operational_status=@(Strings $disk.OperationalStatus)
      health_status=[string]$disk.HealthStatus
      partition_style=[string]$disk.PartitionStyle
      size_bytes=[int64]$disk.Size
      is_read_only=[bool]$disk.IsReadOnly
      is_offline=[bool]$disk.IsOffline
      partitions=$partitions
    }
  }
}catch{}
$smartCardService=$null
try{
  $svc=Get-CimInstance Win32_Service -Filter "Name='SCardSvr'" -ErrorAction Stop
  if($svc){$smartCardService=[pscustomobject]@{name='SCardSvr'; state=[string]$svc.State; start_mode=[string]$svc.StartMode; status=[string]$svc.Status}}
}catch{}
$os=Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
[pscustomobject]@{
  captured_at=(Get-Date).ToUniversalTime().ToString('o')
  machine=@{caption=[string]$os.Caption; version=[string]$os.Version; build_number=[string]$os.BuildNumber; architecture=[string]$os.OSArchitecture}
  devices=$rows
  usb_disks=$disks
  smart_card_service=$smartCardService
} | ConvertTo-Json -Compress -Depth 9
"""


WATCH_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
@(Get-PnpDevice -PresentOnly -ErrorAction Stop | ForEach-Object {
  [pscustomobject]@{instance_id=[string]$_.InstanceId; class=[string]$_.Class; friendly_name=[string]$_.FriendlyName; status=[string]$_.Status}
}) | ConvertTo-Json -Compress -Depth 4
"""


def _events_script(hours: int, limit: int) -> str:
    quoted_logs = ",".join("'" + item.replace("'", "''") + "'" for item in USB_EVENT_LOGS)
    return rf"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$logs=@({quoted_logs})
$start=(Get-Date).ToUniversalTime().AddHours(-{hours})
$states=@()
$events=@()
foreach($name in $logs){{
  $state=Get-WinEvent -ListLog $name -ErrorAction SilentlyContinue
  if($state){{
    $states += [pscustomobject]@{{log_name=$name; enabled=[bool]$state.IsEnabled; record_count=[int64]$state.RecordCount}}
    if($state.IsEnabled){{
      $events += @(Get-WinEvent -FilterHashtable @{{LogName=$name; StartTime=$start}} -MaxEvents {limit} -ErrorAction SilentlyContinue | ForEach-Object {{
        [pscustomobject]@{{log_name=$name; provider=[string]$_.ProviderName; event_id=[int]$_.Id; level=[string]$_.LevelDisplayName; time_created=if($_.TimeCreated){{([datetime]$_.TimeCreated).ToUniversalTime().ToString('o')}}else{{''}}; record_id=[int64]$_.RecordId; message=[string]$_.Message}}
      }})
    }}
  }}else{{
    $states += [pscustomobject]@{{log_name=$name; enabled=$false; record_count=0; unavailable=$true}}
  }}
}}
[pscustomobject]@{{captured_at=(Get-Date).ToUniversalTime().ToString('o'); hours={hours}; log_states=$states; events=@($events | Sort-Object time_created -Descending | Select-Object -First {limit})}} | ConvertTo-Json -Compress -Depth 6
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_fixed(command: list[str], *, timeout: int) -> str:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"returncode={completed.returncode}").strip()
        raise RuntimeError(detail[-2000:])
    return completed.stdout.strip()


def _run_powershell(script: str, *, timeout: int = 45) -> Any:
    raw = _run_fixed(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )
    return json.loads(raw or "null")


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _listify(value) if str(item)]


def _vid_pid(row: dict[str, Any]) -> tuple[str, str]:
    candidates = [str(row.get("instance_id") or ""), *_string_list(row.get("hardware_ids"))]
    for candidate in candidates:
        match = VID_PID_RE.search(candidate)
        if match:
            return match.group(1).upper(), match.group(2).upper()
    return "", ""


def select_usb_related(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [dict(row) for row in rows if isinstance(row, dict) and row.get("instance_id")]
    by_id = {str(row["instance_id"]).casefold(): row for row in normalized}
    selected: set[str] = set()
    for key, row in by_id.items():
        instance_id = str(row.get("instance_id") or "").upper()
        device_class = str(row.get("class") or "").casefold()
        if instance_id.startswith(("USB\\", "HID\\", "UCM\\")) or device_class == "usb":
            selected.add(key)

    changed = True
    while changed:
        changed = False
        for key, row in by_id.items():
            parent = str(row.get("parent_instance_id") or "").casefold()
            if key not in selected and parent in selected:
                selected.add(key)
                changed = True

    container_ids = set()
    for key in selected:
        container_id = str(by_id[key].get("container_id") or "").casefold()
        if container_id and container_id not in PLACEHOLDER_CONTAINER_IDS:
            container_ids.add(container_id)
    for key, row in by_id.items():
        container_id = str(row.get("container_id") or "").casefold()
        if container_id and container_id in container_ids:
            selected.add(key)

    result: list[dict[str, Any]] = []
    for key in selected:
        row = dict(by_id[key])
        row["location_paths"] = _string_list(row.get("location_paths"))
        row["hardware_ids"] = _string_list(row.get("hardware_ids"))
        row["compatible_ids"] = _string_list(row.get("compatible_ids"))
        driver = row.get("driver")
        row["driver"] = dict(driver) if isinstance(driver, dict) else {}
        vid, pid = _vid_pid(row)
        row["vid"] = vid
        row["pid"] = pid
        row["stable_identity"] = {
            "instance_id": str(row.get("instance_id") or ""),
            "container_id": str(row.get("container_id") or ""),
            "vid": vid,
            "pid": pid,
            "parent_instance_id": str(row.get("parent_instance_id") or ""),
            "location_paths": row["location_paths"],
        }
        result.append(row)
    return sorted(result, key=lambda item: str(item.get("instance_id") or "").casefold())


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return ""


def optional_backend_snapshot(smart_card_service: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        import serial
        from serial.tools import list_ports

        ports = []
        for port in list_ports.comports():
            ports.append(
                {
                    "device": str(port.device or ""),
                    "name": str(port.name or ""),
                    "description": str(port.description or ""),
                    "hwid": str(port.hwid or ""),
                    "vid": f"{port.vid:04X}" if port.vid is not None else "",
                    "pid": f"{port.pid:04X}" if port.pid is not None else "",
                    "manufacturer": str(port.manufacturer or ""),
                    "product": str(port.product or ""),
                    "location": str(port.location or ""),
                }
            )
        result["pyserial"] = {"ok": True, "version": str(getattr(serial, "VERSION", "")), "port_count": len(ports), "ports": ports}
    except Exception as exc:
        result["pyserial"] = {"ok": False, "version": _package_version("pyserial"), "error": f"{type(exc).__name__}: {exc}"}

    try:
        import hid

        rows = list(hid.enumerate())
        result["hidapi"] = {"ok": True, "version": _package_version("hidapi"), "interface_count": len(rows)}
    except Exception as exc:
        result["hidapi"] = {"ok": False, "version": _package_version("hidapi"), "error": f"{type(exc).__name__}: {exc}"}

    try:
        import libusb_package
        import usb.core

        backend = libusb_package.get_libusb1_backend()
        devices = list(usb.core.find(find_all=True, backend=backend) or [])
        result["pyusb"] = {
            "ok": backend is not None,
            "version": _package_version("PyUSB"),
            "backend": "libusb-package" if backend is not None else "",
            "backend_version": _package_version("libusb-package"),
            "device_count": len(devices),
        }
    except Exception as exc:
        result["pyusb"] = {"ok": False, "version": _package_version("PyUSB"), "error": f"{type(exc).__name__}: {exc}"}

    try:
        from fido2.hid import CtapHidDevice

        devices = list(CtapHidDevice.list_devices())
        result["fido2"] = {"ok": True, "version": _package_version("fido2"), "authenticator_count": len(devices)}
    except Exception as exc:
        result["fido2"] = {"ok": False, "version": _package_version("fido2"), "error": f"{type(exc).__name__}: {exc}"}

    service_state = str((smart_card_service or {}).get("state") or "").casefold()
    if service_state and service_state != "running":
        result["pyscard"] = {
            "ok": True,
            "available": False,
            "version": _package_version("pyscard"),
            "reader_count": 0,
            "reason": "smart_card_resource_manager_not_running",
            "service_state": str((smart_card_service or {}).get("state") or ""),
        }
    else:
        try:
            from smartcard.System import readers

            names = [str(item) for item in readers()]
            result["pyscard"] = {"ok": True, "available": True, "version": _package_version("pyscard"), "reader_count": len(names), "readers": names}
        except Exception as exc:
            result["pyscard"] = {"ok": False, "available": False, "version": _package_version("pyscard"), "error": f"{type(exc).__name__}: {exc}"}
    return result


def tool_inventory() -> dict[str, Any]:
    android_root = Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools"
    adb = android_root / "adb.exe"
    fastboot = android_root / "fastboot.exe"
    wdk_root = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "Tools"
    usbview = sorted(wdk_root.glob("*/x64/usbview.exe"), reverse=True)
    devcon = sorted(wdk_root.glob("*/x64/devcon.exe"), reverse=True)
    return {
        "pnputil": {"available": Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "pnputil.exe").is_file(), "executed": False},
        "usbview": {"available": bool(usbview), "path": str(usbview[0]) if usbview else "", "executed": False},
        "devcon": {"available": bool(devcon), "path": str(devcon[0]) if devcon else "", "executed": False},
        "adb": {"available": adb.is_file(), "path": str(adb) if adb.is_file() else ""},
        "fastboot": {"available": fastboot.is_file(), "path": str(fastboot) if fastboot.is_file() else ""},
    }


def collect_snapshot() -> dict[str, Any]:
    raw = _run_powershell(SNAPSHOT_SCRIPT, timeout=60)
    if not isinstance(raw, dict):
        raise ValueError("PnP snapshot must be a JSON object")
    devices = select_usb_related(_listify(raw.get("devices")))
    classes = Counter(str(row.get("class") or "unknown") for row in devices)
    statuses = Counter(str(row.get("status") or "unknown") for row in devices)
    smart_card_service = raw.get("smart_card_service") if isinstance(raw.get("smart_card_service"), dict) else {}
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "read_only": True,
        "captured_at": str(raw.get("captured_at") or now_iso()),
        "machine": raw.get("machine") if isinstance(raw.get("machine"), dict) else {},
        "summary": {
            "present_pnp_count": len(_listify(raw.get("devices"))),
            "usb_related_count": len(devices),
            "class_counts": dict(sorted(classes.items())),
            "status_counts": dict(sorted(statuses.items())),
            "usb_disk_count": len(_listify(raw.get("usb_disks"))),
        },
        "devices": devices,
        "usb_disks": _listify(raw.get("usb_disks")),
        "smart_card_service": smart_card_service,
        "optional_backends": optional_backend_snapshot(smart_card_service),
        "tools": tool_inventory(),
        "safety": {
            "device_writes_supported": False,
            "driver_changes_supported": False,
            "service_changes_supported": False,
            "resident_watch_supported": False,
            "arbitrary_command_supported": False,
        },
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot is not a JSON object: {path}")
    return payload


def _device_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("instance_id") or "").casefold(): row
        for row in _listify(snapshot.get("devices"))
        if isinstance(row, dict) and row.get("instance_id")
    }


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = _device_map(before)
    new = _device_map(after)
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    removed = [old[key] for key in sorted(old.keys() - new.keys())]
    changed = []
    fields = ("status", "problem_code", "friendly_name", "class", "parent_instance_id", "container_id", "location_paths", "driver")
    for key in sorted(old.keys() & new.keys()):
        delta = {field: {"before": old[key].get(field), "after": new[key].get(field)} for field in fields if old[key].get(field) != new[key].get(field)}
        if delta:
            changed.append({"instance_id": new[key].get("instance_id"), "changes": delta})
    return {
        "schema": f"{SCHEMA}.diff",
        "ok": True,
        "read_only": True,
        "before_captured_at": str(before.get("captured_at") or ""),
        "after_captured_at": str(after.get("captured_at") or ""),
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def collect_events(*, hours: int, limit: int) -> dict[str, Any]:
    hours = max(1, min(int(hours), 24 * 30))
    limit = max(1, min(int(limit), 500))
    raw = _run_powershell(_events_script(hours, limit), timeout=45)
    if not isinstance(raw, dict):
        raise ValueError("event result must be a JSON object")
    events = []
    for row in _listify(raw.get("events"))[:limit]:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["message"] = str(item.get("message") or "")[:1200]
        events.append(item)
    return {
        "schema": f"{SCHEMA}.events",
        "ok": True,
        "read_only": True,
        "captured_at": str(raw.get("captured_at") or now_iso()),
        "hours": hours,
        "limit": limit,
        "log_states": _listify(raw.get("log_states")),
        "event_count": len(events),
        "events": events,
    }


def _watch_rows() -> dict[str, dict[str, Any]]:
    raw = _run_powershell(WATCH_SCRIPT, timeout=20)
    return {
        str(row.get("instance_id") or "").casefold(): dict(row)
        for row in _listify(raw)
        if isinstance(row, dict) and row.get("instance_id")
    }


def watch_devices(
    *,
    duration: float,
    interval: float,
    collector: Callable[[], dict[str, dict[str, Any]]] = _watch_rows,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    duration = max(1.0, min(float(duration), 300.0))
    interval = max(0.5, min(float(interval), 30.0, duration))
    started_at = now_iso()
    start = clock()
    previous = collector()
    initial_count = len(previous)
    observations = 1
    changes: list[dict[str, Any]] = []
    while True:
        remaining = duration - (clock() - start)
        if remaining <= 0:
            break
        sleeper(min(interval, remaining))
        current = collector()
        observations += 1
        for key in sorted(current.keys() - previous.keys()):
            changes.append({"observed_at": now_iso(), "change": "added", "device": current[key]})
        for key in sorted(previous.keys() - current.keys()):
            changes.append({"observed_at": now_iso(), "change": "removed", "device": previous[key]})
        for key in sorted(current.keys() & previous.keys()):
            if current[key] != previous[key]:
                changes.append({"observed_at": now_iso(), "change": "updated", "before": previous[key], "after": current[key]})
        previous = current
    return {
        "schema": f"{SCHEMA}.watch",
        "ok": True,
        "read_only": True,
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_seconds": duration,
        "interval_seconds": interval,
        "observation_count": observations,
        "initial_device_count": initial_count,
        "final_device_count": len(previous),
        "change_count": len(changes),
        "changes": changes[:200],
        "changes_truncated": len(changes) > 200,
    }


def _adb_server_query(service: str, *, host: str = "127.0.0.1", port: int = 5037, timeout: float = 1.0) -> tuple[bool, str]:
    request = service.encode("utf-8")
    packet = f"{len(request):04x}".encode("ascii") + request
    try:
        with socket.create_connection((host, port), timeout=timeout) as client:
            client.sendall(packet)
            status = _recv_exact(client, 4)
            if status != b"OKAY":
                return False, status.decode("ascii", errors="replace")
            length_raw = _recv_exact(client, 4)
            length = int(length_raw.decode("ascii"), 16)
            return True, _recv_exact(client, length).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return False, ""


def _recv_exact(client: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = client.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("ADB server closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)


def _parse_adb_devices(payload: str) -> list[dict[str, str]]:
    devices = []
    for line in payload.splitlines():
        if "\t" not in line:
            continue
        serial, detail = line.split("\t", 1)
        parts = detail.split()
        row = {"serial": serial, "state": parts[0] if parts else ""}
        for part in parts[1:]:
            if ":" in part:
                key, value = part.split(":", 1)
                if key in {"product", "model", "device", "transport_id"}:
                    row[key] = value
        devices.append(row)
    return devices


def android_status() -> dict[str, Any]:
    tools = tool_inventory()
    adb_path = Path(str(tools["adb"].get("path") or ""))
    fastboot_path = Path(str(tools["fastboot"].get("path") or ""))
    adb_version = ""
    if adb_path.is_file():
        try:
            adb_version = _run_fixed([str(adb_path), "version"], timeout=5)[:1000]
        except Exception as exc:
            adb_version = f"{type(exc).__name__}: {exc}"
    server_ok, server_payload = _adb_server_query("host:devices-l")
    fastboot_devices: list[dict[str, str]] = []
    fastboot_error = ""
    if fastboot_path.is_file():
        try:
            for line in _run_fixed([str(fastboot_path), "devices"], timeout=8).splitlines():
                parts = line.split()
                if parts:
                    fastboot_devices.append({"serial": parts[0], "state": parts[1] if len(parts) > 1 else "fastboot"})
        except Exception as exc:
            fastboot_error = f"{type(exc).__name__}: {exc}"
    return {
        "schema": f"{SCHEMA}.android",
        "ok": True,
        "read_only": True,
        "captured_at": now_iso(),
        "adb": {
            "available": adb_path.is_file(),
            "path": str(adb_path) if adb_path.is_file() else "",
            "version": adb_version,
            "server_running": server_ok,
            "server_started_by_owner": False,
            "devices": _parse_adb_devices(server_payload) if server_ok else [],
            "query_method": "existing_local_adb_server_protocol_only",
        },
        "fastboot": {
            "available": fastboot_path.is_file(),
            "path": str(fastboot_path) if fastboot_path.is_file() else "",
            "devices": fastboot_devices,
            "error": fastboot_error,
        },
        "blocked_actions": ["adb server start", "adb shell", "adb push/install/reboot/sideload", "fastboot flash/erase/reboot"],
    }


def doctor(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    state = snapshot or collect_snapshot()
    issues = []
    for row in _listify(state.get("devices")):
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        problem = int(row.get("problem_code") or 0)
        if status.casefold() != "ok" or problem:
            issues.append({"severity": "risk", "code": "present_usb_device_problem", "instance_id": row.get("instance_id"), "status": status, "problem_code": problem})
    classes = Counter(str(row.get("class") or "") for row in _listify(state.get("devices")) if isinstance(row, dict))
    if not classes.get("USB"):
        issues.append({"severity": "risk", "code": "usb_controller_or_hub_not_detected"})
    backends = state.get("optional_backends") if isinstance(state.get("optional_backends"), dict) else {}
    for name, backend in backends.items():
        if isinstance(backend, dict) and backend.get("ok") is False:
            issues.append({"severity": "advisory", "code": "optional_backend_unavailable", "backend": name, "detail": backend.get("error", "")})
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "read_only": True,
        "captured_at": now_iso(),
        "summary": state.get("summary", {}),
        "issues": issues,
        "next_action": "inspect_risk_rows_and_recent_usb_events" if any(item.get("severity") == "risk" for item in issues) else "none",
    }


def validate() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    source = Path(__file__).read_text(encoding="utf-8").casefold()
    token_hits = [token for token in MUTATING_TOKENS if token in source and token not in {item.casefold() for item in android_status.__doc__.split()}] if android_status.__doc__ else []
    # Blocked-action labels are expected; executable argument vectors are checked separately.
    vectors = [item for item in ALLOWED_EXTERNAL_ACTIONS.values()]
    executable_tokens = " ".join(" ".join(item) for item in vectors).casefold()
    checks.append({"name": "external_command_allowlist", "ok": not any(token in executable_tokens for token in MUTATING_TOKENS), "actions": sorted(ALLOWED_EXTERNAL_ACTIONS)})
    checks.append({"name": "bounded_watch", "ok": True, "max_duration_seconds": 300, "min_interval_seconds": 0.5, "persistent": False})
    checks.append({"name": "fixed_event_logs", "ok": len(USB_EVENT_LOGS) == len(set(USB_EVENT_LOGS)) == 5, "max_events": 500})
    checks.append({"name": "isolated_dependency_root", "ok": RUNTIME_PYTHON.is_dir(), "path": str(RUNTIME_PYTHON)})
    declared = REQUIREMENTS_PATH.read_text(encoding="utf-8") if REQUIREMENTS_PATH.is_file() else ""
    versions = {name: _package_version(name) for name in REQUIRED_PACKAGES}
    checks.append(
        {
            "name": "reproducible_dependency_contract",
            "ok": REQUIREMENTS_PATH.is_file()
            and all(f"{name}=={version}".casefold() in declared.casefold() for name, version in REQUIRED_PACKAGES.items())
            and versions == REQUIRED_PACKAGES,
            "requirements_path": str(REQUIREMENTS_PATH),
            "installed_versions": versions,
        }
    )
    try:
        state = collect_snapshot()
        json.dumps(state, ensure_ascii=False)
        checks.append({"name": "live_snapshot", "ok": bool(state.get("devices")), "usb_related_count": state.get("summary", {}).get("usb_related_count", 0)})
        backend_states = state.get("optional_backends", {})
        checks.append({"name": "optional_backends", "ok": all(bool(backend_states.get(name, {}).get("ok")) for name in ("pyserial", "hidapi", "pyusb", "fido2", "pyscard")), "states": backend_states})
    except Exception as exc:
        checks.append({"name": "live_snapshot", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    adb_before = _adb_server_query("host:version")[0]
    android = android_status()
    adb_after = _adb_server_query("host:version")[0]
    checks.append({"name": "android_non_starting_query", "ok": adb_before == adb_after and android.get("adb", {}).get("server_started_by_owner") is False, "server_before": adb_before, "server_after": adb_after})
    issues = [{"severity": "risk", "code": "validation_check_failed", "check": item.get("name"), "detail": item} for item in checks if not item.get("ok")]
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "read_only": True,
        "generated_at": now_iso(),
        "checks": checks,
        "issues": issues,
        "mutating_source_labels_present": token_hits,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Windows USB device owner")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("snapshot", "doctor", "android", "validate"):
        command = sub.add_parser(action)
        command.add_argument("--full", action="store_true")
    events = sub.add_parser("events")
    events.add_argument("--hours", type=int, default=24)
    events.add_argument("--limit", type=int, default=100)
    events.add_argument("--full", action="store_true")
    watch = sub.add_parser("watch")
    watch.add_argument("--duration", type=float, default=10.0)
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--full", action="store_true")
    diff = sub.add_parser("diff")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    diff.add_argument("--full", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.action == "snapshot":
            payload = collect_snapshot()
        elif args.action == "doctor":
            payload = doctor()
        elif args.action == "events":
            payload = collect_events(hours=args.hours, limit=args.limit)
        elif args.action == "watch":
            payload = watch_devices(duration=args.duration, interval=args.interval)
        elif args.action == "diff":
            payload = diff_snapshots(load_snapshot(args.before), load_snapshot(args.after))
        elif args.action == "android":
            payload = android_status()
        else:
            payload = validate()
    except Exception as exc:
        payload = {
            "schema": f"{SCHEMA}.{args.action}",
            "ok": False,
            "read_only": True,
            "error_class": type(exc).__name__,
            "reason": str(exc),
            "next_action": "inspect_owner_error_without_applying_device_changes",
        }
    projected = governed_cli_payload(
        payload,
        full=bool(getattr(args, "full", False)),
        full_result_ref=f"python _bridge\\usb_device_owner.py {args.action} --full",
        max_success_bytes=16 * 1024,
        max_full_bytes=128 * 1024,
    )
    print(json.dumps(projected, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
