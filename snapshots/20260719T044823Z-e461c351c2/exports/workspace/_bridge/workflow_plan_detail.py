#!/usr/bin/env python3
"""Detail-level projection helpers for workflow plans.

This module owns context-budget projections only. It does not classify tasks,
choose tools, execute commands, or mutate workflow state.
"""

from __future__ import annotations

from typing import Any

from bounded_output import bounded_payload


DETAIL_LEVELS = ("micro", "standard", "full")


def normalize_detail_level(value: str = "full") -> str:
    detail = str(value or "full").lower().strip()
    return detail if detail in DETAIL_LEVELS else "full"


def infer_detail_level(profile: dict[str, Any], selected_domains: list[dict[str, Any]], requested: str = "auto") -> str:
    requested_detail = str(requested or "auto").lower().strip()
    if requested_detail in DETAIL_LEVELS:
        return requested_detail
    domain_keys = [str(item.get("key") or "") for item in selected_domains]
    profile_name = str(profile.get("profile") or "")
    if str(profile.get("state_change_expected")).lower() == "true":
        return "full"
    if profile_name in {"repair_or_code_change", "external_action", "mobile_delegation"}:
        return "full"
    if "workflow_governance" in domain_keys:
        return "standard"
    if profile_name in {"diagnose_only", "research"}:
        return "standard"
    return "micro"


def compact_phase(phase: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": phase.get("id"),
        "owner": phase.get("owner"),
        "enabled": phase.get("enabled"),
        "skip_reason": phase.get("skip_reason"),
        "validation_tier": phase.get("validation_tier"),
        "read_only": phase.get("read_only"),
        "approval_required": phase.get("approval_required"),
        "commands": [
            {
                "cmd": command.get("cmd"),
                "read_only": command.get("read_only"),
                "required": command.get("required"),
            }
            for command in phase.get("commands", [])
            if isinstance(command, dict)
        ],
        "fallback": phase.get("fallback"),
        "validation": phase.get("validation"),
        "next_phase": phase.get("next_phase"),
    }


def micro_phase(phase: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": phase.get("id"),
        "owner": phase.get("owner"),
        "enabled": phase.get("enabled"),
        "skip_reason": phase.get("skip_reason"),
        "validation_tier": phase.get("validation_tier"),
        "approval_required": phase.get("approval_required"),
        "command_count": len(phase.get("commands", []) or []),
        "next_phase": phase.get("next_phase"),
    }


def skill_candidate_summary(skill_orchestration: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in skill_orchestration.get("selected_skills", [])[:limit]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "reasons": item.get("reasons", []),
            }
        )
    return output


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def compact_domains(domains: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.get("key"),
            "label": item.get("label"),
            "confidence": item.get("confidence"),
            "match_quality": item.get("match_quality"),
            "drives_execution": item.get("drives_execution"),
        }
        for item in domains
        if isinstance(item, dict)
    ]


def compact_memory(memory: dict[str, Any], detail: str) -> dict[str, Any]:
    route = _as_dict(memory.get("route"))
    layers = []
    for item in _as_list(route.get("layers")):
        if not isinstance(item, dict):
            continue
        layer = {
            "key": item.get("key"),
            "action": item.get("action"),
            "reason": item.get("reason"),
        }
        if detail == "standard":
            layer["command"] = item.get("command")
            layer["verify"] = item.get("verify")
        layers.append(layer)
    return {
        "primary": route.get("primary"),
        "domain_keys": _as_list(route.get("domain_keys")),
        "layers": layers,
        "rule": memory.get("rule"),
    }


def compact_call_priority(boundary: dict[str, Any], detail: str) -> dict[str, Any]:
    priority = _as_dict(boundary.get("call_priority"))
    steps = []
    for item in _as_list(priority.get("steps")):
        if not isinstance(item, dict):
            continue
        step = {"id": item.get("id"), "action": item.get("action")}
        step["on_failure_next_step"] = item.get("on_failure_next_step")
        if detail == "standard":
            step["continue_on"] = item.get("continue_on")
            step["stop_on"] = item.get("stop_on")
        if item.get("tools"):
            step["tools"] = item.get("tools")
        if detail == "standard":
            for key in ("query", "use_only_when", "use_only_after", "skip_allowed_only_if", "requires_evidence"):
                if item.get(key):
                    step[key] = item.get(key)
        steps.append(step)
    return {
        "profile": priority.get("profile"),
        "tool": priority.get("tool"),
        "capability": priority.get("capability"),
        "execution_affinity": priority.get("execution_affinity"),
        "session_binding": priority.get("session_binding"),
        "priority_source": priority.get("priority_source"),
        "priority_reason": priority.get("priority_reason"),
        "priority_explicit": priority.get("priority_explicit"),
        "required_first_step": priority.get("required_first_step"),
        "preferred_direct_hub_tool": priority.get("preferred_direct_hub_tool"),
        "direct_hub_tools": _as_list(priority.get("direct_hub_tools")),
        "complete_route_boundary": _as_dict(priority.get("complete_route_boundary")) if detail == "standard" else {},
        "steps": steps,
        "continuation_policy": _as_dict(priority.get("continuation_policy")) if detail == "standard" else {
            "direction": _as_dict(priority.get("continuation_policy")).get("direction"),
            "hub_failure_does_not_release_chain": _as_dict(priority.get("continuation_policy")).get("hub_failure_does_not_release_chain"),
        },
    }


def compact_resource_gate(gate: dict[str, Any], detail: str) -> dict[str, Any]:
    completion = _as_dict(gate.get("completion_contract"))
    owner_routes = []
    structured_seed = _as_dict(gate.get("structured_request_seed"))
    if detail == "micro" and structured_seed:
        resource = _as_dict(structured_seed.get("resource"))
        structured_seed = {
            "schema": structured_seed.get("schema"),
            "action": structured_seed.get("action"),
            "target": structured_seed.get("target"),
            "resource": {
                "kind": resource.get("kind"),
                "quantity": _as_dict(resource.get("quantity")),
                "materialization": _as_dict(resource.get("materialization")),
            },
            "complete": structured_seed.get("complete"),
            "errors": _as_list(structured_seed.get("errors")),
        }
    for item in _as_list(gate.get("owner_routes")):
        if not isinstance(item, dict):
            continue
        owner_routes.append(
            {
                "resource": item.get("resource"),
                "owner_mcp": item.get("owner_mcp"),
                "read_tools_first": _as_list(item.get("read_tools_first"))[:6],
            }
        )
    result = {
        "enabled": bool(gate.get("enabled")),
        "task_class": gate.get("task_class"),
        "next_action": gate.get("next_action"),
        "submit_entrypoint": gate.get("submit_entrypoint"),
        "structured_request_seed": structured_seed,
        "source_discovery_owner": gate.get("source_discovery_owner"),
        "candidate_review_before_materialization": gate.get("candidate_review_before_materialization"),
        "owner_routes": owner_routes,
        "completion": {
            "submit_required": completion.get("codex_must_submit_to_resource_layer"),
            "wait_for_receipt": completion.get("codex_waits_for_receipt"),
            "task_ends_at": completion.get("task_ends_at"),
            "consume_required_field": completion.get("consume_required_field"),
            "required_consume_paths_field": completion.get("required_consume_paths_field"),
            "completed_status": completion.get("completed_status"),
            "handoff_required_status": completion.get("handoff_required_status"),
            "deferred_status": completion.get("deferred_status"),
            "failed_or_blocked_status": completion.get("failed_or_blocked_status"),
            "progress_command": completion.get("progress_command"),
            "status_command": completion.get("status_command"),
        },
    }
    if detail == "standard":
        result["primary_command"] = _as_list(gate.get("primary_command"))
        result["primary_command_text"] = gate.get("primary_command_text")
        result["job_run_command"] = gate.get("job_run_command")
        result["rule"] = gate.get("rule")
        result["candidate_review_policy"] = gate.get("candidate_review_policy")
        result["fallback_reasons_for_generic_web"] = gate.get("fallback_reasons_for_generic_web")
    return result


def compact_structured_route(route: dict[str, Any], detail: str) -> dict[str, Any]:
    decision = _as_dict(route.get("route_decision"))
    delegation = _as_dict(route.get("resource_delegation"))
    result = {
        "input_mode": route.get("input_mode"),
        "primary_domain": route.get("primary_domain"),
        "profile": route.get("profile"),
        "validation_tier": route.get("validation_tier"),
        "state_change_expected": route.get("state_change_expected"),
        "required_next_action": decision.get("required_next_action"),
        "resource_delegation": {
            "required": delegation.get("required"),
            "task_class": delegation.get("task_class"),
            "source_discovery_owner": delegation.get("source_discovery_owner"),
            "candidate_review_before_materialization": delegation.get("candidate_review_before_materialization"),
        },
        "downstream_rule": route.get("downstream_rule"),
    }
    if detail == "standard":
        result["task_contract"] = route.get("task_contract")
        result["domain_keys"] = _as_list(route.get("domain_keys"))
        result["route_decision"] = compact_route_decision(decision, detail)
    return result


def compact_required_gate(gate: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema": gate.get("schema"),
        "triggered": gate.get("triggered"),
        "fact": gate.get("fact"),
        "required": gate.get("required"),
        "owner": gate.get("owner"),
        "completion": gate.get("completion"),
        "pre_change_command": _as_dict(gate.get("pre_change")).get("command"),
        "post_change_command": _as_dict(gate.get("post_change")).get("command"),
        "closeout_command": _as_dict(gate.get("closeout")).get("command"),
        "activation_rule": gate.get("activation_rule"),
        "stop_if": _as_list(gate.get("stop_if")),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def compact_route_decision(decision: dict[str, Any], detail: str) -> dict[str, Any]:
    ambiguity = _as_dict(decision.get("ambiguity"))
    owner_route = _as_dict(decision.get("owner_route"))
    result = {
        "task_facts": {
            str(key): bool(value)
            for key, value in _as_dict(decision.get("task_facts")).items()
            if bool(value)
        },
        "required_gates": [
            compact_required_gate(item)
            for item in _as_list(decision.get("required_gates"))
            if isinstance(item, dict)
        ],
        "policy_decisions": [
            {
                "rule_id": item.get("rule_id"),
                "decision": item.get("decision"),
                "enforcement_point": item.get("enforcement_point"),
                "trigger_fact": item.get("trigger_fact"),
            }
            for item in _as_list(decision.get("policy_decisions"))
            if isinstance(item, dict) and item.get("rule_id")
        ],
        "stop_if": _as_list(decision.get("stop_if")),
        "task_mode": decision.get("task_mode"),
        "primary_owner": decision.get("primary_owner"),
        "evidence_owner": decision.get("evidence_owner"),
        "required_next_action": decision.get("required_next_action"),
        "primary_domain": decision.get("primary_domain"),
        "confidence": decision.get("confidence"),
        "match_quality": decision.get("match_quality"),
        "ambiguity": {
            "is_ambiguous": ambiguity.get("is_ambiguous"),
            "resolution": ambiguity.get("resolution"),
        },
        "resource_delegation_required": decision.get("resource_delegation_required"),
        "mcp_priority_required": decision.get("mcp_priority_required"),
        "owner_route": {
            "mcp_profile": owner_route.get("mcp_profile"),
            "tool": owner_route.get("tool"),
            "capability": owner_route.get("capability"),
            "owner_profile": owner_route.get("owner_profile"),
            "hub_tool": owner_route.get("hub_tool"),
            "native_tool": owner_route.get("native_tool"),
            "execution_affinity": owner_route.get("execution_affinity"),
            "session_binding": owner_route.get("session_binding"),
        },
    }
    if detail == "standard":
        result["task_contract"] = decision.get("task_contract")
        result["evidence_required"] = _as_list(decision.get("evidence_required"))
        result["generic_web"] = decision.get("generic_web")
    return result


def compact_execution_route_pack(plan: dict[str, Any], detail: str) -> dict[str, Any]:
    pack = _as_dict(plan.get("execution_route_pack"))
    decision = _as_dict(pack.get("route_decision"))
    resource_gate = _as_dict(pack.get("resource_gate"))
    boundary = _as_dict(pack.get("mcp_boundary"))
    network_gate = _as_dict(pack.get("network_gate"))
    capsules: list[dict[str, Any]] = []
    if resource_gate.get("enabled") or decision.get("resource_delegation_required"):
        capsules.append({"kind": "resource", "contract": compact_resource_gate(resource_gate, detail)})
    priority = _as_dict(boundary.get("call_priority"))
    if decision.get("mcp_priority_required") or priority.get("profile"):
        capsules.append({"kind": "mcp", "contract": compact_call_priority(boundary, detail)})
    if network_gate.get("entrypoint"):
        capsules.append({"kind": "network", "contract": network_gate})
    policies = []
    for item in _as_list(pack.get("tool_policies")):
        if not isinstance(item, dict):
            continue
        policy = {"key": item.get("key"), "validation": item.get("validation")}
        if detail == "standard":
            policy["query_rule"] = item.get("query_rule")
            policy["evidence_required"] = item.get("evidence_required")
        policies.append(policy)
    result = {
        "schema": "execution_route_pack.projection.v2",
        "source_schema": pack.get("schema"),
        "projection": detail,
        "ok": pack.get("ok"),
        "next_phase": pack.get("next_phase"),
        "domain_keys": _as_list(pack.get("domain_keys")),
        "memory": compact_memory(_as_dict(pack.get("memory")), detail),
        "route_decision": compact_route_decision(decision, detail),
        "active_policies": policies,
        "capsules": capsules,
        "validation": pack.get("validation"),
        "stop_if": pack.get("stop_if"),
        "expand": {"standard": "--detail standard", "full": "--detail full"},
    }
    if detail == "standard" and any(item.get("kind") == "resource" for item in capsules):
        result["external_research_gate"] = pack.get("external_research_gate")
    return result


def compact_environment_context(context: dict[str, Any], detail: str) -> dict[str, Any]:
    """Project derived environment knowledge without copying its owner inventories."""

    if not context:
        return {}
    systems = _as_list(context.get("relevant_systems"))
    architecture = _as_list(context.get("architecture_chain"))

    def bounded_systems(limit: int) -> list[dict[str, Any]]:
        rows = [item for item in systems if isinstance(item, dict)]
        selected = rows[:limit]
        workflow = next((item for item in rows if item.get("system") == "workflow"), None)
        if workflow and workflow not in selected:
            selected = [*selected[: max(0, limit - 1)], workflow]
        return selected

    if detail == "micro":
        return {
            "schema": context.get("schema"),
            "ok": context.get("ok"),
            "architecture_chain": [
                item.get("layer") for item in architecture if isinstance(item, dict) and item.get("layer")
            ],
            "relevant_systems": [
                {
                    "system": item.get("system"),
                    "role": str(item.get("role") or "")[:140],
                }
                for item in bounded_systems(4)
            ],
            "tool_entrypoints": _as_list(context.get("tool_entrypoints"))[:2],
            "relationships": _as_list(context.get("relationships"))[:2],
            "source_refs": _as_list(context.get("source_refs"))[:4],
            "issues": _as_list(context.get("issues"))[:2],
            "expand": _as_list(context.get("expansion_commands"))[:1],
        }
    if detail == "standard":
        standard_systems = []
        for item in bounded_systems(5):
            member = next(
                (member for member in _as_list(item.get("selected_members")) if isinstance(member, dict)),
                {},
            )
            standard_systems.append(
                {
                    "system": item.get("system"),
                    "role": str(item.get("role") or "")[:180],
                    "member": member.get("member"),
                    "member_role": str(member.get("responsibility") or "")[:180],
                    "member_source": member.get("source"),
                    "expand": item.get("expand"),
                }
            )
        return {
            "schema": context.get("schema"),
            "ok": context.get("ok"),
            "architecture_chain": [
                item.get("layer") for item in architecture if isinstance(item, dict) and item.get("layer")
            ],
            "relevant_systems": standard_systems,
            "tool_entrypoints": _as_list(context.get("tool_entrypoints"))[:3],
            "mcp_routes": [
                {
                    "capability": item.get("capability"),
                    "execution_affinity": item.get("execution_affinity"),
                    "required_first_step": item.get("required_first_step"),
                    "source": item.get("source"),
                }
                for item in _as_list(context.get("mcp_routes"))[:2]
                if isinstance(item, dict)
            ],
            "relationships": _as_list(context.get("relationships"))[:3],
            "source_refs": _as_list(context.get("source_refs"))[:6],
            "issues": _as_list(context.get("issues"))[:3],
            "expand": _as_list(context.get("expansion_commands"))[:2],
        }
    return context


def compact_retirement_guard(plan: dict[str, Any]) -> dict[str, Any]:
    guard = _as_dict(plan.get("retirement_guard"))
    if not guard:
        return {}
    result = {
        "triggered": guard.get("triggered"),
        "status": guard.get("status"),
        "directive": guard.get("directive"),
    }
    if not guard.get("triggered"):
        return result
    result.update(
        {
            "do_not_route": _as_list(guard.get("do_not_route")),
            "do_not_invoke": _as_list(guard.get("do_not_invoke")),
            "do_not_generate": _as_list(guard.get("do_not_generate")),
            "do_not_recommend": _as_list(guard.get("do_not_recommend")),
            "do_not_repair_or_restore": _as_list(guard.get("do_not_repair_or_restore")),
            "use_replacement": _as_dict(guard.get("use_replacement")),
            "purge_surfaces": _as_list(guard.get("purge_surfaces")),
            "proof_surfaces": _as_list(guard.get("proof_surfaces")),
            "required_surfaces": _as_list(guard.get("required_surfaces")),
            "closure_actions": _as_list(guard.get("closure_actions")),
            "codex_instructions": _as_list(guard.get("codex_instructions")),
            "active_trace_issues": _as_list(guard.get("active_trace_issues"))[:12],
            "membership_rule": guard.get("membership_rule"),
        }
    )
    return result


def projected_plan(plan: dict[str, Any], detail: str, phases: list[dict[str, Any]]) -> dict[str, Any]:
    profile = _as_dict(plan.get("profile"))
    execution = _as_dict(plan.get("execution_plan"))
    skills = _as_dict(plan.get("skills"))
    slash = _as_dict(plan.get("slash_templates"))
    return {
        "schema": plan.get("schema"),
        "ok": plan.get("ok"),
        "generated_at": plan.get("generated_at"),
        "message": plan.get("message"),
        "risk": plan.get("risk"),
        "retirement_guard": compact_retirement_guard(plan),
        "profile": {
            "profile": profile.get("profile"),
            "validation_tier": profile.get("validation_tier"),
            "state_change_expected": profile.get("state_change_expected"),
        },
        "domains": compact_domains(_as_list(plan.get("domains"))),
        "structured_route": compact_structured_route(_as_dict(plan.get("structured_route")), detail),
        "workflow": plan.get("workflow", [])[:6],
        "memory": compact_memory(_as_dict(plan.get("memory")), detail),
        "skills": {
            "selected": _as_list(skills.get("selected")),
            "read_policy": "candidate_summary_first; read full SKILL.md only when selected",
        },
        "skill_orchestration": {
            "ok": _as_dict(plan.get("skill_orchestration")).get("ok"),
            "selected_skills": skill_candidate_summary(_as_dict(plan.get("skill_orchestration")), limit=3),
            "gap_proposals": _as_list(_as_dict(plan.get("skill_orchestration")).get("gap_proposals"))[:2],
        },
        "slash_templates": {
            "selected": _as_list(slash.get("selected")),
            "missing": _as_list(slash.get("missing")),
        },
        "execution_route_pack": compact_execution_route_pack(plan, detail),
        "environment_context": compact_environment_context(
            _as_dict(_as_dict(plan.get("execution_route_pack")).get("environment_context")),
            detail,
        ),
        "machine_phases": phases,
        "execution_plan": {
            "active_phase_ids": _as_list(execution.get("active_phase_ids")),
            "profile": execution.get("profile"),
            "validation_tier": execution.get("validation_tier"),
            "state_change_expected": execution.get("state_change_expected"),
        },
        "validation_tiers": {
            "chosen": _as_dict(_as_dict(plan.get("execution_route_pack")).get("validation")).get("chosen_tier"),
            "quick": _as_list(_as_dict(plan.get("validation_tiers")).get("quick"))[:4],
        },
        "complexity_budget": plan.get("complexity_budget"),
        "detail_level": detail,
        "context_budget_policy": plan.get("context_budget_policy"),
        "expand": {"standard": "--detail standard", "full": "--detail full"},
    }


def apply_detail_level(plan: dict[str, Any], detail_level: str) -> dict[str, Any]:
    detail = normalize_detail_level(detail_level)
    plan["detail_level"] = detail
    plan["context_budget_policy"] = {
        "detail_levels": list(DETAIL_LEVELS),
        "rule": "micro keeps routing summary only; standard keeps compact phase commands; full keeps complete machine contract",
    }
    if detail == "full":
        return plan

    phases = [phase for phase in plan.get("machine_phases", []) if isinstance(phase, dict)]
    if detail == "standard":
        projected = projected_plan(plan, detail, [compact_phase(phase) for phase in phases])
        return bounded_payload(
            projected,
            max_bytes=18 * 1024,
            max_items=30,
            max_string=1200,
            preserve_keys=(
                "schema",
                "ok",
                "generated_at",
                "detail_level",
                "profile",
                "domains",
                "structured_route",
                "execution_route_pack",
                "environment_context",
                "skill_orchestration",
                "machine_phases",
            ),
        )

    plan["workflow"] = [
        "classify domain",
        "delegate complete, low-risk, verifiable, repeatable execution to the owning environment tool",
        "use one primary memory/tool layer first",
        "validate with smallest relevant check",
        "close out only changed facts/proposals",
    ]
    projected = projected_plan(plan, detail, [micro_phase(phase) for phase in phases if phase.get("enabled")])
    return bounded_payload(
        projected,
        max_bytes=8 * 1024,
        max_items=16,
        max_string=500,
        preserve_keys=("schema", "ok", "generated_at", "detail_level", "profile", "domains", "structured_route", "execution_route_pack", "environment_context", "skill_orchestration"),
    )
