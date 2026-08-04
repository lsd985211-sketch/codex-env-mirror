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


def standard_phase(phase: dict[str, Any]) -> dict[str, Any]:
    """Keep standard plans actionable without duplicating full commands."""

    result = micro_phase(phase)
    result["commands"] = [
        {
            "cmd": command.get("cmd"),
            "required": command.get("required"),
            "read_only": command.get("read_only"),
        }
        for command in _as_list(phase.get("commands"))[:2]
        if isinstance(command, dict)
    ]
    return result


def required_command_contract(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep exact required owner commands executable even in micro projections."""

    return [
        {
            "phase_id": phase.get("id"),
            "owner": phase.get("owner"),
            "cmd": command.get("cmd"),
            "read_only": command.get("read_only"),
            "required": True,
        }
        for phase in phases
        if phase.get("enabled")
        for command in _as_list(phase.get("commands"))
        if isinstance(command, dict) and command.get("required")
    ]


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
            "systems": _as_list(item.get("systems")),
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
    delegation = _as_dict(route.get("resource_delegation"))
    contract = _as_dict(route.get("task_contract"))
    contract_facts = _as_dict(contract.get("task_facts"))
    result = {
        "input_mode": route.get("input_mode"),
        "primary_domain": route.get("primary_domain"),
        "profile": route.get("profile"),
        "validation_tier": route.get("validation_tier"),
        "state_change_expected": route.get("state_change_expected"),
        "route_decision_ref": route.get("route_decision_ref") or "execution_route_pack.route_decision",
        "asset_guidance_ref": route.get("asset_guidance_ref") or "execution_route_pack.asset_guidance",
        "resource_delegation": {
            "required": delegation.get("required"),
            "task_class": delegation.get("task_class"),
            "source_discovery_owner": delegation.get("source_discovery_owner"),
            "candidate_review_before_materialization": delegation.get("candidate_review_before_materialization"),
        },
        "downstream_rule": route.get("downstream_rule"),
    }
    if detail == "standard":
        result["task_contract"] = {
            "schema": contract.get("schema"),
            "task_mode": contract.get("task_mode"),
            "business_owner": contract.get("business_owner"),
            "evidence_owner": contract.get("evidence_owner"),
            "true_facts": [str(key) for key, value in contract_facts.items() if value],
            "required_next_action": contract.get("required_next_action"),
        }
        result["domain_keys"] = _as_list(route.get("domain_keys"))
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


def compact_route_decision(decision: dict[str, Any], detail: str, task_facts: dict[str, Any] | None = None) -> dict[str, Any]:
    ambiguity = _as_dict(decision.get("ambiguity"))
    owner_route = _as_dict(decision.get("owner_route"))
    result = {
        "task_facts": {
            str(key): bool(value)
            for key, value in _as_dict(task_facts).items()
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
        result["task_contract_ref"] = "structured_route.task_contract"
        result["evidence_required"] = _as_list(decision.get("evidence_required"))
        result["generic_web"] = decision.get("generic_web")
    return result


def compact_execution_route_pack(plan: dict[str, Any], detail: str) -> dict[str, Any]:
    pack = _as_dict(plan.get("execution_route_pack"))
    decision = _as_dict(pack.get("route_decision"))
    task_contract = _as_dict(_as_dict(plan.get("structured_route")).get("task_contract"))
    resource_gate = _as_dict(pack.get("resource_gate"))
    boundary = _as_dict(pack.get("mcp_boundary"))
    network_gate = _as_dict(pack.get("network_gate"))
    terminal = _as_dict(pack.get("terminal_convergence"))
    authorization = _as_dict(pack.get("authorization"))
    decision_authority = _as_dict(pack.get("decision_authority"))
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
        "route_decision": compact_route_decision(decision, detail, _as_dict(task_contract.get("task_facts"))),
        "asset_guidance": compact_asset_guidance(_as_dict(pack.get("asset_guidance")), detail),
        "environment_context": compact_environment_context(_as_dict(pack.get("environment_context")), detail),
        "authorization": compact_authorization(authorization, detail),
        "decision_authority": compact_decision_authority(decision_authority, detail),
        "terminal_convergence": compact_terminal_convergence(terminal, detail),
        "active_policies": policies,
        "capsules": capsules,
        "validation": pack.get("validation"),
        "stop_if": pack.get("stop_if"),
        "expand": {"standard": "--detail standard", "full": "--detail full"},
    }
    if detail == "micro":
        # The asset path is the admission payload; the full route contract is
        # available through --detail standard/full when needed. Keep only the
        # environment orientation needed to enter the existing authorities.
        result = {
            "schema": result["schema"],
            "projection": result["projection"],
            "domain_keys": result["domain_keys"],
            "route_decision": {
                "task_facts": result["route_decision"].get("task_facts", {}),
                "required_gates": result["route_decision"].get("required_gates", []),
                "policy_decisions": result["route_decision"].get("policy_decisions", []),
                "stop_if": result["route_decision"].get("stop_if", []),
                "required_next_action": result["route_decision"].get("required_next_action"),
                "primary_domain": result["route_decision"].get("primary_domain"),
            },
            "asset_guidance": result.get("asset_guidance", {}),
            "environment_context": result.get("environment_context", {}),
            "authorization": result.get("authorization", {}),
            "decision_authority": result.get("decision_authority", {}),
            "terminal_convergence": result.get("terminal_convergence", {}),
        }
        return result
    if detail == "standard" and any(item.get("kind") == "resource" for item in capsules):
        result["external_research_gate"] = pack.get("external_research_gate")
    return result


def compact_decision_authority(value: dict[str, Any], detail: str) -> dict[str, Any]:
    """Keep authority classes and unresolved blockers visible when compressed."""

    if not value:
        return {}

    def compact_row(item: dict[str, Any]) -> dict[str, Any]:
        row = {
            "decision_id": item.get("decision_id"),
            "decision_class": item.get("decision_class"),
            "enforcement": item.get("enforcement"),
            "blocking": item.get("blocking"),
            "effective_outcome": item.get("effective_outcome"),
            "authority_ref": item.get("authority_ref"),
            "next_action": item.get("next_action"),
        }
        if detail != "micro":
            row["override_authority"] = item.get("override_authority")
            row["required_evidence"] = _as_list(item.get("required_evidence"))
            row["input_signature"] = item.get("input_signature")
            row["policy_signature"] = item.get("policy_signature")
        return row

    result = {
        "schema": value.get("schema"),
        "authority": value.get("authority"),
        "blocking": value.get("blocking"),
        "recommendations": [compact_row(item) for item in _as_list(value.get("recommendations")) if isinstance(item, dict)],
        "authorization_requirements": [compact_row(item) for item in _as_list(value.get("authorization_requirements")) if isinstance(item, dict)],
        "hard_gates": [compact_row(item) for item in _as_list(value.get("hard_gates")) if isinstance(item, dict)],
        "consumed_overrides": [compact_row(item) for item in _as_list(value.get("consumed_overrides")) if isinstance(item, dict)],
        "unresolved": [compact_row(item) for item in _as_list(value.get("unresolved")) if isinstance(item, dict)],
        "signature": value.get("signature"),
    }
    if detail == "micro":
        # hard_gates and unresolved contain the same blocking rows until a
        # decision is satisfied. Keep the actionable unresolved view once.
        result.pop("hard_gates", None)
    return result


def compact_authorization(value: dict[str, Any], detail: str) -> dict[str, Any]:
    """Keep the decision boundary visible without copying owner policy."""

    if not value:
        return {}
    snapshot = _as_dict(value.get("environment_snapshot"))
    compact = {
        "schema": value.get("schema"),
        "required": value.get("required"),
        "status": value.get("status"),
        "effective_decision": value.get("effective_decision"),
        "required_facts": _as_list(value.get("required_facts")),
        "authority": value.get("authority"),
        "environment_signature": value.get("environment_signature"),
        "workflow_semantic_hash": snapshot.get("workflow_semantic_hash"),
        "required_next_action": value.get("required_next_action"),
        "automatic_expansion_allowed": False,
    }
    if detail != "micro":
        compact["inspect_entrypoint"] = value.get("inspect_entrypoint")
        compact["permit_rule"] = value.get("permit_rule")
        compact["environment_snapshot"] = snapshot
    return compact


def compact_terminal_convergence(value: dict[str, Any], detail: str) -> dict[str, Any]:
    """Keep one terminal decision in micro and explanation in standard."""

    if not value or not value.get("relevant"):
        return {"schema": "terminal_convergence.projection.v1", "relevant": False}
    compact = {
        "schema": value.get("schema"),
        "relevant": True,
        "convergence_id": value.get("convergence_id"),
        "current_phase": value.get("current_phase"),
        "terminal_goal": value.get("terminal_goal"),
        "terminal_source_signature": value.get("terminal_source_signature"),
        "next_action": value.get("next_action"),
        "reuse_count": len(_as_list(value.get("completed_action_ids"))),
        "invalidated_action_ids": _as_list(value.get("invalidated_action_ids")),
        "stop_if": [
            "terminal_action_graph_invalid",
            "terminal_signature_changed_after_mutation",
            "duplicate_terminal_mutation",
        ],
        "expand": "--detail standard",
    }
    if detail == "micro":
        return compact
    return {
        **compact,
        "intent_id": value.get("intent_id"),
        "ordered_actions": _as_list(value.get("ordered_actions")),
        "completed_receipts": _as_list(value.get("completed_receipts")),
        "invalidated_receipts": _as_list(value.get("invalidated_receipts")),
        "mutation_barrier": value.get("mutation_barrier"),
        "verification_barrier": value.get("verification_barrier"),
        "automatic_loop_allowed": value.get("automatic_loop_allowed"),
    }


def compact_asset_guidance(guidance: dict[str, Any], detail: str) -> dict[str, Any]:
    """Keep proactive guidance useful within micro/standard route budgets."""

    if not guidance:
        return {}

    def rows(kind: str, limit: int) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in _as_list(guidance.get(kind))[:limit]:
            if not isinstance(item, dict):
                continue
            row = {
                "name": item.get("name"),
                "mode": item.get("mode"),
                "action": item.get("action"),
            }
            if kind == "tools":
                row["use_for"] = item.get("use_for")
                row["skip_when"] = item.get("skip_when")
            elif detail == "standard":
                row["reason"] = item.get("reason")
            output.append(row)
        return output

    frame = _as_dict(guidance.get("environment_decision_frame"))
    candidates = [
        item for item in _as_list(frame.get("candidates"))[:3]
        if isinstance(item, dict)
    ]
    candidate_refs = list(dict.fromkeys(
        str(item.get("authority_ref") or "")
        for item in candidates
        if str(item.get("authority_ref") or "")
    ))
    probe_refs = list(dict.fromkeys(
        str(_as_dict(item.get("disambiguation")).get("probe_ref") or item.get("expand_ref") or "")
        for item in candidates
        if str(_as_dict(item.get("disambiguation")).get("probe_ref") or item.get("expand_ref") or "")
    ))
    compact_frame: dict[str, Any] = {
        "schema": frame.get("schema"),
        "decision_mode": frame.get("decision_mode"),
        "post_gate_mode": frame.get("post_gate_mode"),
        "hard_gate_count": len(_as_list(frame.get("hard_gates"))),
        "candidate_refs": candidate_refs,
        "probe_refs": probe_refs,
        "judgment_rule": frame.get("judgment_rule"),
        "expand": "--detail standard" if detail == "micro" else "--detail full",
    }
    if detail == "micro":
        # The mode plus stable authority/probe references are sufficient to
        # enter the next layer; the explanatory rule remains in standard/full.
        compact_frame.pop("judgment_rule", None)
    if detail == "standard":
        compact_frame["hard_gates"] = [
            {
                "fact": item.get("fact"),
                "authority_ref": item.get("authority_ref"),
                "enforcement_ref": item.get("enforcement_ref"),
            }
            for item in _as_list(frame.get("hard_gates"))[:4]
            if isinstance(item, dict)
        ]
        compact_frame["candidates"] = [
            {
                "id": item.get("id"),
                "system": item.get("system"),
                "authority_ref": item.get("authority_ref"),
                "applicability": item.get("applicability"),
                "availability": item.get("availability"),
                "freshness": _as_dict(item.get("freshness")),
                "cost": _as_dict(item.get("cost")),
                "disambiguation": {
                    "required": _as_dict(item.get("disambiguation")).get("required"),
                    "probe_ref": _as_dict(item.get("disambiguation")).get("probe_ref"),
                },
                "expand_ref": item.get("expand_ref"),
                "confidence": item.get("confidence"),
            }
            for item in candidates
        ]

    bundle = _as_dict(guidance.get("capability_bundle"))
    domain = _as_dict(guidance.get("domain_projection"))
    if detail == "micro":
        primary_owner = _as_dict(guidance.get("primary_owner"))
        capability_view = _as_dict(primary_owner.get("capability_view"))
        compact_primary_owner = {
            "name": primary_owner.get("name"),
            "authority_ref": primary_owner.get("authority_ref"),
            "selection_reason": primary_owner.get("selection_reason"),
            "handoff_condition": primary_owner.get("handoff_condition"),
            "decision_role": primary_owner.get("decision_role"),
            "supporting_evidence_refs": _as_list(primary_owner.get("supporting_evidence_refs"))[:2],
        }
        if capability_view:
            compact_primary_owner["capability_view"] = {
                "schema": capability_view.get("schema"),
                "source_signature": capability_view.get("source_signature"),
                "decision_mode": capability_view.get("decision_mode"),
                "candidate_count": len(_as_list(capability_view.get("candidate_refs"))),
                "issue_codes": [
                    item.get("code")
                    for item in _as_list(capability_view.get("issues"))[:2]
                    if isinstance(item, dict) and item.get("code")
                ],
                "next_action": capability_view.get("next_action"),
            }
        return {
            "schema": guidance.get("schema"),
            "active": guidance.get("active"),
            "decision_mode": guidance.get("decision_mode") or frame.get("decision_mode"),
            "primary_owner": compact_primary_owner,
            "capability_bundle": {
                "schema": bundle.get("schema"),
                "simple_fast_path": bundle.get("simple_fast_path"),
                "status": bundle.get("status"),
                "required_decision": bundle.get("required_decision"),
                "asset_count": len(_as_list(bundle.get("assets"))),
                "evidence_signature": bundle.get("evidence_signature"),
                "expand": [],
            },
            "environment_decision_frame": compact_frame,
            "domain_projection": {
                "schema": domain.get("schema"),
                "scenario": domain.get("scenario"),
                "asset_count": len(_as_list(domain.get("assets"))),
                "acceptance": _as_dict(domain.get("acceptance")),
                "expand": [],
            },
            "sequence": _as_list(guidance.get("sequence")),
            "tools": rows("tools", 4),
            "expand": "--detail standard",
        }

    return {
        "schema": guidance.get("schema"),
        "active": guidance.get("active"),
        "reason": guidance.get("reason"),
        "decision_mode": guidance.get("decision_mode") or frame.get("decision_mode"),
        "environment_decision_frame": compact_frame,
        "principle": guidance.get("principle") if detail == "standard" else "apply the smallest useful asset path; skip irrelevant assets",
        "primary_owner": _as_dict(guidance.get("primary_owner")),
        "capability_bundle": {
            "schema": bundle.get("schema"),
            "simple_fast_path": bundle.get("simple_fast_path"),
            "status": bundle.get("status"),
            "required_decision": bundle.get("required_decision"),
            "primary_owner": _as_dict(bundle.get("primary_owner")),
            "asset_count": len(_as_list(bundle.get("assets"))),
            "result_contract": _as_dict(bundle.get("result_contract")),
            "evidence_signature": bundle.get("evidence_signature"),
            "expand": _as_list(bundle.get("expand"))[:4],
        },
        "domain_projection": {
            "schema": domain.get("schema"),
            "scenario": domain.get("scenario"),
            "asset_count": len(_as_list(domain.get("assets"))),
            "acceptance": _as_dict(domain.get("acceptance")),
            "expand": _as_list(domain.get("expand"))[:4],
        },
        "sequence": _as_list(guidance.get("sequence")),
        "rules": rows("rules", 3),
        "skills": rows("skills", 2),
        "owners": rows("owners", 2),
        "tools": rows("tools", 4),
        "fallback": guidance.get("fallback") if detail == "standard" else "continue through the configured forward fallback when an asset is unavailable",
    }


def compact_environment_context(context: dict[str, Any], detail: str) -> dict[str, Any]:
    """Project derived environment knowledge without copying its owner inventories."""

    if not context:
        return {}
    systems = _as_list(context.get("relevant_systems"))
    architecture = _as_list(context.get("architecture_chain"))
    semantic_capabilities = []
    for item in _as_list(context.get("semantic_capabilities"))[:2]:
        if not isinstance(item, dict):
            continue
        business_results = _as_dict(item.get("business_results"))
        semantic_capabilities.append(
            {
                "capability_id": item.get("capability_id"),
                "name": item.get("name"),
                "owner": item.get("owner"),
                "entry_point": item.get("entry_point"),
                "state": item.get("state") or "unknown",
                "callable": item.get("callable"),
                "healthy": item.get("healthy"),
                "fallback_route": item.get("fallback_route"),
                "business_scope": item.get("business_scope"),
                "business_results": {
                    "vector_scope": business_results.get("vector_scope"),
                    "retrieval_mode": business_results.get("retrieval_mode"),
                    "semantic_modes": _as_list(business_results.get("semantic_modes"))[:4],
                    "observability_fields": _as_list(business_results.get("observability_fields"))[:4],
                    "citation_required": business_results.get("citation_required"),
                    "consumer_adapters": [
                        {
                            "id": adapter.get("id"),
                            "entry_point": adapter.get("entry_point"),
                            "acceptance": adapter.get("acceptance"),
                        }
                        for adapter in _as_list(business_results.get("consumer_adapters"))[:2]
                        if isinstance(adapter, dict)
                    ],
                },
                "evidence_refs": _as_list(item.get("evidence_refs"))[:3],
            }
        )

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
                item.get("layer") if isinstance(item, dict) else str(item)
                for item in architecture
                if (isinstance(item, dict) and item.get("layer")) or str(item).strip()
            ],
            "relevant_systems": [
                {"system": item.get("system")}
                for item in bounded_systems(4)
            ],
            "tool_entrypoints": [
                {
                    "purpose": item.get("purpose"),
                    "authority": item.get("authority"),
                }
                for item in _as_list(context.get("tool_entrypoints"))[:2]
                if isinstance(item, dict)
            ],
            "semantic_capabilities": semantic_capabilities,
            "source_refs": _as_list(context.get("source_refs"))[:4],
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
            "semantic_capabilities": semantic_capabilities,
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
        "rule": "micro keeps routing summary plus exact required owner commands; standard keeps compact phase commands; full keeps complete machine contract",
    }
    if detail == "full":
        return plan

    phases = [phase for phase in plan.get("machine_phases", []) if isinstance(phase, dict)]
    if detail == "standard":
        route = compact_execution_route_pack(plan, detail)
        projected = {
            "schema": plan.get("schema"),
            "ok": plan.get("ok"),
            "generated_at": plan.get("generated_at"),
            "message": plan.get("message"),
            "detail_level": detail,
            "profile": {
                "profile": _as_dict(plan.get("profile")).get("profile"),
                "validation_tier": _as_dict(plan.get("profile")).get("validation_tier"),
                "state_change_expected": _as_dict(plan.get("profile")).get("state_change_expected"),
            },
            "domains": compact_domains(_as_list(plan.get("domains"))),
            "retirement_guard": compact_retirement_guard(plan),
            "structured_route": compact_structured_route(_as_dict(plan.get("structured_route")), detail),
            "execution_route_pack": route,
            "machine_phases": [standard_phase(phase) for phase in phases if phase.get("enabled")],
        }
        return bounded_payload(
            projected,
            max_bytes=20 * 1024,
            max_items=30,
            max_string=1200,
            preserve_keys=(
                "schema",
                "ok",
                "generated_at",
                "detail_level",
                "profile",
                "domains",
                "retirement_guard",
                "structured_route",
                "execution_route_pack",
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
    route = compact_execution_route_pack(plan, detail)
    projected = {
        "schema": plan.get("schema"),
        "ok": plan.get("ok"),
        "generated_at": plan.get("generated_at"),
        "message": plan.get("message"),
        "detail_level": detail,
        "profile": {
            "profile": _as_dict(plan.get("profile")).get("profile"),
            "validation_tier": _as_dict(plan.get("profile")).get("validation_tier"),
            "state_change_expected": _as_dict(plan.get("profile")).get("state_change_expected"),
        },
        "domains": compact_domains(_as_list(plan.get("domains"))),
        "retirement_guard": compact_retirement_guard(plan),
        "structured_route": compact_structured_route(_as_dict(plan.get("structured_route")), "micro"),
        "execution_route_pack": route,
        "required_commands": required_command_contract(phases),
    }
    return bounded_payload(
        projected,
        max_bytes=10 * 1024,
        max_depth=8,
        max_items=16,
        max_string=500,
        preserve_keys=("schema", "ok", "generated_at", "detail_level", "profile", "domains", "retirement_guard", "structured_route", "execution_route_pack", "required_commands"),
    )
