#!/usr/bin/env python3
"""Automation delegation policy for workflow routing.

Ownership: workflow orchestration support for deciding Codex-vs-environment work.
Non-goals: execute tasks, mutate queues, classify domains, or bypass owner tools.
State behavior: read-only policy projection.
Caller context: workflow_orchestrator and execution_route_pack expose this as a
compact prompt so Codex and tool routers can hand deterministic work to the
environment while keeping complex judgment with Codex.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


POLICY = {
    "schema": "workflow_automation_delegation.v2",
    "principle": "Codex handles judgment, analysis, design, and exceptions; the environment handles low-risk, verifiable, reusable execution.",
    "efficiency_principle": "Do the least necessary work: reuse a valid receipt or derived index by stable input signature, batch independent operations, and run only the first invalidated step.",
    "codex_owns": [
        "unclear_goal_or_missing_context",
        "root_cause_analysis",
        "tradeoff_or_architecture_decision",
        "external_research_or_evidence_synthesis",
        "permission_safety_or_stability_boundary",
        "failure_recovery_or_exception_handling",
    ],
    "environment_owns_when_all_true": [
        "fields_complete",
        "owner_tool_or_cli_exists",
        "operation_is_low_risk",
        "behavior_is_deterministic_or_template_based",
        "result_can_be_verified_by_readback_doctor_validate_metrics_or_receipt",
        "no_new_permission_secret_destructive_or_external_send_boundary",
    ],
    "handoff_outputs": {
        "auto_execute": "environment may run the owned deterministic path and return structured evidence",
        "codex_deferred": "environment may enqueue/package the task; Codex is invoked only for the complex generation or analysis step",
        "review_required": "environment must not write/execute; Codex or user must resolve missing, ambiguous, risky, or unsupported inputs",
        "blocked": "task cannot proceed under current boundary; report the concrete blocker",
    },
    "evidence_required": [
        "decision_class",
        "owner_route",
        "action_taken_or_not_taken",
        "verification_result",
        "remaining_human_or_codex_work",
        "input_signature",
        "reuse_or_skip_decision",
        "batch_key_or_singleton_reason",
    ],
    "evolution_rule": "If the same safe deterministic work recurs, promote it from Codex-handled steps into an owner CLI/MCP/scheduler path with validation.",
    "escalation_rule": "Escalate only for ambiguity, missing authority, approval boundaries, unknown inputs, failed validation, or an owner result that cannot be consumed.",
    "deduplication_rules": [
        "Never repeat a successful read-only owner call when its input signature and freshness receipt are still valid.",
        "Do not repeat a source discovery, package metadata lookup, hash, or asset validation already covered by a current receipt.",
        "Batch independent resource/package requests under one bounded deadline and one route decision.",
        "A source-affecting closeout may publish at most one final snapshot; later steps consume its receipt.",
    ],
}


def automation_delegation_policy() -> dict[str, Any]:
    """Return a copy of the workflow delegation policy."""

    return {
        **POLICY,
        "codex_owns": list(POLICY["codex_owns"]),
        "environment_owns_when_all_true": list(POLICY["environment_owns_when_all_true"]),
        "handoff_outputs": dict(POLICY["handoff_outputs"]),
        "evidence_required": list(POLICY["evidence_required"]),
        "deduplication_rules": list(POLICY["deduplication_rules"]),
    }


def compact_automation_delegation_policy() -> dict[str, Any]:
    """Return the compact policy subset for execution_route_pack."""

    return {
        "schema": POLICY["schema"],
        "principle": POLICY["principle"],
        "decision_classes": list(POLICY["handoff_outputs"].keys()),
        "environment_gate": list(POLICY["environment_owns_when_all_true"]),
        "evidence_required": list(POLICY["evidence_required"]),
        "efficiency_principle": POLICY["efficiency_principle"],
        "deduplication_rules": list(POLICY["deduplication_rules"]),
        "escalation_rule": POLICY["escalation_rule"],
    }


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def input_signature(*, declared_inputs: dict[str, Any], owner_version: str = "") -> str:
    """Return a stable signature for machine work, excluding chat narration."""

    payload = {"owner_version": str(owner_version or ""), "declared_inputs": _canonical(declared_inputs)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def automation_delegation_decision(
    *,
    task_facts: dict[str, Any],
    owner_route: dict[str, Any],
    required_gates: list[dict[str, Any]],
    machine_phases: list[dict[str, Any]],
    declared_inputs: dict[str, Any],
    risk: str = "unknown",
    ambiguous: bool = False,
    resource_required: bool = False,
) -> dict[str, Any]:
    """Classify who should act and how much of a repeatable path may run."""

    gates = [item for item in required_gates if isinstance(item, dict)]
    route_known = bool(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or resource_required)
    approval_required = str(risk or "").lower() in {"l3", "high", "write", "dangerous"} or any(
        bool(item.get("approval_required")) for item in gates
    )
    external_effect = any(bool(task_facts.get(key)) for key in ("external_write", "external_send", "destructive_or_high_risk"))
    stateful_effect = any(
        bool(task_facts.get(key))
        for key in ("local_write", "config_change", "system_member_change", "database_write", "gui_or_browser_state", "reload_or_restart_required")
    )
    unknown_input = bool(task_facts.get("unknown_input") or task_facts.get("missing_context"))
    if not route_known:
        decision_class = "blocked"
        reason = "owner_route_missing"
    elif ambiguous or approval_required or external_effect or unknown_input or (stateful_effect and not resource_required):
        decision_class = "review_required"
        reason = "codex_or_user_boundary_required"
    elif resource_required:
        decision_class = "codex_deferred"
        reason = "environment_acquires_and_returns_receipt_codex_consumes_result"
    else:
        decision_class = "auto_execute"
        reason = "deterministic_low_risk_owner_path"

    machine_actions: list[str] = []
    for phase in machine_phases:
        if not isinstance(phase, dict) or not phase.get("enabled"):
            continue
        commands = phase.get("commands") if isinstance(phase.get("commands"), list) else []
        if commands and all(bool(item.get("read_only")) and not bool(item.get("approval_required")) for item in commands if isinstance(item, dict)):
            machine_actions.append(str(phase.get("id") or ""))
    signature = input_signature(declared_inputs=declared_inputs, owner_version=str(owner_route.get("capability") or ""))
    return {
        "schema": "workflow_automation_delegation.decision.v1",
        "decision_class": decision_class,
        "reason": reason,
        "codex_owns": decision_class in {"review_required", "blocked"},
        "environment_owns": decision_class in {"auto_execute", "codex_deferred"},
        "machine_actions": [item for item in machine_actions if item],
        "input_signature": signature,
        "reuse_policy": {
            "reuse_receipt_when": ["same_input_signature", "owner_version_unchanged", "receipt_fresh_and_validated"],
            "skip_steps_when": ["step_receipt_is_current", "downstream_inputs_unchanged"],
            "invalidate_when": ["declared_input_changed", "owner_version_changed", "validation_failed", "source_freshness_changed"],
        },
        "batch_policy": {
            "eligible": decision_class in {"auto_execute", "codex_deferred"},
            "key": f"{decision_class}:{signature}",
            "rule": "batch_independent_same-owner_operations_under_one_deadline; keep stateful or approval-bound operations separate",
        },
        "codex_escalation": {
            "only_for": ["ambiguity", "missing_owner", "approval_boundary", "unknown_input", "failed_validation", "unconsumable_receipt"],
            "required_now": decision_class in {"review_required", "blocked"},
        },
        "evidence_required": list(POLICY["evidence_required"]),
    }
