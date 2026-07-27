#!/usr/bin/env python3
"""Shared execution-platform admission for owner health checks."""

from __future__ import annotations

import os
from pathlib import Path


def execution_platform_scope() -> str:
    if os.name == "nt":
        return "windows_host"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        version = ""
    return "wsl" if "microsoft" in version or "wsl" in version else "linux"


def platform_scope_matches(required: str, current: str) -> bool:
    required_scope = str(required or "all").strip().lower()
    current_scope = str(current or "").strip().lower()
    return required_scope in {"", "all"} or required_scope == current_scope
