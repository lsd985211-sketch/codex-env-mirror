#!/usr/bin/env python3
"""Audit legacy resource-layer entry points.

Ownership: read-only resource compatibility and deprecation visibility.
Non-goals: remove commands, fetch resources, mutate logs, or rewrite callers.
State behavior: read-only deterministic report.
Caller context: resource_cli legacy-audit, workflow closeout, and validators.
"""

from __future__ import annotations

import argparse
from typing import Any

from shared.json_cli import configure_utf8_stdio, now_iso, print_json


configure_utf8_stdio()


LEGACY_ENTRIES: tuple[dict[str, Any], ...] = (
    {
        "command": "fetch-file",
        "status": "compatibility",
        "risk": "low",
        "interference": "bypasses_codex_delegation_metadata",
        "keep_reason": "stable shortcut for explicit local file materialization",
        "preferred": "delegate -> request for Codex-authored work; acquire for direct low-level compatibility",
        "remove": False,
    },
    {
        "command": "fetch-url",
        "status": "compatibility",
        "risk": "medium",
        "interference": "can materialize network resources without owner MCP evidence if used as a primary workflow route",
        "keep_reason": "stable explicit user URL compatibility path",
        "preferred": "delegate --url ... -> request, with allow-network and allow-filesystem-write set explicitly",
        "remove": False,
    },
    {
        "command": "probe-url",
        "status": "low_level_tool",
        "risk": "low",
        "interference": "should not replace source-owner MCP for docs/GitHub/browser evidence",
        "keep_reason": "bounded connectivity/probe primitive",
        "preferred": "delegate/request for resource work; network gateway for route decisions",
        "remove": False,
    },
    {
        "command": "preview-url",
        "status": "low_level_tool",
        "risk": "medium",
        "interference": "can look like research evidence while only providing generic URL preview",
        "keep_reason": "bounded preview primitive for generic URLs",
        "preferred": "owner MCP first for docs/GitHub/browser; preview-url only after fallback reason or no owner",
        "remove": False,
    },
    {
        "command": "acquire",
        "status": "low_level_policy_engine",
        "risk": "medium",
        "interference": "requires caller to choose intent/stage manually and lacks Codex delegation expectation metadata",
        "keep_reason": "resource_fetcher policy facade used by compatibility commands",
        "preferred": "delegate -> request for Codex workflow; acquire only for owner-maintained low-level tests",
        "remove": False,
    },
    {
        "command": "route",
        "status": "read_only_planner",
        "risk": "low",
        "interference": "can be mistaken for full request submission",
        "keep_reason": "cheap owner-tool planning and validation helper",
        "preferred": "delegate when Codex is handing work to resource layer; route for diagnostics only",
        "remove": False,
    },
)


def audit() -> dict[str, Any]:
    outdated = [item for item in LEGACY_ENTRIES if item["status"] == "compatibility"]
    medium_risk = [item for item in LEGACY_ENTRIES if item["risk"] == "medium"]
    return {
        "schema": "resource_legacy_audit.v1",
        "ok": True,
        "generated_at": now_iso(),
        "policy": {
            "primary_codex_entrypoint": "resource_cli delegate -> resource_cli request",
            "compatibility_rule": "legacy shortcuts remain available but must not be used as the default route for Codex-authored online/resource acquisition",
            "removal_rule": "do not remove until callers and scenario tests prove no dependency; prefer warning/reporting first",
        },
        "entries": list(LEGACY_ENTRIES),
        "summary": {
            "entry_count": len(LEGACY_ENTRIES),
            "compatibility_count": len(outdated),
            "medium_risk_count": len(medium_risk),
            "remove_now_count": sum(1 for item in LEGACY_ENTRIES if item.get("remove")),
        },
        "recommended_next_actions": [
            "Prefer delegate output in workflow/execution route packs for Codex-originated resource requests.",
            "Keep fetch-url/fetch-file as explicit compatibility shortcuts, not default automation paths.",
            "Use legacy-audit in validation/closeout when resource routing behavior changes.",
        ],
    }


def validate() -> dict[str, Any]:
    payload = audit()
    commands = {str(item.get("command") or "") for item in payload["entries"]}
    ok = (
        payload["summary"]["remove_now_count"] == 0
        and "fetch-url" in commands
        and "delegate" in payload["policy"]["primary_codex_entrypoint"]
        and "request" in payload["policy"]["primary_codex_entrypoint"]
        and any(item.get("command") == "fetch-url" and item.get("risk") == "medium" for item in payload["entries"])
    )
    return {
        "schema": "resource_legacy_audit.validate.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "summary": payload["summary"],
        "commands": sorted(commands),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit legacy resource-layer entry points.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.cmd == "audit":
        print_json(audit())
        return 0
    if args.cmd == "validate":
        payload = validate()
        print_json(payload)
        return 0 if payload.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
