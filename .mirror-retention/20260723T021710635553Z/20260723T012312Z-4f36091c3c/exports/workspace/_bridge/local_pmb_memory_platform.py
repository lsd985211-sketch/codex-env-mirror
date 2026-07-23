"""Resolve local PMB process paths and environment across Windows and WSL.

Ownership: PMB runtime executable discovery, WSLInterop environment export,
and path translation at the Windows process boundary.
Non-goals: PMB data mutation, daemon policy, compatibility decisions, or MCP
routing.
State behavior: pure path and environment construction; never writes files or
starts processes.
Caller context: consumed by ``local_pmb_memory.py`` and
``pmb_compatibility.py`` before invoking the governed PMB runtime.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

try:
    from platform_paths import host_accessible_path, host_compatibility_root
except ModuleNotFoundError:
    from _bridge.platform_paths import host_accessible_path, host_compatibility_root


PMB_WSLENV_KEYS = ("PMB_HOME", "PMB_WORKSPACE", "PYTHONIOENCODING", "PYTHONUTF8")


def uses_windows_interop(executable: str | Path, *, platform_name: str | None = None) -> bool:
    return (platform_name or os.name) != "nt" and str(executable).lower().endswith(".exe")


def runtime_bridge_root(
    source_bridge_root: Path,
    *,
    compatibility_root: Path | None = None,
    platform_name: str | None = None,
) -> Path:
    if (platform_name or os.name) == "nt":
        return source_bridge_root
    candidate = (compatibility_root or host_compatibility_root()) / "_bridge"
    pmb_exe = candidate / "venvs" / "pmb-memory" / "Scripts" / "pmb.exe"
    return candidate if pmb_exe.is_file() else source_bridge_root


def pmb_venv_root(source_bridge_root: Path, *, platform_name: str | None = None) -> Path:
    """Resolve one PMB venv root, preferring WSL-native storage on POSIX."""

    if (platform_name or os.name) == "nt":
        return source_bridge_root / "venvs" / "pmb-memory"
    native_root = Path(
        os.environ.get("CODEX_PMB_RUNTIME_ROOT")
        or (Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "codex-runtimes" / "pmb-memory")
    ).expanduser()
    if (native_root / "bin" / "pmb").is_file():
        return native_root
    candidate = (host_compatibility_root() / "_bridge" / "venvs" / "pmb-memory")
    return candidate if (candidate / "Scripts" / "pmb.exe").is_file() else native_root


def pmb_executables(venv_root: Path, *, platform_name: str | None = None) -> dict[str, Path]:
    if (platform_name or os.name) == "nt":
        return {
            "pmb": venv_root / "Scripts" / "pmb.exe",
            "python": venv_root / "Scripts" / "python.exe",
            "pythonw": venv_root / "Scripts" / "pythonw.exe",
        }
    return {
        "pmb": venv_root / "bin" / "pmb",
        "python": venv_root / "bin" / "python",
        "pythonw": venv_root / "bin" / "python",
    }


def process_argument_path(
    path: str | Path,
    executable: str | Path,
    *,
    platform_name: str | None = None,
) -> Path:
    if uses_windows_interop(executable, platform_name=platform_name):
        return host_accessible_path(path, platform_name="nt")
    return Path(path)


def local_accessible_path(value: str | Path, *, platform_name: str | None = None) -> Path:
    target_platform = platform_name or os.name
    return host_accessible_path(value, platform_name=target_platform)


def merge_wslenv(current: str, names: tuple[str, ...] = PMB_WSLENV_KEYS) -> str:
    replaced = set(names)
    entries = [
        item
        for item in str(current or "").split(":")
        if item and item.split("/", 1)[0] not in replaced
    ]
    entries.extend(names)
    return ":".join(entries)


def process_environment(
    base_env: Mapping[str, str] | None,
    *,
    pmb_home: Path,
    workspace: str,
    executable: str | Path,
    platform_name: str | None = None,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env["PMB_HOME"] = str(process_argument_path(pmb_home, executable, platform_name=platform_name))
    if workspace:
        env["PMB_WORKSPACE"] = workspace
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if uses_windows_interop(executable, platform_name=platform_name):
        env["WSLENV"] = merge_wslenv(env.get("WSLENV", ""))
    else:
        # A native Linux PMB process must not inherit Desktop's Windows export list.
        env.pop("WSLENV", None)
    return env
