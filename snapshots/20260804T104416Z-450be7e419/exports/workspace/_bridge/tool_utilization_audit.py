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
import importlib
import importlib.util
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.json_cli import configure_utf8_stdio, now_iso, print_json


BuildPlan = Callable[..., dict[str, Any]]
SkillPlan = Callable[[str], dict[str, Any]]

BRIDGE = Path(__file__).resolve().parent
EXTERNAL_DEPENDENCY_DIR = BRIDGE / "contracts" / "external_dependencies"


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


def _activation_case(
    *,
    source_id: str,
    capability_id: str,
    task: dict[str, Any],
    activation: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    usage = activation.get("usage_evidence") if isinstance(activation.get("usage_evidence"), dict) else {}
    return {
        "id": f"activation:{source_id}:{task.get('id') or sequence}",
        "profile_id": source_id,
        "capability_id": capability_id,
        "message": str(task["message"]),
        "expect_domain": str(task.get("expected_workflow_domain") or ""),
        "expect_skill": str(task.get("expected_skill") or ""),
        "expect_owner": str(task.get("expected_owner") or activation.get("owner") or ""),
        "expected_plan_path": str(task.get("expected_plan_path") or ""),
        "expected_plan_prefix": str(task.get("expected_plan_prefix") or ""),
        "minimum_applied": int(usage.get("minimum_applied") or 0),
        "usage_skill": str(usage.get("skill") or task.get("expected_skill") or ""),
        "runtime_evidence": activation.get("runtime_evidence", {}),
        "usage_evidence": activation.get("usage_evidence", {}),
        "result_consumption": str(activation.get("result_consumption") or ""),
        "reason": "declared member activation acceptance must prove placement and use",
    }


def declared_activation_cases(
    profile_dir: Path = EXTERNAL_DEPENDENCY_DIR,
    *,
    system_contracts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load representative activation tasks from existing dependency authorities."""

    cases: list[dict[str, Any]] = []
    for path in sorted(profile_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        profile_id = str(profile.get("profile_id") or path.stem)
        for capability in profile.get("capability_contracts", []):
            if not isinstance(capability, dict):
                continue
            activation = capability.get("activation_acceptance")
            if not isinstance(activation, dict):
                continue
            for task in activation.get("representative_tasks", []):
                if not isinstance(task, dict) or not str(task.get("message") or "").strip():
                    continue
                cases.append(_activation_case(
                    source_id=profile_id,
                    capability_id=str(capability.get("capability_id") or ""),
                    task=task,
                    activation=activation,
                    sequence=len(cases) + 1,
                ))
    include_member_registry = system_contracts is None
    membership_registry: list[dict[str, Any]] = []
    if system_contracts is None:
        try:
            import system_membership

            system_contracts = system_membership.CONTRACTS
            membership_registry = system_membership.MIRROR_MEMBER_REGISTRY
        except (ImportError, AttributeError):
            system_contracts = {}
    for system, contract in sorted(system_contracts.items()):
        activation = contract.get("activation_acceptance") if isinstance(contract, dict) else None
        if not isinstance(activation, dict):
            continue
        for task in activation.get("representative_tasks", []):
            if not isinstance(task, dict) or not str(task.get("message") or "").strip():
                continue
            cases.append(_activation_case(
                source_id=f"system-{system}",
                capability_id=str(activation.get("capability_id") or system),
                task=task,
                activation=activation,
                sequence=len(cases) + 1,
            ))
    if include_member_registry:
        for member in membership_registry:
            if not isinstance(member, dict):
                continue
            activation = member.get("activation_acceptance")
            if not isinstance(activation, dict):
                continue
            for task in activation.get("representative_tasks", []):
                if not isinstance(task, dict) or not str(task.get("message") or "").strip():
                    continue
                cases.append(_activation_case(
                    source_id=f"member-{member.get('member_id') or member.get('owner') or 'unknown'}",
                    capability_id=str(activation.get("capability_id") or member.get("member_id") or ""),
                    task=task,
                    activation=activation,
                    sequence=len(cases) + 1,
                ))
    return cases


_LOCAL_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _local_probe(spec: Any) -> dict[str, Any] | None:
    """Execute one explicitly declared, read-only owner probe.

    Activation profiles own the probe contract.  The audit only permits a
    simple local module/function resolved from this bridge directory and
    keyword arguments, so a profile cannot turn validation into arbitrary
    shell execution or an external write.
    """

    if not isinstance(spec, dict):
        return None
    module_name = str(spec.get("module") or "").strip()
    function_name = str(spec.get("function") or "").strip()
    if not _LOCAL_MODULE_NAME.fullmatch(module_name) or not _LOCAL_MODULE_NAME.fullmatch(function_name):
        return {"ok": False, "error": "probe_identity_invalid"}
    try:
        module_spec = importlib.util.find_spec(module_name)
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        return {"ok": False, "error": f"probe_module_unavailable:{type(exc).__name__}"}
    origin = Path(module_spec.origin).resolve() if module_spec and module_spec.origin else None
    if origin is None or BRIDGE not in origin.parents:
        return {"ok": False, "error": "probe_module_outside_bridge"}
    try:
        owner = importlib.import_module(module_name)
        function = getattr(owner, function_name)
        if not callable(function):
            return {"ok": False, "error": "probe_function_not_callable"}
        kwargs = spec.get("kwargs") if isinstance(spec.get("kwargs"), dict) else {}
        result = function(**kwargs)
    except Exception as exc:  # noqa: BLE001 - expose a bounded owner failure.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "result": result}


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _probe_expectations(result: Any, expectations: Any) -> tuple[bool, list[dict[str, Any]]]:
    if not isinstance(expectations, dict):
        return True, []
    checks: list[dict[str, Any]] = []
    for path, expected in expectations.items():
        if path == "results_min":
            actual = len(result.get("results", [])) if isinstance(result, dict) and isinstance(result.get("results"), list) else 0
            checks.append({"path": path, "ok": actual >= int(expected), "expected": f">={expected}", "actual": actual})
            continue
        elif path == "retrievers_contains":
            actual = _path_value(result, "retrievers")
            expected = [str(item) for item in expected] if isinstance(expected, list) else [str(expected)]
            checks.append({"path": path, "ok": all(item in (actual or []) for item in expected), "expected": expected, "actual": actual})
            continue
        else:
            actual = _path_value(result, path)
        checks.append({"path": path, "ok": actual == expected, "expected": expected, "actual": actual})
    return all(item["ok"] for item in checks), checks


def _contains_owner(value: Any, owner: str, *, budget: int = 400) -> bool:
    if not owner:
        return False
    if budget <= 0:
        return False
    if isinstance(value, dict):
        if any(value.get(key) == owner for key in ("owner", "source_authority", "primary_owner")):
            return True
        return any(_contains_owner(item, owner, budget=budget - 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_owner(item, owner, budget=budget - 1) for item in value[:40])
    return False


def evaluate_activation_case(
    workflow_plan: dict[str, Any],
    skill_plan: dict[str, Any],
    usage_summary: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    domains = _domain_keys(workflow_plan)
    selected_skills = {
        str(item.get("name") or "")
        for item in skill_plan.get("selected_skills", [])
        if isinstance(item, dict)
    }
    expected_domain = str(case.get("expect_domain") or "")
    expected_skill = str(case.get("expect_skill") or "")
    expected_owner = str(case.get("expect_owner") or "")
    usage_skill = str(case.get("usage_skill") or expected_skill)
    minimum_applied = int(case.get("minimum_applied") or 0)
    used_count = int(usage_summary.get("skills", {}).get(usage_skill, {}).get("applied") or 0)
    checks = [
        {
            "name": "representative_workflow_domain_selected",
            "ok": bool(expected_domain and expected_domain in domains),
            "expected": expected_domain,
            "actual": sorted(domains),
        },
        {
            "name": "runtime_evidence_owner_declared",
            "ok": bool(case.get("runtime_evidence", {}).get("owner")),
            "expected": "non-empty owner",
            "actual": case.get("runtime_evidence", {}),
        },
        {
            "name": "result_consumption_declared",
            "ok": bool(case.get("result_consumption")),
            "expected": "non-empty consumption predicate",
        },
    ]
    if expected_skill:
        checks.append({
            "name": "representative_skill_selected",
            "ok": expected_skill in selected_skills,
            "expected": expected_skill,
            "actual": sorted(selected_skills),
        })
    if expected_owner:
        checks.append({
            "name": "representative_owner_selected",
            "ok": _contains_owner(workflow_plan, expected_owner),
            "expected": expected_owner,
        })
    runtime = case.get("runtime_evidence") if isinstance(case.get("runtime_evidence"), dict) else {}
    runtime_probe = _local_probe(runtime.get("probe"))
    if runtime.get("probe") is not None:
        runtime_ok = bool(runtime_probe and runtime_probe.get("ok"))
        runtime_expect_ok, runtime_checks = _probe_expectations(
            runtime_probe.get("result") if runtime_probe else None,
            runtime.get("probe", {}).get("expect") if isinstance(runtime.get("probe"), dict) else {},
        )
        checks.append({
            "name": "runtime_probe_callable",
            "ok": runtime_ok and runtime_expect_ok,
            "expected": "declared local owner probe succeeds",
            "actual": {"probe": runtime_probe, "checks": runtime_checks},
        })
    usage = case.get("usage_evidence") if isinstance(case.get("usage_evidence"), dict) else {}
    usage_probe = _local_probe(usage.get("probe"))
    if usage.get("probe") is not None:
        usage_expect_ok, usage_checks = _probe_expectations(
            usage_probe.get("result") if usage_probe else None,
            usage.get("probe", {}).get("expect") if isinstance(usage.get("probe"), dict) else {},
        )
        checks.append({
            "name": "consumed_runtime_result",
            "ok": bool(usage_probe and usage_probe.get("ok") and usage_expect_ok),
            "expected": "declared production probe returns an accepted result",
            "actual": {"probe": usage_probe, "checks": usage_checks},
        })
    if minimum_applied:
        checks.append({
            "name": "consumed_usage_evidence_present",
            "ok": used_count >= minimum_applied,
            "expected": minimum_applied,
            "actual": used_count,
        })
    expected_plan_path = str(case.get("expected_plan_path") or "")
    if expected_plan_path:
        value: Any = workflow_plan
        for part in expected_plan_path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        prefix = str(case.get("expected_plan_prefix") or "")
        checks.append({
            "name": "declared_plan_surface_active",
            "ok": value is not None and (not prefix or str(value).startswith(prefix)),
            "expected": {"path": expected_plan_path, "prefix": prefix},
            "actual": value,
        })
    return {
        "id": case.get("id"),
        "profile_id": case.get("profile_id"),
        "capability_id": case.get("capability_id"),
        "ok": all(item["ok"] for item in checks),
        "message": case.get("message"),
        "reason": case.get("reason"),
        "checks": checks,
    }


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


def activation_acceptance_with_build_plan(
    build_plan: BuildPlan,
    *,
    skill_plan: SkillPlan | None = None,
    usage_summary: dict[str, Any] | None = None,
    activation_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    declared_cases = declared_activation_cases() if activation_cases is None else activation_cases
    if not declared_cases:
        return {
            "schema": "tool_utilization_audit.activation_acceptance.v1",
            "ok": True,
            "case_count": 0,
            "results": [],
            "underused": [],
        }
    skill_owner: Any = None
    if skill_plan is None:
        import skill_orchestrator as skill_owner

        routing_context = skill_owner.prepare_routing_context()

        def routed_skill_plan(message: str) -> dict[str, Any]:
            return skill_owner.build_plan(message, routing_context=routing_context)

        skill_plan = routed_skill_plan
    if usage_summary is None:
        if skill_owner is None:
            import skill_orchestrator as skill_owner

        quality = skill_owner.skill_lifecycle_state.quality_summary(limit=500)
        usage_summary = {"skills": quality.get("skills", {})}
    results = [
        evaluate_activation_case(
            build_plan(str(case["message"]), detail="full"),
            skill_plan(str(case["message"])),
            usage_summary,
            case,
        )
        for case in declared_cases
    ]
    underused = [item for item in results if not item.get("ok")]
    return {
        "schema": "tool_utilization_audit.activation_acceptance.v1",
        "ok": not underused,
        "case_count": len(results),
        "results": results,
        "underused": underused,
    }


def audit_with_build_plan(
    build_plan: BuildPlan,
    *,
    message: str = "",
    skill_plan: SkillPlan | None = None,
    usage_summary: dict[str, Any] | None = None,
    activation_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    activation = activation_acceptance_with_build_plan(
        build_plan,
        skill_plan=skill_plan,
        usage_summary=usage_summary,
        activation_cases=activation_cases,
    )
    activation_results = activation["results"]
    all_results = [*results, *activation_results]
    underused = [item for item in all_results if not item.get("ok")]
    return {
        "schema": "tool_utilization_audit.v1",
        "ok": not underused,
        "generated_at": now_iso(),
        "case_count": len(all_results),
        "declared_activation_case_count": activation["case_count"],
        "underused_count": len(underused),
        "results": all_results,
        "underused": underused,
        "rule": "A capability is underutilized when its declared representative task does not select its route and skill, or when no consumed use satisfies its activation profile.",
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
