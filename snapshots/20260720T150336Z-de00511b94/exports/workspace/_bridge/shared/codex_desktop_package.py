#!/usr/bin/env python3
"""Resolve and identify the installed Codex Desktop MSIX host.

Ownership: read the installed OpenAI.Codex package identity, parse its declared
Desktop executable, and classify Windows processes that belong to that host.
Non-goals: launch or stop Codex, alter package files, inspect browser profile
contents, or conflate the Desktop host with ``app/resources/codex.exe``.
State behavior: read-only; package and process information is queried live.
Caller context: startup diagnostics, model runtime, session governance,
Defender governance, and mobile maintenance probes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


PACKAGE_NAME = "OpenAI.Codex"
APPLICATION_ID = "App"
DESKTOP_HOST_EXECUTABLE_NAMES = frozenset({"chatgpt.exe", "codex.exe"})
LEGACY_DESKTOP_RELATIVE_PATHS = (Path("app/ChatGPT.exe"), Path("app/Codex.exe"))
CLI_RELATIVE_SUFFIX = ("app", "resources", "codex.exe")
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


@dataclass(frozen=True)
class CodexDesktopPackage:
    install_location: Path
    package_family_name: str
    version: str
    application_id: str
    executable_relative_path: Path
    executable_path: Path
    source: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("install_location", "executable_relative_path", "executable_path"):
            value[key] = str(value[key])
        return value


def _powershell_json(script: str, *, timeout: int = 15) -> Any:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def parse_manifest_entrypoint(
    manifest_path: Path,
    *,
    application_id: str = APPLICATION_ID,
) -> tuple[str, Path] | None:
    """Return ``(application_id, executable_relative_path)`` from a Manifest."""
    try:
        root = ET.parse(manifest_path).getroot()
    except (OSError, ET.ParseError):
        return None

    applications: list[ET.Element] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "Application":
            applications.append(element)
    preferred = next((item for item in applications if item.attrib.get("Id") == application_id), None)
    selected = preferred if preferred is not None else (applications[0] if applications else None)
    if selected is None:
        return None
    executable = str(selected.attrib.get("Executable") or "").strip().replace("\\", "/")
    if not executable:
        return None
    return str(selected.attrib.get("Id") or application_id), Path(executable)


def resolve_entrypoint_from_install_location(install_location: Path) -> tuple[str, Path, str] | None:
    manifest = install_location / "AppxManifest.xml"
    parsed = parse_manifest_entrypoint(manifest)
    if parsed is not None:
        app_id, relative = parsed
        candidate = install_location / relative
        if candidate.is_file():
            return app_id, relative, "manifest"

    for relative in LEGACY_DESKTOP_RELATIVE_PATHS:
        if (install_location / relative).is_file():
            return APPLICATION_ID, relative, "fallback"
    return None


def _installed_package_rows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
@(Get-AppxPackage -Name 'OpenAI.Codex' |
  Sort-Object -Property @{ Expression = { [version]$_.Version }; Descending = $true } |
  Select-Object InstallLocation,PackageFamilyName,Version) | ConvertTo-Json -Depth 3 -Compress
"""
    raw = _powershell_json(script)
    if isinstance(raw, dict):
        return [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def resolve_installed_package() -> CodexDesktopPackage | None:
    for row in _installed_package_rows():
        install_location = Path(str(row.get("InstallLocation") or ""))
        if not install_location.is_dir():
            continue
        resolved = resolve_entrypoint_from_install_location(install_location)
        if resolved is None:
            continue
        application_id, relative, source = resolved
        return CodexDesktopPackage(
            install_location=install_location,
            package_family_name=str(row.get("PackageFamilyName") or ""),
            version=str(row.get("Version") or ""),
            application_id=application_id,
            executable_relative_path=relative,
            executable_path=install_location / relative,
            source=source,
        )
    return None


def _normalized_parts(value: str | Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(str(value)).parts)


def is_codex_cli_path(value: str | Path) -> bool:
    parts = _normalized_parts(value)
    return len(parts) >= len(CLI_RELATIVE_SUFFIX) and parts[-3:] == CLI_RELATIVE_SUFFIX


def is_desktop_host_path(value: str | Path) -> bool:
    text = str(value or "").strip()
    if not text or is_codex_cli_path(text):
        return False
    path = Path(text)
    if path.name.casefold() not in DESKTOP_HOST_EXECUTABLE_NAMES:
        return False
    normalized = text.replace("/", "\\").casefold()
    return "\\openai.codex_" in normalized and "\\app\\" in normalized


def is_desktop_host_process(
    *,
    name: str,
    executable_path: str = "",
    command_line: str = "",
    main_only: bool = False,
) -> bool:
    if str(name or "").casefold() not in DESKTOP_HOST_EXECUTABLE_NAMES:
        return False
    if executable_path and not is_desktop_host_path(executable_path):
        return False
    if not executable_path:
        normalized = str(command_line or "").replace("/", "\\").casefold()
        if "\\openai.codex_" not in normalized or "\\app\\" not in normalized:
            return False
        if "\\app\\resources\\codex.exe" in normalized:
            return False
    return not main_only or "--type=" not in str(command_line or "").casefold()


def query_desktop_host_processes(*, main_only: bool = False) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = r"""
@(Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('ChatGPT.exe','Codex.exe') } |
  Select-Object ProcessId,Name,ExecutablePath,CommandLine,CreationDate) | ConvertTo-Json -Depth 3 -Compress
"""
    raw = _powershell_json(script)
    rows: Iterable[Any] = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and is_desktop_host_process(
            name=str(row.get("Name") or ""),
            executable_path=str(row.get("ExecutablePath") or ""),
            command_line=str(row.get("CommandLine") or ""),
            main_only=main_only,
        )
    ]


def query_codex_cli_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = r"""
@(Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -ieq 'codex.exe' -and (
      $_.ExecutablePath -like '*\OpenAI.Codex_*\app\resources\codex.exe' -or
      $_.CommandLine -like '*\OpenAI.Codex_*\app\resources\codex.exe*'
    )
  } |
  Select-Object ProcessId,Name,ExecutablePath,CommandLine,CreationDate) | ConvertTo-Json -Depth 3 -Compress
"""
    raw = _powershell_json(script)
    rows: Iterable[Any] = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    return [row for row in rows if isinstance(row, dict)]


def codex_process_family_running() -> bool:
    return bool(query_desktop_host_processes() or query_codex_cli_processes())


def running_desktop_executable_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for row in query_desktop_host_processes():
        value = str(row.get("ExecutablePath") or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            paths.append(Path(value))
    return paths


__all__ = [
    "APPLICATION_ID",
    "CodexDesktopPackage",
    "DESKTOP_HOST_EXECUTABLE_NAMES",
    "is_codex_cli_path",
    "is_desktop_host_path",
    "is_desktop_host_process",
    "parse_manifest_entrypoint",
    "query_codex_cli_processes",
    "query_desktop_host_processes",
    "resolve_entrypoint_from_install_location",
    "resolve_installed_package",
    "running_desktop_executable_paths",
    "codex_process_family_running",
]
