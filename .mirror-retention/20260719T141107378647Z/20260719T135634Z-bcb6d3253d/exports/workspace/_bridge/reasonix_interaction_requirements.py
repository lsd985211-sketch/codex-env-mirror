#!/usr/bin/env python3
"""Requirement catalog for the Reasonix interaction shadow validator.

This file is intentionally declarative. It defines what the interaction
policy must preserve before it is allowed to graduate from shadow mode.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    id: str
    priority: str
    statement: str
    acceptance: tuple[str, ...]
    forbidden: tuple[str, ...] = ()
    min_positive_scenarios: int = 1
    min_negative_scenarios: int = 1
    blocks_release: bool = True


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        id="REQ-001",
        priority="P0",
        statement="Only a completed Reasonix response with non-empty content can be treated as consumable review.",
        acceptance=(
            "required review paths wait for done_nonempty-style results",
            "claimed/executing/pending states are surfaced as progress or risk, never success",
        ),
        forbidden=("treat_claimed_as_success", "consume_empty_result"),
    ),
    Requirement(
        id="REQ-002",
        priority="P0",
        statement="Reasonix offline knowledge assistance must never be mislabeled as true AI review.",
        acceptance=(
            "optional offline paths may expose reasonix_offline_kb",
            "required offline paths exclude reasonix_offline_kb and preserve pending/late-result handling",
        ),
        forbidden=("offline_kb_as_required_review",),
    ),
    Requirement(
        id="REQ-003",
        priority="P0",
        statement="AI offline handling must decide whether to wake/probe Reasonix before falling back.",
        acceptance=(
            "responder down chooses responder_then_true_ai",
            "responder alive but AI offline chooses true_ai and checks credentials",
        ),
        forbidden=("silent_offline_downgrade",),
    ),
    Requirement(
        id="REQ-004",
        priority="P0",
        statement="Transport, credential, and responder blockers must be explicit and non-successful.",
        acceptance=(
            "mcp transport closed creates a blocker",
            "missing credentials create a blocker for true AI wake attempts",
        ),
        forbidden=("hide_blocker", "blocker_marked_success"),
    ),
    Requirement(
        id="REQ-005",
        priority="P0",
        statement="The policy must remain shadow-only before rollout.",
        acceptance=(
            "all decisions include a shadow-only note",
            "shadow actions describe would-do behavior only",
        ),
        forbidden=("writes_bridge_state", "starts_process", "calls_external_api"),
        min_negative_scenarios=0,
    ),
    Requirement(
        id="REQ-006",
        priority="P1",
        statement="Late results must be preserved without pretending they were available synchronously.",
        acceptance=(
            "required offline paths include late_result as an acceptable future result kind",
            "timeout_pending remains distinguishable from reasonix_ai_review",
        ),
        forbidden=("drop_late_result", "retroactive_sync_success"),
    ),
    Requirement(
        id="REQ-007",
        priority="P1",
        statement="Repeated or backlogged requests must be visible as risk until a dedup/result contract exists.",
        acceptance=(
            "claimed-without-result backlog is reported as risk",
            "pending review backlog is reported as risk",
        ),
        forbidden=("duplicate_silent_success",),
    ),
    Requirement(
        id="REQ-008",
        priority="P1",
        statement="Review need classification must be conservative for explicit Reasonix, high risk, and domain-critical tasks.",
        acceptance=(
            "explicit Reasonix requests require review",
            "L2/L3 tasks require review",
            "architecture/config/root-cause keywords require review",
        ),
        forbidden=("critical_task_skips_review",),
    ),
    Requirement(
        id="REQ-009",
        priority="P1",
        statement="Malformed payloads must not crash the shadow validator.",
        acceptance=(
            "missing task/state maps are normalized",
            "unknown risk values do not crash evaluation",
        ),
        forbidden=("malformed_payload_crash",),
    ),
    Requirement(
        id="REQ-010",
        priority="P2",
        statement="Low-risk unrelated requests should avoid needless Reasonix calls.",
        acceptance=("greeting/status/test chatter can be handled directly",),
        forbidden=("overcall_reasonix_for_trivial_task",),
        blocks_release=False,
    ),
    Requirement(
        id="REQ-011",
        priority="P0",
        statement="System-level Reasonix interaction work must expose a read-only maintenance contract.",
        acceptance=(
            "maintenance snapshot exposes contract_version, producer_version, schema_version, capabilities, safety, validation summary, and metrics",
            "snapshot is machine-readable and does not invoke live execution",
        ),
        forbidden=("missing_maintenance_contract", "snapshot_mutates_state"),
    ),
    Requirement(
        id="REQ-012",
        priority="P0",
        statement="Maintenance repair must remain dry-run/proposal-only before rollout.",
        acceptance=(
            "repair-plan reports would_write=false and would_start_process=false",
            "repair plan items require user confirmation and do not execute actions",
        ),
        forbidden=("repair_executes_without_confirmation", "repair_bypasses_shadow_only"),
    ),
    Requirement(
        id="REQ-013",
        priority="P0",
        statement="Maintenance doctor must classify failures into policy failure, coverage gap, live-state drift, and external transport failure.",
        acceptance=(
            "doctor returns separate blockers, risks, and advisories",
            "transport blockers and backlog drift are distinguishable from policy coverage failures",
        ),
        forbidden=("ambiguous_maintenance_failure",),
    ),
    Requirement(
        id="REQ-014",
        priority="P1",
        statement="Maintenance outputs must provide stable machine-readable metrics.",
        acceptance=(
            "metrics include requirements_ok, scenario_count, blocking_requirement_count, shadow_only_ok, and repair_dry_run_only",
            "validate returns quick/full profile metadata and structured validation output",
        ),
        forbidden=("metrics_text_only", "validate_unstructured"),
    ),
    Requirement(
        id="REQ-015",
        priority="P1",
        statement="Maintenance contract coverage must evolve with interaction requirements.",
        acceptance=(
            "maintenance requirements are part of the same requirement coverage gate",
            "coverage failure blocks rollout when maintenance contract scenarios are missing",
        ),
        forbidden=("maintenance_contract_outside_coverage_gate",),
    ),
)


def requirement_by_id(requirement_id: str) -> Requirement:
    for requirement in REQUIREMENTS:
        if requirement.id == requirement_id:
            return requirement
    raise KeyError(requirement_id)
