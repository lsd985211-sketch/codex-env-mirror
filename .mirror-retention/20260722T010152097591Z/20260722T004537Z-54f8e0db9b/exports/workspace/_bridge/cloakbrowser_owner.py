#!/usr/bin/env python3
"""Governed optional CloakBrowser owner surface.

The wrapper may be installed, but browser binary acquisition and launches stay
explicit because the binary has separate licensing, size, and runtime effects.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any


BRIDGE_ROOT = Path(__file__).resolve().parent
DEPENDENCY_ROOT = BRIDGE_ROOT / "runtime_dependencies" / "cloakbrowser"
DEFAULT_CACHE_ROOT = BRIDGE_ROOT / "resources" / "cloakbrowser" / "binary-cache"


def _load_wrapper() -> tuple[Any | None, str]:
    if str(DEPENDENCY_ROOT) not in sys.path:
        sys.path.insert(0, str(DEPENDENCY_ROOT))
    try:
        import cloakbrowser  # type: ignore

        version = importlib.metadata.version("cloakbrowser")
        return cloakbrowser, version
    except Exception:
        return None, ""


def snapshot() -> dict[str, Any]:
    module, version = _load_wrapper()
    configured_binary = str(os.environ.get("CLOAKBROWSER_BINARY_PATH") or "").strip()
    binary_path = Path(configured_binary).expanduser().resolve() if configured_binary else None
    return {
        "schema": "cloakbrowser_owner.snapshot.v1",
        "ok": module is not None,
        "wrapper_installed": module is not None,
        "wrapper_version": version,
        "dependency_root": str(DEPENDENCY_ROOT),
        "binary_configured": bool(binary_path),
        "binary_exists": bool(binary_path and binary_path.exists()),
        "binary_path": str(binary_path or ""),
        "managed_cache_root": str(DEFAULT_CACHE_ROOT),
        "default_route": "disabled_until_explicit_authorized_request",
        "replaces_default_browser_chain": False,
        "auto_download_allowed": False,
    }


def doctor() -> dict[str, Any]:
    state = snapshot()
    issues: list[dict[str, Any]] = []
    if not state["wrapper_installed"]:
        issues.append({"severity": "risk", "code": "cloakbrowser_wrapper_missing", "next_action": "install_through_resource_layer"})
    if state["binary_configured"] and not state["binary_exists"]:
        issues.append({"severity": "risk", "code": "configured_binary_missing", "path": state["binary_path"]})
    if not state["binary_configured"]:
        issues.append({"severity": "advisory", "code": "binary_not_materialized", "next_action": "keep_disabled_or_submit_explicit_licensed_binary_resource_request"})
    return {
        "schema": "cloakbrowser_owner.doctor.v1",
        "ok": not any(item["severity"] == "risk" for item in issues),
        "issues": issues,
        "snapshot": state,
    }


def plan(*, task: str, authorized: bool) -> dict[str, Any]:
    state = snapshot()
    blockers: list[str] = []
    if not authorized:
        blockers.append("explicit_authorization_required")
    if not state["binary_exists"]:
        blockers.append("verified_binary_required")
    return {
        "schema": "cloakbrowser_owner.plan.v1",
        "ok": not blockers,
        "task": task,
        "authorized": authorized,
        "blockers": blockers,
        "route": [
            "confirm task authorization and legal/terms boundary",
            "materialize a licensed binary through the resource layer with source, hash, size, and path receipt",
            "set CLOAKBROWSER_BINARY_PATH only for the isolated child process",
            "use a dedicated user-data and cache directory",
            "verify visible or machine-readable browser result",
        ],
        "safety": {
            "no_default_browser_replacement": True,
            "no_implicit_binary_download": True,
            "no_global_proxy_or_profile_mutation": True,
            "separate_profile_required": True,
        },
        "snapshot": state,
    }


def validate() -> dict[str, Any]:
    state = snapshot()
    denied = plan(task="validation", authorized=False)
    return {
        "schema": "cloakbrowser_owner.validate.v1",
        "ok": bool(state["wrapper_installed"] and "explicit_authorization_required" in denied["blockers"] and not state["auto_download_allowed"]),
        "snapshot": state,
        "authorization_gate_ok": "explicit_authorization_required" in denied["blockers"],
        "implicit_download_disabled": not state["auto_download_allowed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed optional CloakBrowser owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("doctor")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--task", required=True)
    plan_parser.add_argument("--authorized", action="store_true")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "plan":
        payload = plan(task=args.task, authorized=bool(args.authorized))
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
