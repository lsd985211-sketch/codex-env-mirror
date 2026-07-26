#!/usr/bin/env python3
"""Tool utilization audit for workflow route plans.

Ownership: read-only audit of whether high-value tools naturally enter the
workflow route pack when task semantics call for them.
Non-goals: executing tools, mutating route policy, replacing validators, or
measuring every shell command.
State behavior: deterministic from workflow plans plus optional current message.
Caller context: explicit routing-maintainer diagnostics and user-requested tool
utilization reviews; never routine task admission or closeout.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from shared.json_cli import configure_utf8_stdio, now_iso, print_json


BuildPlan = Callable[..., dict[str, Any]]


AUDIT_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "codegraph_for_system_governance",
        "message": "优化未充分利用工具的触发、路由、指标和验证机制",
        "expect_domain": "workflow_governance",
        "expect_policy": ("codegraph_policy", "maintenance_upgrade_policy"),
        "expect_phase": "phase_6_module_context",
        "reason": "system governance and route changes need source impact evidence, not only rg/manual reads",
    },
    {
        "id": "sqlite_for_structured_state",
        "message": "查询邮件回信附件处理状态和资源回执",
        "expect_policy": ("structured_state_policy",),
        "reason": "queue, mail, receipt, and indexed record status should prefer SQLite/indexed state before logs",
    },
    {
        "id": "resource_layer_for_external_research",
        "message": "联网搜索相关成熟知识，完善工具利用机制",
        "expect_domain": "external_docs_research",
        "expect_resource_gate": True,
        "reason": "external research should start as a resource-layer job and receipt",
    },
    {
        "id": "network_gateway_for_resource_work",
        "message": "资源层获取资源速度慢，优化网络层路线和下载策略",
        "expect_policy": ("network_policy",),
        "reason": "resource acquisition performance depends on gateway route/env/lease evidence",
    },
    {
        "id": "memory_for_memory_governance",
        "message": "目前记忆系统的利用方式是什么，继续优化记忆治理",
        "expect_domain": "memory",
        "expect_memory_primary": True,
        "reason": "memory work should route through memory governance instead of ad hoc file scans",
    },
    {
        "id": "browser_for_runtime_ui",
        "message": "打开浏览器检查页面 DOM 并截图验证",
        "expect_domain": "gui_browser",
        "expect_mcp_profile_any": ("chrome-devtools|playwright", "chrome-devtools", "playwright"),
        "reason": "runtime UI evidence needs browser/devtools routes instead of static source guesses",
    },
)


def _domain_keys(plan: dict[str, Any]) -> set[str]:
    return {str(item.get("key") or "") for item in plan.get("domains", []) if isinstance(item, dict)}


def _policy_enabled(plan: dict[str, Any], key: str) -> bool:
    policy = plan.get("tools", {}).get(key, {})
    return isinstance(policy, dict) and bool(policy.get("enabled"))


def _phase_enabled(plan: dict[str, Any], phase_id: str) -> bool:
    for phase in plan.get("machine_phases", []):
        if isinstance(phase, dict) and phase.get("id") == phase_id:
            return bool(phase.get("enabled"))
    return False


def _route_pack(plan: dict[str, Any]) -> dict[str, Any]:
    pack = plan.get("execution_route_pack", {})
    return pack if isinstance(pack, dict) else {}


def evaluate_plan(plan: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    domains = _domain_keys(plan)
    pack = _route_pack(plan)
    decision = pack.get("route_decision", {}) if isinstance(pack.get("route_decision"), dict) else {}
    mcp_boundary = pack.get("mcp_boundary", {}) if isinstance(pack.get("mcp_boundary"), dict) else {}
    owner_route = decision.get("owner_route", {}) if isinstance(decision.get("owner_route"), dict) else {}
    checks: list[dict[str, Any]] = []

    expected_domain = str(case.get("expect_domain") or "")
    if expected_domain:
        checks.append({"name": "domain_present", "ok": expected_domain in domains, "expected": expected_domain})

    for policy_key in case.get("expect_policy", ()):
        checks.append({"name": f"{policy_key}_enabled", "ok": _policy_enabled(plan, str(policy_key)), "expected": policy_key})

    expected_phase = str(case.get("expect_phase") or "")
    if expected_phase:
        checks.append({"name": "phase_enabled", "ok": _phase_enabled(plan, expected_phase), "expected": expected_phase})

    if case.get("expect_resource_gate"):
        resource_gate = pack.get("resource_gate", {}) if isinstance(pack.get("resource_gate"), dict) else {}
        checks.append({"name": "resource_gate_enabled", "ok": bool(resource_gate.get("enabled")), "expected": True})
        checks.append(
            {
                "name": "resource_gate_blocks_generic_web",
                "ok": resource_gate.get("generic_web_allowed") is False,
                "expected": False,
            }
        )

    if case.get("expect_memory_primary"):
        memory_route = plan.get("memory", {}).get("route", {}) if isinstance(plan.get("memory"), dict) else {}
        checks.append(
            {
                "name": "memory_primary_selected",
                "ok": bool(memory_route.get("primary")),
                "expected": "memory route primary",
            }
        )

    expected_profiles = tuple(str(item) for item in case.get("expect_mcp_profile_any", ()))
    if expected_profiles:
        profile = str(owner_route.get("mcp_profile") or mcp_boundary.get("call_priority", {}).get("profile") or "")
        checks.append({"name": "mcp_profile_matches", "ok": profile in expected_profiles, "expected": expected_profiles, "actual": profile})

    ok = all(bool(item.get("ok")) for item in checks)
    return {
        "id": case.get("id"),
        "ok": ok,
        "message": case.get("message"),
        "reason": case.get("reason"),
        "domains": sorted(domains),
        "primary_domain": decision.get("primary_domain"),
        "required_next_action": decision.get("required_next_action"),
        "checks": checks,
    }


def audit_with_build_plan(build_plan: BuildPlan, *, message: str = "") -> dict[str, Any]:
    cases = list(AUDIT_CASES)
    if message:
        cases.append(
            {
                "id": "current_message",
                "message": message,
                "reason": "current user request should expose the tools implied by its route pack",
            }
        )
    # Policy audits inspect the canonical contract. Compact plans are execution
    # projections and intentionally omit inactive policy detail.
    results = [evaluate_plan(build_plan(str(case["message"]), detail="full"), case) for case in cases]
    underused = [item for item in results if not item.get("ok")]
    return {
        "schema": "tool_utilization_audit.v1",
        "ok": not underused,
        "generated_at": now_iso(),
        "case_count": len(results),
        "underused_count": len(underused),
        "results": results,
        "underused": underused,
        "rule": "A tool is underutilized when the route plan omits it for a task class where it is the owning evidence or execution layer.",
    }


def validate_with_build_plan(build_plan: BuildPlan) -> dict[str, Any]:
    payload = audit_with_build_plan(build_plan)
    return {
        "schema": "tool_utilization_audit.validate.v1",
        "ok": payload.get("ok"),
        "generated_at": now_iso(),
        "audit": payload,
    }


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Audit whether workflow plans naturally use high-value tools.")
    parser.add_argument("command", choices=("audit", "validate"))
    parser.add_argument("--message", default="")
    args = parser.parse_args()
    from workflow_orchestrator import build_plan

    payload = validate_with_build_plan(build_plan) if args.command == "validate" else audit_with_build_plan(build_plan, message=args.message)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
