#!/usr/bin/env python3
"""Read-only gate for direct generic web access.

Ownership: decides whether a Codex turn may use generic web directly for an
online/external resource need, and emits the resource-layer-first plan.
Non-goals: executing web requests, executing resource acquisition, mutating
resource-layer state, or hiding platform tools.
State behavior: read-only; direct-web exceptions are represented as evidence.
Caller context: workflow plans, closeout validation, and global coherence checks.
"""

from __future__ import annotations

import argparse
from typing import Any

from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


configure_utf8_stdio()

SCHEMA = "online_access_gate.v1"

RESOURCE_LAYER_ACTIVE_STATUSES = {"handoff_required", "running", "queued", "processing"}
RESOURCE_LAYER_REFINEMENT_STATUSES = {"deferred", "insufficient_coverage", "low_relevance"}
RESOURCE_LAYER_ROUTE_CHAIN_STATUSES = {"failed", "blocked", "terminal_blocker", "no_owner_route"}
DIRECT_WEB_REASONS = {
    "resource_layer_unavailable": "resource layer command/tool is unavailable or cannot start",
    "predefined_online_route_exhausted": "resource layer and the configured owner/Hub online route chain were tried and could not complete",
    "explicit_user_direct_web": "user explicitly requested Codex direct web for this task",
}
PLATFORM_WEB_REASON = {
    "higher_precedence_platform_web_required": "a higher-precedence platform instruction explicitly required generic web for this task",
}
ONLINE_TERMS = (
    "联网",
    "搜索",
    "查资料",
    "相关知识",
    "web",
    "online",
    "research",
    "lookup",
    "docs",
    "github",
)

ONLINE_ROUTE_STEPS = (
    "native_owner",
    "hub_owner_direct",
    "complete_route_if_needed",
    "local_hub",
    "owner_cli",
    "generic_web",
)
ONLINE_ROUTE_EVIDENCE_ALIASES = {
    "native_owner_failed": "native_owner",
    "native_mcp_failed": "native_owner",
    "hub_owner_failed": "hub_owner_direct",
    "hub_direct_failed": "hub_owner_direct",
    "complete_route_failed": "complete_route_if_needed",
    "complete_route_not_needed": "complete_route_if_needed",
    "local_hub_failed": "local_hub",
    "local_hub_not_applicable": "local_hub",
    "owner_cli_failed": "owner_cli",
    "owner_cli_not_applicable": "owner_cli",
}


def route_chain_evidence(evidence: str) -> dict[str, Any]:
    raw = [item.strip().lower() for item in (evidence or "").replace(",", ";").split(";") if item.strip()]
    completed: list[str] = []
    for item in raw:
        step = ONLINE_ROUTE_EVIDENCE_ALIASES.get(item, item.split("=", 1)[0].split(":", 1)[0])
        if step in ONLINE_ROUTE_STEPS and step not in completed:
            completed.append(step)
    required = ["native_owner", "hub_owner_direct", "local_hub", "owner_cli"]
    missing = [step for step in required if step not in completed]
    ordered = [step for step in ONLINE_ROUTE_STEPS if step in completed] == completed
    return {
        "schema": "online_access_gate.route_chain_evidence.v1",
        "completed_steps": completed,
        "required_steps": required,
        "missing_steps": missing,
        "ordered": ordered,
        "exhausted": not missing and ordered,
        "next_step": missing[0] if missing else "generic_web",
    }


def wants_online(message: str) -> bool:
    return bool(matched_terms(message, ONLINE_TERMS))


def user_requested_direct_web(message: str) -> bool:
    text = (message or "").lower()
    markers = (
        "直接 web",
        "直接联网",
        "codex 直接",
        "不用资源层",
        "不要资源层",
        "direct web",
        "direct fetch",
        "skip resource",
    )
    return bool(matched_terms(text, markers))


def resource_command(message: str) -> str:
    safe = (message or "").replace('"', '\\"')
    return f'python _bridge\\codex_workflow_entry.py resource job run --task "{safe}" --target "{safe}" --validation-profile quick --receipt-detail compact --json'


def plan(message: str, *, direct_web_requested: bool = False) -> dict[str, Any]:
    direct = direct_web_requested or user_requested_direct_web(message)
    online = wants_online(message)
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": True,
        "generated_at": now_iso(),
        "message": message,
        "online_intent_detected": online,
        "resource_layer_required": bool(online and not direct),
        "direct_web_allowed": bool(direct),
        "direct_web_reason": "explicit_user_direct_web" if direct else "",
        "resource_submit_command": "" if direct or not online else resource_command(message),
        "rule": "Generic web is allowed only when the resource layer is unavailable, the configured owner/Hub online route chain is exhausted, the user explicitly requests direct web, or a higher-precedence platform instruction is recorded through a dedicated structured flag.",
        "required_evidence_before_direct_web": [
            "resource_layer_unavailable",
            "predefined_online_route_exhausted",
            "explicit_user_direct_web",
            "higher_precedence_platform_web_required (structured flag only)",
        ],
    }


def normalize_reason(reason: str) -> str:
    value = (reason or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "route_chain_exhausted": "predefined_online_route_exhausted",
        "owner_route_exhausted": "predefined_online_route_exhausted",
        "user_direct": "explicit_user_direct_web",
        "explicit_user": "explicit_user_direct_web",
        "resource_unavailable": "resource_layer_unavailable",
    }
    return aliases.get(value, value)


def check(
    *,
    web_used: bool,
    resource_request_id: str = "",
    resource_status: str = "",
    fallback_reason: str = "",
    user_direct_web: bool = False,
    platform_web_required: bool = False,
    evidence: str = "",
) -> dict[str, Any]:
    reason = normalize_reason(fallback_reason)
    status = (resource_status or "").strip().lower()
    chain_evidence = route_chain_evidence(evidence)
    blockers: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    direct_allowed = False
    matched_reason = ""
    if platform_web_required:
        direct_allowed = True
        matched_reason = "higher_precedence_platform_web_required"
    elif user_direct_web or reason == "explicit_user_direct_web":
        direct_allowed = True
        matched_reason = "explicit_user_direct_web"
    elif reason == "resource_layer_unavailable":
        direct_allowed = True
        matched_reason = "resource_layer_unavailable"
    elif reason == "predefined_online_route_exhausted":
        direct_allowed = bool(chain_evidence.get("exhausted"))
        matched_reason = "predefined_online_route_exhausted" if direct_allowed else ""
    elif resource_request_id and status in RESOURCE_LAYER_REFINEMENT_STATUSES:
        blockers.append(
            {
                "code": "resource_layer_needs_refinement",
                "message": "Resource layer returned an improvable result; refine the resource delegation instead of using direct generic web.",
                "resource_request_id": resource_request_id,
                "resource_status": status,
            }
        )
    elif resource_request_id and status in RESOURCE_LAYER_ROUTE_CHAIN_STATUSES:
        blockers.append(
            {
                "code": "resource_layer_failure_requires_online_route_chain",
                "message": "Resource-layer failure/blocker must go through the configured owner/Hub online route chain before direct generic web.",
                "resource_request_id": resource_request_id,
                "resource_status": status,
            }
        )
    elif resource_request_id and status in RESOURCE_LAYER_ACTIVE_STATUSES:
        advisories.append(
            {
                "code": "resource_layer_still_owns_request",
                "message": "Resource layer still owns this online need; direct web should not replace it.",
                "resource_request_id": resource_request_id,
                "resource_status": status,
            }
        )
    if web_used and not direct_allowed:
        blockers.append(
            {
                "code": "direct_web_without_resource_exception",
                "message": "Generic web was used without resource-layer unavailable evidence, configured route-chain exhaustion evidence, explicit user direct-web request, or an explicit higher-precedence platform requirement flag.",
                "resource_request_id": resource_request_id,
                "resource_status": status,
                "fallback_reason": fallback_reason,
            }
        )
    if web_used and reason == "predefined_online_route_exhausted" and not chain_evidence.get("exhausted"):
        blockers.append(
            {
                "code": "route_chain_exhaustion_without_complete_evidence",
                "message": "Direct web requires ordered evidence for every applicable owner/Hub fallback step.",
                "missing_steps": chain_evidence.get("missing_steps", []),
                "next_step": chain_evidence.get("next_step", ""),
            }
        )
    if web_used and direct_allowed and not (
        evidence
        or resource_request_id
        or matched_reason in {"explicit_user_direct_web", "higher_precedence_platform_web_required"}
    ):
        advisories.append(
            {
                "code": "direct_web_exception_evidence_weak",
                "message": "Direct web exception is allowed but should include concrete resource-layer evidence when possible.",
                "matched_reason": matched_reason,
            }
        )
    return {
        "schema": f"{SCHEMA}.check",
        "ok": not blockers,
        "generated_at": now_iso(),
        "web_used": web_used,
        "direct_web_allowed": direct_allowed,
        "matched_reason": matched_reason,
        "matched_reason_description": (DIRECT_WEB_REASONS | PLATFORM_WEB_REASON).get(matched_reason, ""),
        "platform_web_required": platform_web_required,
        "resource_request_id": resource_request_id,
        "resource_status": status,
        "fallback_reason": fallback_reason,
        "evidence": evidence,
        "route_chain_evidence": chain_evidence,
        "blockers": blockers,
        "advisories": advisories,
        "rule": "Use resource layer first; direct generic web requires resource-layer unavailable evidence, configured owner/Hub route-chain exhaustion evidence, explicit user request, or an explicit higher-precedence platform requirement flag.",
    }


def exception(reason: str, *, resource_request_id: str = "", resource_status: str = "", evidence: str = "") -> dict[str, Any]:
    normalized = normalize_reason(reason)
    allowed = normalized in DIRECT_WEB_REASONS
    blockers: list[dict[str, Any]] = []
    if not allowed:
        blockers.append({"code": "unsupported_direct_web_reason", "reason": reason, "allowed": sorted(DIRECT_WEB_REASONS)})
    if normalized == "predefined_online_route_exhausted" and not evidence:
        blockers.append({"code": "route_chain_exception_needs_evidence", "message": "Route-chain exhaustion exceptions must carry concrete owner/Hub attempt evidence."})
    return {
        "schema": f"{SCHEMA}.exception",
        "ok": not blockers,
        "generated_at": now_iso(),
        "direct_web_allowed": not blockers,
        "reason": normalized,
        "reason_description": DIRECT_WEB_REASONS.get(normalized, ""),
        "resource_request_id": resource_request_id,
        "resource_status": resource_status,
        "evidence": evidence,
        "blockers": blockers,
    }


def validate() -> dict[str, Any]:
    cases = {
        "plan_requires_resource": plan("联网搜索相关知识"),
        "plan_allows_user_direct": plan("这次直接 web 搜索", direct_web_requested=False),
        "check_rejects_web_without_exception": check(web_used=True),
        "check_rejects_resource_deferred": check(web_used=True, resource_request_id="res_test", resource_status="deferred"),
        "check_rejects_resource_failed_without_chain": check(web_used=True, resource_request_id="res_test", resource_status="failed"),
        "check_rejects_incomplete_route_chain": check(web_used=True, resource_request_id="res_test", resource_status="failed", fallback_reason="predefined_online_route_exhausted", evidence="native_owner_failed;hub_owner_failed"),
        "check_allows_route_chain_exhausted": check(web_used=True, resource_request_id="res_test", resource_status="failed", fallback_reason="predefined_online_route_exhausted", evidence="native_owner_failed;hub_owner_failed;local_hub_not_applicable;owner_cli_not_applicable"),
        "check_allows_user_direct": check(web_used=True, user_direct_web=True),
        "check_allows_platform_required": check(
            web_used=True,
            platform_web_required=True,
            resource_request_id="batch_test",
            resource_status="completed",
        ),
        "check_rejects_platform_reason_without_flag": check(web_used=True, fallback_reason="higher_precedence_platform_web_required"),
        "check_rejects_running_resource": check(web_used=True, resource_request_id="res_test", resource_status="running"),
        "exception_rejects_unknown": exception("owner_mcp_insufficient"),
    }
    expectations = {
        "plan_requires_resource": bool(cases["plan_requires_resource"].get("resource_layer_required")) and not cases["plan_requires_resource"].get("direct_web_allowed"),
        "plan_allows_user_direct": bool(cases["plan_allows_user_direct"].get("direct_web_allowed")),
        "check_rejects_web_without_exception": not cases["check_rejects_web_without_exception"].get("ok"),
        "check_rejects_resource_deferred": not cases["check_rejects_resource_deferred"].get("ok"),
        "check_rejects_resource_failed_without_chain": not cases["check_rejects_resource_failed_without_chain"].get("ok"),
        "check_rejects_incomplete_route_chain": not cases["check_rejects_incomplete_route_chain"].get("ok"),
        "check_allows_route_chain_exhausted": bool(cases["check_allows_route_chain_exhausted"].get("ok")),
        "check_allows_user_direct": bool(cases["check_allows_user_direct"].get("ok")),
        "check_allows_platform_required": bool(cases["check_allows_platform_required"].get("ok"))
        and cases["check_allows_platform_required"].get("matched_reason") == "higher_precedence_platform_web_required",
        "check_rejects_platform_reason_without_flag": not cases["check_rejects_platform_reason_without_flag"].get("ok"),
        "check_rejects_running_resource": not cases["check_rejects_running_resource"].get("ok"),
        "exception_rejects_unknown": not cases["exception_rejects_unknown"].get("ok"),
    }
    issues = [
        {"severity": "risk", "code": f"{name}_failed", "case": cases[name]}
        for name, ok in expectations.items()
        if not ok
    ]
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "case_ok": expectations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only direct web access gate")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--message", required=True)
    p.add_argument("--direct-web-requested", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--web-used", action="store_true")
    c.add_argument("--resource-request-id", default="")
    c.add_argument("--resource-status", default="")
    c.add_argument("--fallback-reason", default="")
    c.add_argument("--user-direct-web", action="store_true")
    c.add_argument("--platform-web-required", action="store_true")
    c.add_argument("--evidence", default="")
    e = sub.add_parser("exception")
    e.add_argument("--reason", required=True)
    e.add_argument("--resource-request-id", default="")
    e.add_argument("--resource-status", default="")
    e.add_argument("--evidence", default="")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "plan":
        payload = plan(args.message, direct_web_requested=args.direct_web_requested)
    elif args.command == "check":
        payload = check(
            web_used=args.web_used,
            resource_request_id=args.resource_request_id,
            resource_status=args.resource_status,
            fallback_reason=args.fallback_reason,
            user_direct_web=args.user_direct_web,
            platform_web_required=args.platform_web_required,
            evidence=args.evidence,
        )
    elif args.command == "exception":
        payload = exception(
            args.reason,
            resource_request_id=args.resource_request_id,
            resource_status=args.resource_status,
            evidence=args.evidence,
        )
    elif args.command == "validate":
        payload = validate()
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    print_json(payload)
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
