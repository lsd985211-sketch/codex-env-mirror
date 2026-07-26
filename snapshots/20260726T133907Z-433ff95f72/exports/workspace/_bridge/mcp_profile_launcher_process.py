#!/usr/bin/env python3
"""MCP profile launcher process-lifecycle boundary.

Ownership: enter the lazy stdio proxy in the current launcher process or run a
non-lazy profile as a guarded child while preserving stdio and environment.
Non-goals: choose profiles, alter MCP permissions, share stateful children, or
implement MCP protocol behavior.
State behavior: no durable state writes; temporary cwd/environment changes are
restored if an in-process proxy returns.
Caller context: mcp_profile_launcher.py after profile routing is complete.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def run_profile_process(
    command: list[str],
    *,
    extra_env: dict[str, str],
    cwd: Path,
    lazy_proxy: Path,
    lazy_entrypoint: Callable[[list[str] | None], int] | None = None,
) -> int:
    if not command:
        raise ValueError("profile command is empty")
    if len(command) >= 2 and _same_path(command[1], lazy_proxy):
        if lazy_entrypoint is None:
            from mcp_lazy_stdio_proxy import main as lazy_entrypoint

        previous_cwd = Path.cwd()
        previous_env = {key: os.environ.get(key) for key in extra_env}
        try:
            os.environ.update(extra_env)
            os.chdir(cwd)
            return int(lazy_entrypoint(command[2:]))
        finally:
            os.chdir(previous_cwd)
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        **NO_WINDOW_KW,
    ).returncode
