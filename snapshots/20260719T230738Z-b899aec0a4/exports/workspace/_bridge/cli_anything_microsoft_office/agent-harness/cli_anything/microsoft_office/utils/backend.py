"""Ownership: invoke the bounded PowerShell COM backend and parse JSON results.

Non-goals: expose arbitrary PowerShell, COM members, VBA, or shell execution.
State behavior: each call is an isolated subprocess and Office COM instance.
Caller context: core document operations and system status probes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("office_backend.ps1")


class OfficeBackendError(RuntimeError):
    """Raised when the PowerShell or Office COM backend reports failure."""


def _powershell() -> str:
    path = shutil.which("powershell.exe") or shutil.which("powershell")
    if not path:
        raise OfficeBackendError("Windows PowerShell is required for Microsoft Office COM automation")
    return path


def invoke(action: str, payload: dict[str, Any] | None = None, *, timeout: float = 120.0) -> dict[str, Any]:
    if not SCRIPT.is_file():
        raise OfficeBackendError(f"Office backend script is missing: {SCRIPT}")
    command = [
        _powershell(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-Action",
        action,
    ]
    completed = subprocess.run(
        command,
        input=json.dumps(payload or {}, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not stdout_lines:
        detail = completed.stderr.strip() or f"backend exited {completed.returncode} without JSON"
        raise OfficeBackendError(detail)
    try:
        result = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        raise OfficeBackendError(f"Invalid backend JSON: {stdout_lines[-1]}") from exc
    if completed.returncode != 0 or result.get("ok") is not True:
        error = result.get("error") or completed.stderr.strip() or f"backend action failed: {action}"
        raise OfficeBackendError(str(error))
    return result

