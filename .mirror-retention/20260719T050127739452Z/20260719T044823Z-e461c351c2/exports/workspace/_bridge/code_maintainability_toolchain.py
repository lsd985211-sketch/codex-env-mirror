#!/usr/bin/env python3
"""Developer toolchain probes for code maintainability governance.

Ownership: inspect local developer CLI availability used by
`code_maintainability.py` validation and reporting.
Non-goals: scanning project code, choosing refactor targets, installing tools,
or changing PATH, package managers, or system configuration.
State behavior: read-only subprocess probes with hidden Windows process flags.
Caller context: `code_maintainability.py toolchain|snapshot|validate`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from shared.json_cli import now_iso

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SEARCH_NOISE_MARKERS = (
    "_bridge/mobile_openclaw_bridge/runtime/dashboard-browser-profile/",
    "_bridge\\mobile_openclaw_bridge\\runtime\\dashboard-browser-profile\\",
    "/Default/Extensions/",
    "\\Default\\Extensions\\",
    "/node_modules/",
    "\\node_modules\\",
    "/.cache/",
    "\\.cache\\",
    "/.pytest_cache/",
    "\\.pytest_cache\\",
    "/_bridge/runtime/",
    "\\_bridge\\runtime\\",
)


DEV_TOOLCHAIN = {
    "rg": {
        "command": ["rg", "--version"],
        "required": True,
        "use": "broad_text_search_with_exclusions",
    },
    "fd": {
        "command": ["fd", "--version"],
        "required": False,
        "use": "fast_file_discovery_when_clearer_than_rg_files",
    },
    "uv": {
        "command": ["uv", "--version"],
        "required": True,
        "use": "stable_python_tool_and_environment_execution",
    },
    "uvx": {
        "command": ["uvx", "--version"],
        "required": True,
        "use": "one_shot_python_cli_tool_execution",
    },
    "ruff": {
        "command": ["ruff", "--version"],
        "required": True,
        "use": "targeted_python_lint_feedback_without_broad_format_churn",
    },
}


def hidden_subprocess_options() -> dict[str, Any]:
    startupinfo = None
    creationflags = 0
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"startupinfo": startupinfo, "creationflags": creationflags}


def run_version_command(name: str, command: list[str]) -> dict[str, Any]:
    path = shutil.which(command[0])
    if not path:
        return {
            "name": name,
            "ok": False,
            "available": False,
            "path": "",
            "version": "",
            "error": "not_on_path",
        }
    run_command = command
    if sys.platform.startswith("win") and Path(path).suffix.lower() in {".cmd", ".bat"}:
        run_command = ["cmd.exe", "/d", "/c", *command]
    try:
        completed = subprocess.run(
            run_command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **hidden_subprocess_options(),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic surface must report boundary failures.
        return {
            "name": name,
            "ok": False,
            "available": True,
            "path": path,
            "version": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    return {
        "name": name,
        "ok": completed.returncode == 0,
        "available": True,
        "path": path,
        "version": output[0] if output else "",
        "returncode": completed.returncode,
        "error": "" if completed.returncode == 0 else (completed.stderr or completed.stdout or "").strip()[:400],
    }


def run_capture(command: list[str], *, timeout: int = 20) -> dict[str, Any]:
    path = shutil.which(command[0])
    if not path:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"{command[0]} not_on_path"}
    run_command = command
    if sys.platform.startswith("win") and Path(path).suffix.lower() in {".cmd", ".bat"}:
        run_command = ["cmd.exe", "/d", "/c", *command]
    try:
        completed = subprocess.run(
            run_command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **hidden_subprocess_options(),
        )
    except Exception as exc:  # noqa: BLE001 - validation must report environment failures.
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode in {0, 1},
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def search_hygiene_snapshot() -> dict[str, Any]:
    """Check default search excludes generated runtime/cache noise but remains opt-in scannable."""
    default = run_capture(["rg", "--files"], timeout=30)
    default_paths = [line.strip() for line in default.get("stdout", "").splitlines() if line.strip()]
    noisy_default = [
        path
        for path in default_paths
        if any(marker.lower() in path.replace("\\", "/").lower() or marker.lower() in path.lower() for marker in DEFAULT_SEARCH_NOISE_MARKERS)
    ][:20]
    explicit_target = "_bridge/mobile_openclaw_bridge/runtime/dashboard-browser-profile"
    explicit = run_capture(["rg", "-u", "--files", explicit_target], timeout=30)
    explicit_paths = [line.strip() for line in explicit.get("stdout", "").splitlines() if line.strip()]
    return {
        "schema": "code_maintainability.search_hygiene.v1",
        "ok": bool(default.get("ok")) and not noisy_default and bool(explicit.get("ok")),
        "default_search": {
            "ok": bool(default.get("ok")),
            "path_count": len(default_paths),
            "noisy_path_count": len(noisy_default),
            "noisy_examples": noisy_default,
        },
        "explicit_runtime_scan": {
            "ok": bool(explicit.get("ok")),
            "target": explicit_target,
            "path_count": len(explicit_paths),
            "sample": explicit_paths[:5],
            "command": f"rg -u --files {explicit_target}",
            "rule": "Runtime/cache/browser-profile paths are excluded from default search but can be scanned explicitly with -u/--no-ignore and a target path.",
        },
    }


def developer_toolchain_snapshot() -> dict[str, Any]:
    tools = []
    for name, spec in DEV_TOOLCHAIN.items():
        item = run_version_command(name, spec["command"])
        item["required"] = bool(spec["required"])
        item["use"] = spec["use"]
        tools.append(item)
    missing_required = [item["name"] for item in tools if item["required"] and not item["ok"]]
    return {
        "schema": "code_maintainability.developer_toolchain.v1",
        "ok": not missing_required,
        "generated_at": now_iso(),
        "tools": tools,
        "search_hygiene": search_hygiene_snapshot(),
        "missing_required": missing_required,
        "usage_policy": {
            "rg": "use for broad searches before slower recursive scans; default search excludes runtime/cache/browser-profile noise via .ignore",
            "rg_runtime_opt_in": "when runtime/cache/browser-profile evidence is needed, use an explicit target plus -u/--no-ignore, for example rg -u --files _bridge/mobile_openclaw_bridge/runtime/dashboard-browser-profile",
            "fd": "use for file discovery when installed and simpler than rg --files",
            "uv": "use for reproducible Python tool/env execution when package management is needed",
            "uvx": "use for one-shot Python CLI tools without permanent installs",
            "ruff": "use ruff check on targeted Python files; avoid broad formatting unless explicitly approved",
        },
    }


def validate() -> dict[str, Any]:
    payload = developer_toolchain_snapshot()
    search_hygiene = payload.get("search_hygiene") if isinstance(payload.get("search_hygiene"), dict) else {}
    return {
        "schema": "code_maintainability_toolchain.validate.v1",
        "ok": bool(payload.get("ok")) and bool(payload.get("tools")) and bool(search_hygiene.get("ok")),
        "tool_count": len(payload.get("tools") or []),
        "search_hygiene_ok": bool(search_hygiene.get("ok")),
        "search_hygiene": search_hygiene,
        "writes_files": False,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
