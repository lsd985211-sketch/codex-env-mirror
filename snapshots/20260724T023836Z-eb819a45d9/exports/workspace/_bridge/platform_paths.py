#!/usr/bin/env python3
"""Resolve platform paths without coupling the workspace to one host."""

from __future__ import annotations

import os
import re
from pathlib import Path


WINDOWS_HOST_COMPATIBILITY_ROOT = Path(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
WSL_HOST_COMPATIBILITY_ROOT = Path("/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager")
DEFAULT_WSL_DISTRIBUTION = "Codex-Wsl-Lab"
DEFAULT_WSL_WORKTREE = "/home/codexlab/work/codex-workspace"
WINDOWS_RESOURCE_LIBRARY_ROOT = Path(r"C:\Users\45543\Desktop\Codex资源库")
WSL_RESOURCE_LIBRARY_ROOT = Path("/mnt/c/Users/45543/Desktop/Codex资源库")
WSL_UNC_RE = re.compile(r"(?i)^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)\\?(.*)$")
WSL_MOUNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def _env_path(name: str, fallback: Path) -> Path:
    value = str(os.environ.get(name, "")).strip()
    return Path(value).expanduser().resolve() if value else fallback.resolve()


def wsl_distribution() -> str:
    return str(os.environ.get("WSL_DISTRIBUTION") or os.environ.get("WSL_DISTRO_NAME") or DEFAULT_WSL_DISTRIBUTION)


def wsl_worktree_linux_root() -> str:
    return str(os.environ.get("WSL_WORKTREE_LINUX") or DEFAULT_WSL_WORKTREE).replace("\\", "/").rstrip("/")


def wsl_worktree_unc_root() -> Path:
    suffix = wsl_worktree_linux_root().lstrip("/").replace("/", "\\")
    return Path(rf"\\wsl.localhost\{wsl_distribution()}\{suffix}")


def wsl_linux_path_text(value: str | Path) -> str:
    """Return a Linux path string without letting Windows pathlib rewrite separators."""

    text = str(value or "").strip()
    unc = WSL_UNC_RE.match(text)
    if unc and unc.group(1).casefold() == wsl_distribution().casefold():
        suffix = unc.group(2).replace("\\", "/").lstrip("/")
        return f"/{suffix}" if suffix else "/"
    normalized = text.replace("\\", "/")
    return normalized


def host_accessible_path(value: str | Path, *, platform_name: str | None = None) -> Path:
    """Translate WSL/Linux paths for Windows owners and WSL UNC paths back for Linux owners."""

    target_platform = platform_name or os.name
    text = str(value or "").strip()
    if not text:
        return Path("")
    normalized = text.replace("\\", "/")
    if target_platform == "nt":
        if WSL_UNC_RE.match(text):
            return Path(text)
        mount = WSL_MOUNT_RE.match(normalized)
        if mount:
            suffix = str(mount.group(2) or "").replace("/", "\\")
            return Path(f"{mount.group(1).upper()}:\\{suffix}" if suffix else f"{mount.group(1).upper()}:\\")
        if normalized.startswith("/"):
            suffix = normalized.lstrip("/").replace("/", "\\")
            return Path(rf"\\wsl.localhost\{wsl_distribution()}\{suffix}")
        return Path(text).expanduser()
    unc = WSL_UNC_RE.match(text)
    if unc and unc.group(1).casefold() == wsl_distribution().casefold():
        return Path(wsl_linux_path_text(text))
    drive = WINDOWS_DRIVE_RE.match(text)
    if drive:
        suffix = drive.group(2).replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive.group(1).lower()}/{suffix}")
    return Path(text).expanduser()


def same_host_path(left: str | Path, right: str | Path) -> bool:
    """Compare Windows and WSL spellings of the same host-accessible path."""

    def identity(value: str | Path) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        translated = host_accessible_path(text, platform_name="nt")
        return str(translated).replace("/", "\\").rstrip("\\").casefold()

    left_identity = identity(left)
    return bool(left_identity) and left_identity == identity(right)


def worktree_root() -> Path:
    value = str(os.environ.get("WORKTREE_ROOT", "")).strip()
    if value:
        return Path(value).expanduser().resolve()
    if os.name == "nt":
        wsl_root = wsl_worktree_unc_root()
        if (wsl_root / "workspace" / "_bridge").exists():
            return wsl_root
    return Path(__file__).resolve().parents[2]


def workspace_root() -> Path:
    candidate = str(os.environ.get("WORKSPACE_ROOT", "")).strip()
    if candidate:
        path = Path(candidate).expanduser().resolve()
        if (path / "_bridge").exists():
            return path
        nested = path / "workspace"
        if (nested / "_bridge").exists():
            return nested
    root = worktree_root()
    nested = root / "workspace"
    if (nested / "_bridge").exists():
        return nested
    if (root / "_bridge").exists():
        return root
    return Path(__file__).resolve().parents[1]


def host_compatibility_root() -> Path:
    fallback = WINDOWS_HOST_COMPATIBILITY_ROOT if os.name == "nt" else WSL_HOST_COMPATIBILITY_ROOT
    return _env_path("WINDOWS_HOST_COMPATIBILITY_ROOT", fallback)


def windows_user_root() -> Path:
    """Return the host user profile root from either execution platform."""

    if os.name == "nt":
        return Path.home().resolve()
    host_root = host_compatibility_root()
    try:
        return host_root.parents[2]
    except IndexError:
        return Path("/mnt/c/Users/45543")


def resource_library_root() -> Path:
    fallback = WINDOWS_RESOURCE_LIBRARY_ROOT if os.name == "nt" else WSL_RESOURCE_LIBRARY_ROOT
    return _env_path("CODEX_RESOURCE_LIBRARY_ROOT", fallback)


def memory_root() -> Path:
    """Return the platform-local durable memory root.

    Windows keeps the host resource-library location for compatibility. WSL
    stores active PMB state on ext4 so the daily control plane does not depend
    on an NTFS projection.
    """

    if os.name == "nt":
        fallback = resource_library_root() / "memory"
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        fallback = data_home / "codex" / "memory"
    return _env_path("CODEX_MEMORY_ROOT", fallback)


def scheduler_state_root() -> Path:
    """Return the durable scheduler control-plane state location.

    The Windows resource library remains the host compatibility location.  WSL
    runs the primary scheduler, so its mutable task table, lock, heartbeat and
    receipts must stay on ext4 rather than traverse the Windows 9P mount.
    """

    if os.name == "nt":
        fallback = resource_library_root() / "文档" / "定时模块" / "运行态" / "统一调度"
    else:
        state_home = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
        fallback = state_home / "codex" / "scheduler"
    return _env_path("CODEX_SCHEDULER_STATE_ROOT", fallback)


def codex_home() -> Path:
    return _env_path("CODEX_HOME", Path.home() / ".codex")


def agent_home() -> Path:
    return _env_path("AGENT_HOME", Path.home() / ".agents")


def cc_switch_home() -> Path:
    fallback = Path.home() / ".cc-switch" if os.name == "nt" else windows_user_root() / ".cc-switch"
    return _env_path("CC_SWITCH_HOME", fallback)


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def cc_switch_database_path() -> Path:
    return cc_switch_home() / "cc-switch.db"


def exported_environment() -> dict[str, str]:
    return {
        "WORKTREE_ROOT": str(worktree_root()),
        "WORKSPACE_ROOT": str(workspace_root()),
        "WINDOWS_HOST_COMPATIBILITY_ROOT": str(host_compatibility_root()),
        "CODEX_RESOURCE_LIBRARY_ROOT": str(resource_library_root()),
        "CODEX_MEMORY_ROOT": str(memory_root()),
        "CODEX_SCHEDULER_STATE_ROOT": str(scheduler_state_root()),
        "CODEX_HOME": str(codex_home()),
        "AGENT_HOME": str(agent_home()),
        "CC_SWITCH_HOME": str(cc_switch_home()),
    }
