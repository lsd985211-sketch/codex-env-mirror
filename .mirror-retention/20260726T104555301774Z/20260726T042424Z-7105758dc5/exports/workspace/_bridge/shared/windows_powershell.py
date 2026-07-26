"""Safe PowerShell argument construction for WSL-to-Windows calls.

Ownership: encode fixed, owner-authored PowerShell source without relying on
the Windows PowerShell 5.1 source-file encoding default.
Non-goals: shell execution, script policy, command allowlisting, or transport.
"""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path


WINDOWS_POWERSHELL_WSL_PATH = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
WINDOWS_POWERSHELL_NATIVE_PATH = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")


def resolve_powershell_executable() -> str:
    """Resolve Windows PowerShell without depending on the caller's PATH."""
    configured = os.environ.get("CODEX_POWERSHELL", "").strip()
    if os.name == "nt":
        candidates = (
            configured,
            shutil.which("powershell.exe"),
            shutil.which("powershell"),
            str(WINDOWS_POWERSHELL_NATIVE_PATH),
            shutil.which("pwsh.exe"),
            shutil.which("pwsh"),
        )
    else:
        candidates = (
            configured,
            str(WINDOWS_POWERSHELL_WSL_PATH),
            shutil.which("powershell.exe"),
            shutil.which("powershell"),
        )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
        resolved = shutil.which(str(candidate))
        if resolved:
            return resolved
    return "powershell.exe"


def encoded_command_arguments(script: str) -> list[str]:
    """Return noninteractive PowerShell arguments using UTF-16LE transport."""
    if not isinstance(script, str) or not script:
        raise ValueError("PowerShell script must be a non-empty string")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def decode_encoded_command(encoded: str) -> str:
    """Decode an encoded command for tests and diagnostics only."""
    return base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-16le")


def powershell_encoded_command(
    script: str,
    *,
    executable: str | None = None,
    execution_policy_bypass: bool = False,
    window_style_hidden: bool = False,
    no_logo: bool = False,
) -> list[str]:
    """Build one resolved, noninteractive encoded-command invocation."""
    command = [executable or resolve_powershell_executable()]
    if no_logo:
        command.append("-NoLogo")
    command.extend(["-NoProfile", "-NonInteractive"])
    if execution_policy_bypass:
        command.extend(["-ExecutionPolicy", "Bypass"])
    if window_style_hidden:
        command.extend(["-WindowStyle", "Hidden"])
    encoded = encoded_command_arguments(script)
    command.extend(encoded[2:])
    return command


def powershell_file_command(
    script_path: str | Path,
    *arguments: str,
    executable: str | None = None,
    execution_policy_bypass: bool = False,
    no_logo: bool = False,
) -> list[str]:
    """Build one resolved invocation for an owner-controlled script file."""
    path_text = str(script_path).strip()
    if not path_text:
        raise ValueError("PowerShell script path must be non-empty")
    command = [executable or resolve_powershell_executable()]
    if no_logo:
        command.append("-NoLogo")
    command.extend(["-NoProfile", "-NonInteractive"])
    if execution_policy_bypass:
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(["-File", path_text, *[str(argument) for argument in arguments]])
    return command
