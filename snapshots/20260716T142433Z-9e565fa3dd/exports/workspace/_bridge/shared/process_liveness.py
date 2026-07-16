#!/usr/bin/env python3
"""Side-effect-free process liveness checks for first-party bridge owners."""

from __future__ import annotations

import ast
import errno
import os
from pathlib import Path
from typing import Any


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
EXCLUDED_SCAN_PARTS = frozenset(
    {
        "archive",
        "backups",
        "logs",
        "node_modules",
        "resources",
        "runtime",
        "runtime_dependencies",
        "tmp",
        "venvs",
        "wheelhouse",
        "wheels",
        ".ruff_cache",
        "_bridge",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
    }
)


def normalize_pid(value: Any) -> int | None:
    """Return a positive PID without accepting booleans or lossy numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.isdecimal():
            return None
        numeric = int(text)
        return numeric if numeric > 0 else None
    return None


def _windows_process_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def process_is_alive(value: Any) -> bool:
    """Check process state without signalling a Windows process or process group."""
    pid = normalize_pid(value)
    if pid is None:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    return _posix_process_is_alive(pid)


def find_unsafe_zero_signal_probes(root: Path) -> list[dict[str, Any]]:
    """Find first-party direct ``os.kill(pid, 0)`` liveness probes."""
    findings: list[dict[str, Any]] = []
    own_path = Path(__file__).resolve()
    paths: list[Path] = []
    for directory, child_dirs, filenames in os.walk(root):
        child_dirs[:] = [name for name in child_dirs if name.casefold() not in EXCLUDED_SCAN_PARTS]
        paths.extend(Path(directory) / name for name in filenames if name.casefold().endswith(".py"))
    for path in paths:
        resolved = path.resolve()
        if resolved == own_path:
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
            if "os.kill" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            function = node.func
            is_os_kill = (
                isinstance(function, ast.Attribute)
                and function.attr == "kill"
                and isinstance(function.value, ast.Name)
                and function.value.id == "os"
            )
            signal_arg = node.args[1]
            if is_os_kill and isinstance(signal_arg, ast.Constant) and signal_arg.value == 0:
                findings.append({"path": str(path), "line": int(node.lineno), "code": "unsafe_windows_zero_signal_probe"})
    return findings


__all__ = ["find_unsafe_zero_signal_probes", "normalize_pid", "process_is_alive"]
