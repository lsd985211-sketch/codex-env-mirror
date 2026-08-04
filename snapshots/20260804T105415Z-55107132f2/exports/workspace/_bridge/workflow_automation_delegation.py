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
import re
from pathlib import Path
from typing import Any

from workflow_terminal_convergence import invalidate_dependents, receipt_reuse_key


POLICY_SCHEMA_FAMILY = "workflow_automation_delegation"
POLICY_SCHEMA = f"{POLICY_SCHEMA_FAMILY}.v9"
POLICY_SCHEMA_AUTHORITY = {
    "family": POLICY_SCHEMA_FAMILY,
    "schema": POLICY_SCHEMA,
    "producer": "_bridge/workflow_automation_delegation.py",
    "consumer_scope": "_bridge/*.py",
    "rule": "Consumers import the producer schema authority and compare runtime projections; they do not pin a version literal.",
}

POLICY = {
    "schema": POLICY_SCHEMA,
    "principle": "Codex handles judgment, analysis, design, and exceptions; the environment handles low-risk, verifiable, reusable execution.",
    "efficiency_principle": "Do the least necessary work: reuse a valid receipt or derived index by stable input signature, batch independent operations, and run only the first invalidated step.",
    "single_authority_principle": "Persist each contract or state fact once at its owning layer; downstream layers consume it by reference and emit only the smallest derived projection needed by their caller.",
    "redundancy_design_checks": [
        "name_one_authoritative_owner_for_each_state_or_contract",
        "use_refs_for_cross_layer_consumption_instead_of_copying_full_payloads",
        "keep_one_validation_or_publication_step_per_stable_input_signature",
        "invalidate_only_the_changed_authority_and_its_dependents",
        "do_not_add_a_second_audit_when_a_route_guidance_or_owner_receipt_already_answers_the_question",
    ],
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
    "machine_execution_invariants": [
        "automate_only_a_declared_owner_operation_with_complete_inputs_and_a_stable_input_signature",
        "record_whether_a_current_receipt_is_reused_or_which_changed_input_invalidated_it",
        "require_a_consumable_readback_doctor_validate_metric_or_receipt_before_reporting_machine_success",
        "never_automate_approval_bypass_secret_access_external_send_destructive_cleanup_or_failure_state_erasure",
        "same_intent_input_owner_version_reuses_a_verified_terminal_receipt",
        "invalidate_only_graph_reachable_dependents",
        "post_mutation_validation_uses_read_only_owner_entrypoints",
        "a_terminal_transaction_never_automatically_loops",
    ],
    "evolution_rule": "If the same safe deterministic work recurs, promote it from Codex-handled steps into an owner CLI/MCP/scheduler path with validation.",
    "escalation_rule": "Escalate only for ambiguity, missing authority, approval boundaries, unknown inputs, failed validation, or an owner result that cannot be consumed.",
    "deduplication_rules": [
        "Never repeat a successful read-only owner call when its input signature and freshness receipt are still valid.",
        "Do not repeat a source discovery, package metadata lookup, hash, or asset validation already covered by a current receipt.",
        "Batch independent resource/package requests under one bounded deadline and one route decision.",
        "A source-affecting closeout may publish at most one final snapshot; later steps consume its receipt.",
    ],
    "long_command_contract": {
        "preference_order": ["consume_native_process_or_session_handle", "single_call_durable_terminal_convergence"],
        "identity": "one stable intent id maps to one task id and one command-cwd-timeout execution signature",
        "reuse": "a matching running or terminal receipt is consumed, including a failed terminal result; the business command is never resubmitted as polling",
        "conflict": "the same intent or task id with a different execution signature fails closed",
        "convergence": "one machine call submits or reuses the intent and stays alive until the durable terminal receipt; running observations never emit another status or follow command",
        "expansion": "full stdout and stderr remain available through the durable raw result reference",
    },
    "command_contract_reuse": {
        "scope": "one_owner_version_and_command_family_per_task",
        "authority_order": [
            "owner_plan_receipt",
            "owner_registry_or_machine_readable_schema",
            "task_current_contract_projection",
            "targeted_help_last_resort",
        ],
        "reuse_when": [
            "owner_is_unchanged",
            "owner_version_or_contract_signature_is_unchanged",
            "plan_or_schema_already_supplies_required_arguments_and_confirmation_tokens",
        ],
        "expand_help_only_when": [
            "argument_rejected",
            "owner_changed",
            "owner_version_or_contract_signature_changed",
            "contract_incomplete_for_requested_operation",
        ],
        "adjacent_subcommand_rule": "A plan contract covering apply, commit, integrate, or equivalent adjacent phases is consumed once; do not query each phase help separately.",
        "capability_preservation": "Targeted help remains available as the explicit expansion path when the reusable contract is insufficient.",
    },
    "live_contract_preconditions": {
        "tool_schema_authority": "App, MCP, and owner CLI calls consume the current exposed schema or owner contract; remembered fields and copied examples are not authoritative. Missing MCP configuration, CLI visibility, persisted dynamic-tool metadata, or collaboration-agent registration does not prove a native App tool is unavailable: inspect the current exposed App schema and operation-specific discovery entrypoint before making an unavailable claim.",
        "argument_rejection": "Compare rejected arguments with the current schema before declaring contract drift; unknown or missing fields are caller errors.",
        "drift_evidence": "Contract drift requires two distinct authoritative schema fingerprints, not a rejected call alone.",
        "repair_budget": "After a pre-effect argument rejection, rebuild once from the current schema; a second rejection stops, and a possible side effect is never guessed or retried.",
        "thread_coordination": "Cross-task coordination and closeout delegation must exclude the current thread by ID whenever CODEX_THREAD_ID identifies it: target equality with that ID is always rejected, even when a caller labels the target currentTask=false. Absence of that identity does not alter otherwise valid coordination of another target. Discover only same-repository active targets and obtain a second current Codex App state projection immediately before every dispatch; a listed Codex App threadId is delivered only through send_message_to_thread with its current hostId, while collaboration-agent messaging accepts only agents registered in the current collaboration tree and never an App threadId; idle, notLoaded, completed, archived, foreign, stale, and current tasks are excluded.",
        "receipt_boundary": "A message receipt proves delivery only, not target activity, agreement, overlap resolution, or handoff completion.",
    },
}


def schema_authority_contract() -> dict[str, str]:
    """Return the single producer-owned schema/version contract."""

    return dict(POLICY_SCHEMA_AUTHORITY)


def validate_consumer_schema_authority(
    *,
    family: str,
    schema: str,
    producer_path: Path,
    consumer_root: Path,
) -> dict[str, Any]:
    """Reject producer-owned schema versions copied into Python consumers.

    This is deliberately parameterized so other workflow contracts can adopt
    the same producer/consumer invariant without creating another registry or
    validator implementation.
    """

    family_pattern = re.compile(rf"{re.escape(family)}\.v\d+")
    copied_literals: list[dict[str, Any]] = []
    for path in sorted(consumer_root.glob("*.py")):
        if path.resolve() == producer_path.resolve():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            copied_literals.append({"path": str(path), "reason": "unreadable_consumer", "detail": str(exc)[:200]})
            continue
        for line_number, line in enumerate(source.splitlines(), start=1):
            matches = sorted(set(family_pattern.findall(line)))
            if matches:
                copied_literals.append(
                    {"path": str(path), "line": line_number, "schemas": matches, "reason": "copied_version_literal"}
                )
    return {
        "ok": not copied_literals,
        "family": family,
        "schema": schema,
        "producer": str(producer_path),
        "consumer_root": str(consumer_root),
        "copied_literals": copied_literals,
    }


def validate_schema_authority(*, bridge_root: Path | None = None) -> dict[str, Any]:
    """Validate the delegation producer and every Python consumer.

    Runtime consumers must import ``POLICY_SCHEMA``. Scanning the whole bridge
    Python surface catches both a stale prior version and a newly copied current
    version without maintaining a second consumer inventory.
    """

    root = bridge_root or Path(__file__).resolve().parent
    producer = Path(__file__).resolve()
    consumer_validation = validate_consumer_schema_authority(
        family=POLICY_SCHEMA_FAMILY,
        schema=POLICY_SCHEMA,
        producer_path=producer,
        consumer_root=root,
    )
    checks = {
        "producer_policy_matches_authority": POLICY.get("schema") == POLICY_SCHEMA_AUTHORITY["schema"],
        "consumer_version_literals_absent": consumer_validation["ok"],
    }
    return {
        "schema": "workflow_automation_delegation.schema_authority_validation.v1",
        "ok": all(checks.values()),
        "authority": schema_authority_contract(),
        "checks": checks,
        "copied_literals": consumer_validation["copied_literals"],
    }


def automation_delegation_policy() -> dict[str, Any]:
    """Return a copy of the workflow delegation policy."""

    return {
        **POLICY,
        "codex_owns": list(POLICY["codex_owns"]),
        "environment_owns_when_all_true": list(POLICY["environment_owns_when_all_true"]),
        "handoff_outputs": dict(POLICY["handoff_outputs"]),
        "evidence_required": list(POLICY["evidence_required"]),
        "machine_execution_invariants": list(POLICY["machine_execution_invariants"]),
        "deduplication_rules": list(POLICY["deduplication_rules"]),
        "command_contract_reuse": {
            **POLICY["command_contract_reuse"],
            "authority_order": list(POLICY["command_contract_reuse"]["authority_order"]),
            "reuse_when": list(POLICY["command_contract_reuse"]["reuse_when"]),
            "expand_help_only_when": list(POLICY["command_contract_reuse"]["expand_help_only_when"]),
        },
        "long_command_contract": {
            **POLICY["long_command_contract"],
            "preference_order": list(POLICY["long_command_contract"]["preference_order"]),
        },
        "live_contract_preconditions": dict(POLICY["live_contract_preconditions"]),
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
        "single_authority_principle": POLICY["single_authority_principle"],
        "redundancy_design_checks": list(POLICY["redundancy_design_checks"]),
        "deduplication_rules": list(POLICY["deduplication_rules"]),
        "command_contract_reuse": {
            **POLICY["command_contract_reuse"],
            "authority_order": list(POLICY["command_contract_reuse"]["authority_order"]),
            "reuse_when": list(POLICY["command_contract_reuse"]["reuse_when"]),
            "expand_help_only_when": list(POLICY["command_contract_reuse"]["expand_help_only_when"]),
        },
        "long_command_contract": {
            **POLICY["long_command_contract"],
            "preference_order": list(POLICY["long_command_contract"]["preference_order"]),
        },
        "machine_execution_invariants": list(POLICY["machine_execution_invariants"]),
        "escalation_rule": POLICY["escalation_rule"],
        "live_contract_preconditions": dict(POLICY["live_contract_preconditions"]),
    }


def single_authority_plan_check(plan: dict[str, Any]) -> dict[str, Any]:
    """Check route-plan structure for cross-layer contract duplication."""

    structured = plan.get("structured_route") if isinstance(plan.get("structured_route"), dict) else {}
    pack = plan.get("execution_route_pack") if isinstance(plan.get("execution_route_pack"), dict) else {}
    decision = pack.get("route_decision") if isinstance(pack.get("route_decision"), dict) else {}
    resource_gate = pack.get("resource_gate") if isinstance(pack.get("resource_gate"), dict) else {}
    checks = {
        "task_contract_has_one_authority": bool(structured.get("task_contract"))
        and "task_contract" not in decision
        and decision.get("task_contract_ref") == "structured_route.task_contract",
        "route_decision_has_one_authority": "route_decision" not in structured
        and structured.get("route_decision_ref") == "execution_route_pack.route_decision",
        "task_facts_are_referenced_not_copied": "task_facts" not in decision
        and decision.get("task_facts_ref") == "structured_route.task_contract.task_facts",
        "matched_signals_are_referenced_not_copied": "matched_signals" not in decision
        and decision.get("matched_signals_ref") == "structured_route.task_contract.matched_signals",
        "resource_contracts_have_one_authority": (
            not resource_gate.get("enabled")
            or (
                bool(resource_gate.get("completion_contract"))
                and bool(resource_gate.get("task_lifecycle"))
                and "resource_completion_contract" not in decision
                and "resource_task_lifecycle" not in decision
                and decision.get("resource_completion_contract_ref") == "resource_gate.completion_contract"
                and decision.get("resource_task_lifecycle_ref") == "resource_gate.task_lifecycle"
            )
        ),
        "asset_guidance_has_one_authority": "asset_guidance" in pack
        and "asset_guidance" not in plan
        and structured.get("asset_guidance_ref") == "execution_route_pack.asset_guidance",
        "environment_context_has_one_authority": "environment_context" in pack and "environment_context" not in plan,
        "automation_decision_has_one_authority": "automation_decision" in pack and "automation_decision" not in plan,
    }
    return {
        "schema": "workflow_automation_delegation.single_authority_check.v1",
        "ok": all(checks.values()),
        "checks": checks,
        "rule": POLICY["single_authority_principle"],
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


def tool_contract_preflight(
    *,
    tool_name: str,
    current_schema: dict[str, Any] | None,
    attempted_arguments: dict[str, Any] | None,
    prior_schema_fingerprint: str = "",
    repair_attempted: bool = False,
) -> dict[str, Any]:
    """Classify a call against its live contract without executing it."""

    schema = current_schema if isinstance(current_schema, dict) else {}
    arguments = attempted_arguments if isinstance(attempted_arguments, dict) else {}
    if not schema:
        return {
            "schema": "workflow_automation_delegation.tool_contract_preflight.v1",
            "tool_name": str(tool_name or ""),
            "decision": "contract_unavailable",
            "current_schema_fingerprint": "",
            "prior_schema_fingerprint": str(prior_schema_fingerprint or ""),
            "contract_drift_observed": False,
            "single_schema_rebuild_allowed": False,
            "next_action": "read_current_authoritative_contract_before_call",
        }

    current_fingerprint = hashlib.sha256(
        json.dumps(_canonical(schema), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = sorted({str(value) for value in schema.get("required", []) if str(value)})
    unknown = sorted(key for key in arguments if key not in properties)
    missing = sorted(name for name in required if name not in arguments)
    drift_observed = bool(prior_schema_fingerprint and prior_schema_fingerprint != current_fingerprint)
    caller_arguments_invalid = bool(unknown or missing)
    if drift_observed:
        decision = "contract_drift_observed"
        next_action = "rebuild_call_from_current_authoritative_schema"
    elif caller_arguments_invalid:
        decision = "caller_arguments_invalid"
        next_action = "rebuild_call_once_from_current_authoritative_schema" if not repair_attempted else "stop_and_report_unconsumable_contract"
    else:
        decision = "current_contract_matches"
        next_action = "execute_once_with_current_contract"
    return {
        "schema": "workflow_automation_delegation.tool_contract_preflight.v1",
        "tool_name": str(tool_name or ""),
        "decision": decision,
        "current_schema_fingerprint": current_fingerprint,
        "prior_schema_fingerprint": str(prior_schema_fingerprint or ""),
        "contract_drift_observed": drift_observed,
        "unknown_arguments": unknown,
        "missing_arguments": missing,
        "caller_arguments_invalid": caller_arguments_invalid,
        "single_schema_rebuild_allowed": bool(caller_arguments_invalid and not repair_attempted),
        "repair_attempted": bool(repair_attempted),
        "next_action": next_action,
    }


def terminal_receipt_decision(
    *,
    action_id: str,
    input_signature: str,
    owner_contract_version: str,
    intent_id: str,
    receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the execution-economy decision for one terminal receipt.

    The pure convergence module owns receipt identity. This adapter keeps the
    machine-first policy consumable without copying graph or hashing logic.
    """

    expected_key = receipt_reuse_key(
        action_id=action_id,
        input_signature=input_signature,
        owner_contract_version=owner_contract_version,
        intent_id=intent_id,
    )
    value = receipt if isinstance(receipt, dict) else {}
    accepted = value.get("reuse_key") == expected_key and value.get("accepted") is True
    return {
        "schema": "workflow_automation_delegation.terminal_receipt_decision.v1",
        "decision": "reuse" if accepted else "execute",
        "reason": "verified_receipt_matches_intent" if accepted else "receipt_identity_mismatch",
        "expected_reuse_key": expected_key,
        "expected_receipt": {
            "action_id": action_id,
            "reuse_key": expected_key,
            "accepted": True,
        },
    }


def terminal_invalidated_actions(
    actions: list[dict[str, Any]], changed_action_ids: list[str]
) -> list[str]:
    """Project minimal graph invalidation through the execution-economy owner."""

    return invalidate_dependents(actions, changed_action_ids)


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
    execution_contract = {
        "machine_actions": [item for item in machine_actions if item],
        "input_signature": signature,
        "required_evidence": ["reuse_or_skip_decision", "verification_result", "consumable_receipt"],
        "automated_write_allowed": False,
        "forbidden": ["approval_bypass", "secret_access", "external_send", "destructive_cleanup", "failure_state_erasure"],
    }
    return {
        "schema": "workflow_automation_delegation.decision.v1",
        "decision_class": decision_class,
        "reason": reason,
        "codex_owns": decision_class in {"review_required", "blocked"},
        "environment_owns": decision_class in {"auto_execute", "codex_deferred"},
        "machine_actions": execution_contract["machine_actions"],
        "input_signature": signature,
        "machine_execution_contract": execution_contract,
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
