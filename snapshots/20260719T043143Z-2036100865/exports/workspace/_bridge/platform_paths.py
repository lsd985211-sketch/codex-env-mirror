#!/usr/bin/env python3
"""Resolve platform paths without coupling the workspace to one host."""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str, fallback: Path) -> Path:
    value = str(os.environ.get(name, "")).strip()
    return Path(value).expanduser().resolve() if value else fallback.resolve()


def worktree_root() -> Path:
    value = str(os.environ.get("WORKTREE_ROOT", "")).strip()
    if value:
        return Path(value).expanduser().resolve()
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
    return Path(__file__).resolve().parents[1]


def codex_home() -> Path:
    return _env_path("CODEX_HOME", Path.home() / ".codex")


def agent_home() -> Path:
    return _env_path("AGENT_HOME", Path.home() / ".agents")


def cc_switch_home() -> Path:
    return _env_path("CC_SWITCH_HOME", Path.home() / ".cc-switch")


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def cc_switch_database_path() -> Path:
    return cc_switch_home() / "cc-switch.db"


def exported_environment() -> dict[str, str]:
    return {
        "WORKTREE_ROOT": str(worktree_root()),
        "WORKSPACE_ROOT": str(workspace_root()),
        "CODEX_HOME": str(codex_home()),
        "AGENT_HOME": str(agent_home()),
        "CC_SWITCH_HOME": str(cc_switch_home()),
    }
