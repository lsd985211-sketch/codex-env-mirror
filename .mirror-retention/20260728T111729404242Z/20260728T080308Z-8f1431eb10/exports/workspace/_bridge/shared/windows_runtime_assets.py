"""Authoritative paths for reusable Windows-only capability runtimes."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_WINDOWS_LOCAL_APPDATA = Path(r"C:\Users\45543\AppData\Local")


def windows_local_appdata() -> Path:
    configured = str(os.environ.get("LOCALAPPDATA") or "").strip()
    return Path(configured) if configured else DEFAULT_WINDOWS_LOCAL_APPDATA


def windows_codex_runtime_root() -> Path:
    configured = str(os.environ.get("CODEX_WINDOWS_RUNTIME_ROOT") or "").strip()
    return Path(configured) if configured else windows_local_appdata() / "Codex"


def windows_pip_cache_root() -> Path:
    configured = str(os.environ.get("CODEX_WINDOWS_PIP_CACHE_ROOT") or "").strip()
    return Path(configured) if configured else windows_local_appdata() / "pip" / "cache"


def gui_ocr_runtime_root() -> Path:
    configured = str(os.environ.get("GUI_OCR_RUNTIME_ROOT") or "").strip()
    return Path(configured) if configured else windows_codex_runtime_root() / "runtimes" / "ocr"


def gui_ocr_python_paths() -> tuple[Path, Path]:
    root = gui_ocr_runtime_root()
    primary = Path(os.environ.get("GUI_OCR_PYTHON") or root / "gpu-venv" / "Scripts" / "python.exe")
    fallback = Path(os.environ.get("GUI_OCR_FALLBACK_PYTHON") or root / "cpu-venv" / "Scripts" / "python.exe")
    return primary, fallback


def gui_ocr_pip_cache_path() -> Path:
    """Logical OCR cache path; it should link to the single Windows pip cache."""
    return gui_ocr_runtime_root() / "pip-cache"


def openclaw_runtime_root() -> Path:
    configured = str(os.environ.get("CODEX_OPENCLAW_RUNTIME_ROOT") or "").strip()
    return Path(configured) if configured else windows_codex_runtime_root() / "openclaw"


def openclaw_install_root() -> Path:
    return openclaw_runtime_root() / "clean-install"


def openclaw_node_path() -> Path:
    configured = str(os.environ.get("CODEX_OPENCLAW_NODE") or "").strip()
    if configured:
        return Path(configured)
    return openclaw_runtime_root() / "node24" / "node-v24.17.0-win-x64" / "node.exe"


def openclaw_state_path() -> Path:
    return openclaw_install_root() / "state"


def openclaw_reply_script_path() -> Path:
    return openclaw_runtime_root() / "weixin_send_reply.mjs"
