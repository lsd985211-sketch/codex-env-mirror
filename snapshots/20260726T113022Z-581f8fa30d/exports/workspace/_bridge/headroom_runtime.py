#!/usr/bin/env python3
"""Managed Headroom runtime and standalone MCP launcher.

Ownership: resolve the resource-owned ABI-scoped Headroom install, verify the
pinned distribution and imports, and launch only the standalone context
compression MCP with an isolated short-lived cache.
Non-goals: package installation, proxy/provider wrapping, Codex config changes,
model routing, long-term memory, telemetry services, or unrestricted Headroom
CLI access.
State behavior: status/validate are read-only; serve writes only Headroom's
TTL-bound reversible compression cache below the declared external state root.
Caller context: local_mcp_hub_headroom starts this owner through fresh stdio.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import managed_python_dependency_runtime as managed_python_runtime
import platform_paths


SCHEMA = "headroom_runtime.v1"
BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_DEPENDENCY_ROOT = BRIDGE_ROOT / "runtime_dependencies"
DEFAULT_STATE_ROOT = Path.home() / ".local" / "share" / "codex-headroom"
PACKAGE_NAME = "headroom-ai"
EXPECTED_VERSION = "0.32.1"
CCR_TTL_SECONDS = 1800


def _default_dependency_root() -> Path:
    if os.name != "nt":
        canonical = Path(platform_paths.wsl_worktree_linux_root()) / "workspace" / "_bridge" / "runtime_dependencies"
        if canonical.is_dir():
            return canonical.resolve()
    return DEFAULT_DEPENDENCY_ROOT.resolve()


def dependency_root(value: Path | None = None) -> Path:
    raw = str(os.environ.get("CODEX_HEADROOM_DEPENDENCY_ROOT") or "").strip()
    return (value or (Path(raw).expanduser() if raw else _default_dependency_root())).resolve()


def state_root(value: Path | None = None) -> Path:
    raw = str(os.environ.get("CODEX_HEADROOM_STATE_ROOT") or "").strip()
    return (value or (Path(raw).expanduser() if raw else DEFAULT_STATE_ROOT)).resolve()


def _installed_version(target: Path) -> str:
    for distribution in importlib.metadata.distributions(path=[str(target)]):
        name = re.sub(r"[-_.]+", "-", str(distribution.metadata.get("Name") or "")).lower()
        if name == PACKAGE_NAME:
            return str(distribution.version or "")
    return ""


def status(*, dependency: Path | None = None, state: Path | None = None) -> dict[str, Any]:
    root = dependency_root(dependency)
    selected = managed_python_runtime.select_dependency_target(root, PACKAGE_NAME)
    target = Path(str(selected.get("path") or ""))
    version = _installed_version(target) if selected.get("ok") else ""
    ready = bool(selected.get("ok") and version == EXPECTED_VERSION)
    return {
        "schema": f"{SCHEMA}.status",
        "ok": ready,
        "reason": "headroom_runtime_ready" if ready else "headroom_runtime_not_ready",
        "package": PACKAGE_NAME,
        "expected_version": EXPECTED_VERSION,
        "installed_version": version,
        "dependency_root": str(root),
        "runtime_target": str(target),
        "runtime_selection": selected,
        "state_root": str(state_root(state)),
        "state_contract": "ttl_bound_reversible_context_cache_not_long_term_memory",
        "ttl_seconds": CCR_TTL_SECONDS,
        "provider_config_modified": False,
        "pmb_authority_preserved": True,
    }


def command_spec(*, dependency: Path | None = None, state: Path | None = None) -> dict[str, Any]:
    current = status(dependency=dependency, state=state)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "serve",
        "--dependency-root",
        str(dependency_root(dependency)),
        "--state-root",
        str(state_root(state)),
    ]
    return {
        "schema": f"{SCHEMA}.mcp_command",
        "ok": bool(current.get("ok")),
        "command": command,
        "working_directory": str(BRIDGE_ROOT.parent.parent),
        "lifecycle": "fresh_stdio_per_hub_call",
        "runtime": current,
    }


def validate(*, dependency: Path | None = None, state: Path | None = None) -> dict[str, Any]:
    current = status(dependency=dependency, state=state)
    required_imports = list(managed_python_runtime.required_imports(PACKAGE_NAME))
    issues: list[dict[str, Any]] = []
    if not current.get("ok"):
        issues.append({"severity": "risk", "code": "headroom_runtime_not_ready", "details": current})
    if "headroom.ccr.mcp_server" not in required_imports:
        issues.append({"severity": "risk", "code": "headroom_mcp_import_contract_missing"})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "issues": issues,
        "status": current,
        "required_imports": required_imports,
        "activation": "Hub reload only; no Codex provider or model configuration change",
    }


async def _serve(dependency: Path, state: Path) -> None:
    current = status(dependency=dependency, state=state)
    if not current.get("ok"):
        raise RuntimeError(json.dumps(current, ensure_ascii=False))
    target = str(current["runtime_target"])
    sys.path.insert(0, target)
    state.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "HEADROOM_WORKSPACE_DIR": str(state),
            "HEADROOM_CONFIG_DIR": str(state / "config"),
            "HEADROOM_CCR_TTL_SECONDS": str(CCR_TTL_SECONDS),
            "HEADROOM_MCP_CLIENT": "codex-local-mcp-hub",
            "HEADROOM_MCP_READ": "off",
            "PYTHONNOUSERSITE": "1",
        }
    )
    from headroom.ccr.mcp_server import HeadroomMCPServer

    server = HeadroomMCPServer(check_proxy=False)
    try:
        await server.run_stdio()
    finally:
        await server.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Managed Headroom MCP runtime")
    parser.add_argument("command", choices=("status", "validate", "mcp-command", "serve"))
    parser.add_argument("--dependency-root", type=Path)
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "serve":
        asyncio.run(_serve(dependency_root(args.dependency_root), state_root(args.state_root)))
        return 0
    payload = (
        status(dependency=args.dependency_root, state=args.state_root)
        if args.command == "status"
        else command_spec(dependency=args.dependency_root, state=args.state_root)
        if args.command == "mcp-command"
        else validate(dependency=args.dependency_root, state=args.state_root)
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
