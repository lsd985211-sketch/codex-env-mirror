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

from typing import Any


POLICY = {
    "schema": "workflow_automation_delegation.v1",
    "principle": "Codex handles judgment, analysis, design, and exceptions; the environment handles low-risk, verifiable, reusable execution.",
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
    ],
    "evolution_rule": "If the same safe deterministic work recurs, promote it from Codex-handled steps into an owner CLI/MCP/scheduler path with validation.",
}


def automation_delegation_policy() -> dict[str, Any]:
    """Return a copy of the workflow delegation policy."""

    return {
        **POLICY,
        "codex_owns": list(POLICY["codex_owns"]),
        "environment_owns_when_all_true": list(POLICY["environment_owns_when_all_true"]),
        "handoff_outputs": dict(POLICY["handoff_outputs"]),
        "evidence_required": list(POLICY["evidence_required"]),
    }


def compact_automation_delegation_policy() -> dict[str, Any]:
    """Return the compact policy subset for execution_route_pack."""

    return {
        "schema": POLICY["schema"],
        "principle": POLICY["principle"],
        "decision_classes": list(POLICY["handoff_outputs"].keys()),
        "environment_gate": list(POLICY["environment_owns_when_all_true"]),
        "evidence_required": list(POLICY["evidence_required"]),
    }
