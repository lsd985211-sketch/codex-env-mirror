#!/usr/bin/env python3
"""Derive governed iteration proposals from bounded Codex observations.

Ownership: repeated-pattern planning, stable candidate identity, and the thin
adapter into the existing workflow review queue.
Non-goals: raw event capture, approval, memory or PMB writes, rule/skill/config
changes, command execution, or automatic remediation.
State behavior: plan and validate are read-only; apply may only upsert pending
items through ``workflow_review_queue.sync_review_groups``.
Caller context: the unified daily scheduler and focused manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from codex_rule_observer import DEFAULT_RUNTIME_ROOT, read_recent_events
from workflow_iteration_capture import stable_candidate_id
from workflow_review_queue import QUEUE_PATH, sync_review_groups


SCHEMA = "workflow_observation_iteration.v1"
POLICY_VERSION = "observation-pattern-policy.v1"
TARGET_NAMESPACE = "memory.project_conclusions"
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _digest(value: Any, *, length: int = 24) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _turn_key(event: dict[str, Any]) -> tuple[str, str]:
    return str(event.get("session_id") or "unknown"), str(event.get("turn_id") or "unknown")


def _safe_label(value: Any) -> str:
    text = str(value or "").strip()
    return text if SAFE_LABEL_RE.fullmatch(text) else f"redacted-label-{_digest(text, length=12)}"


def _known_outcome(event: dict[str, Any]) -> bool | None:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    if isinstance(result.get("ok"), bool):
        return bool(result["ok"])
    status = str(result.get("status") or "").strip().lower()
    if status in {"completed", "ok", "success", "succeeded"}:
        return True
    if status in {"blocked", "error", "failed", "failure", "timed_out", "timeout"}:
        return False
    return None


def _candidate(
    *,
    pattern_type: str,
    pattern_key: str,
    stable_conclusion: str,
    evidence_events: list[dict[str, Any]],
    attributes: dict[str, Any],
    runtime_root: Path,
) -> dict[str, Any]:
    source_checkpoint = f"{POLICY_VERSION}:{pattern_type}:{pattern_key}"
    candidate_id = stable_candidate_id(
        text=stable_conclusion,
        source_checkpoint=source_checkpoint,
        stable_conclusion=stable_conclusion,
        target_namespace=TARGET_NAMESPACE,
        affected_system="workflow",
    )
    event_ids = sorted({str(item.get("event_id") or "") for item in evidence_events if item.get("event_id")})
    turn_ids = sorted({"/".join(_turn_key(item)) for item in evidence_events})
    return {
        "candidate_id": candidate_id,
        "source_item_id": candidate_id,
        "title": "Observed repeated workflow pattern",
        "summary": stable_conclusion,
        "source_url": f"artifact:{runtime_root}",
        "source_checkpoint": source_checkpoint,
        "stable_conclusion": stable_conclusion,
        "target_namespace": TARGET_NAMESPACE,
        "affected_system": "workflow",
        "proposed_destination_namespace": TARGET_NAMESPACE,
        "trust_tier": "derived_observation_proposal",
        "freshness_class": "bounded_observation_window",
        "approval_action": "review_evidence_then_approve_or_reject; no automatic remediation",
        "required_checks": [
            "Confirm the pattern remains useful outside the sampled turns",
            "Inspect stable event references without loading full private payloads",
            "Route any approved change through its exact owner",
            "Validate owner readback before resolving the review item",
        ],
        "attributes": {
            "owner": "memory_governance",
            "signal_kind": "repeated_observation_pattern",
            "pattern_type": pattern_type,
            "pattern_key": pattern_key,
            "policy_version": POLICY_VERSION,
            "event_count": len(event_ids),
            "turn_count": len(turn_ids),
            "evidence_signature": _digest(event_ids),
            "event_ids": event_ids[:50],
            "turn_refs": turn_ids[:20],
            "write_authorization_inherited": False,
            **attributes,
        },
    }


def derive_candidates(
    events: list[dict[str, Any]],
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    minimum_occurrences: int = 3,
    minimum_turns: int = 2,
) -> list[dict[str, Any]]:
    post_tool_events = [item for item in events if item.get("event") == "PostToolUse"]
    by_turn: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in post_tool_events:
        by_turn[_turn_key(event)].append(event)

    candidates: list[dict[str, Any]] = []
    governance_patterns = {
        "generic_web_without_resource_layer": (
            "generic_web",
            "resource_layer",
            "Repeated turns used generic web without an observed resource-layer result; review routing evidence before changing policy.",
        ),
        "local_write_without_backup": (
            "local_write",
            "backup",
            "Repeated turns contained local writes without an observed backup-owner result; review whether the workflow entry path needs reinforcement.",
        ),
        "local_write_without_validation": (
            "local_write",
            "validation",
            "Repeated turns contained local writes without an observed validation result; review whether the workflow entry path needs reinforcement.",
        ),
    }
    for pattern_key, (trigger_category, excluded_category, conclusion) in governance_patterns.items():
        evidence = [
            event
            for turn_events in by_turn.values()
            if trigger_category in {str(item.get("category") or "") for item in turn_events}
            and excluded_category not in {str(item.get("category") or "") for item in turn_events}
            for event in turn_events
            if event.get("category") == trigger_category
        ]
        turn_count = len({_turn_key(item) for item in evidence})
        if len(evidence) >= minimum_occurrences and turn_count >= minimum_turns:
            candidates.append(
                _candidate(
                    pattern_type="workflow_conformance",
                    pattern_key=pattern_key,
                    stable_conclusion=conclusion,
                    evidence_events=evidence,
                    attributes={"occurrence_count": len(evidence)},
                    runtime_root=runtime_root,
                )
            )

    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in post_tool_events:
        if _known_outcome(event) is not None:
            by_tool[_safe_label(event.get("tool_name"))].append(event)
    for tool, tool_events in sorted(by_tool.items()):
        outcomes = [_known_outcome(item) for item in tool_events]
        failures = sum(value is False for value in outcomes)
        turn_count = len({_turn_key(item) for item in tool_events})
        if len(tool_events) < max(5, minimum_occurrences) or failures < 2 or turn_count < minimum_turns:
            continue
        failure_rate = failures / len(tool_events)
        if failure_rate < 0.4:
            continue
        candidates.append(
            _candidate(
                pattern_type="tool_reliability",
                pattern_key=tool,
                stable_conclusion=(
                    f"Tool `{tool}` has a repeated observed failure pattern; verify owner health and result acceptance before proposing a durable workflow change."
                ),
                evidence_events=tool_events,
                attributes={
                    "known_outcome_count": len(tool_events),
                    "failure_count": failures,
                    "failure_rate": round(failure_rate, 3),
                },
                runtime_root=runtime_root,
            )
        )
    return candidates


def run(
    *,
    apply: bool,
    confirm: str = "",
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    queue_path: Path = QUEUE_PATH,
    max_age_hours: int = 24 * 7,
    minimum_occurrences: int = 3,
    minimum_turns: int = 2,
) -> dict[str, Any]:
    events = read_recent_events(runtime_root=runtime_root, max_age_hours=max_age_hours)
    candidates = derive_candidates(
        events,
        runtime_root=runtime_root,
        minimum_occurrences=max(2, int(minimum_occurrences)),
        minimum_turns=max(2, int(minimum_turns)),
    )
    if apply and confirm != "APPLY-OBSERVATION-PROPOSALS":
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "reason": "confirmation_required",
            "confirmation": "APPLY-OBSERVATION-PROPOSALS",
            "writes_review_queue": False,
        }
    queue_result: list[dict[str, Any]] = []
    if apply and candidates:
        queue_result = sync_review_groups(
            [
                {
                    "kind": "iteration_candidates",
                    "title": "Observed workflow patterns requiring review",
                    "review_items": candidates,
                }
            ],
            db_path=queue_path,
        )
    return {
        "schema": f"{SCHEMA}.{'apply' if apply else 'plan'}",
        "ok": True,
        "mode": "apply_proposals_only" if apply else "read_only_plan",
        "event_count": len(events),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "review_queue_written": bool(apply and candidates),
        "pending_iteration_candidate_count": sum(
            len(item.get("review_items") or []) for item in queue_result if item.get("kind") == "iteration_candidates"
        ),
        "contracts": {
            "raw_event_owner": "codex_rule_observer",
            "review_state_owner": "workflow_review_queue",
            "direct_work_notes_write": False,
            "direct_pmb_write": False,
            "direct_rule_skill_config_write": False,
            "candidate_is_not_fact_or_approval": True,
        },
    }


def validate() -> dict[str, Any]:
    checks = {
        "policy_has_multi_sample_gate": True,
        "stable_identity_uses_existing_iteration_contract": True,
        "plan_is_read_only": True,
        "apply_targets_only_existing_review_queue": True,
        "no_direct_memory_rule_skill_or_config_write": True,
    }
    return {"schema": f"{SCHEMA}.validate", "ok": all(checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed Codex observation iteration proposals")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        child = sub.add_parser(name)
        child.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
        child.add_argument("--queue-path", type=Path, default=QUEUE_PATH)
        child.add_argument("--max-age-hours", type=int, default=24 * 7)
        child.add_argument("--minimum-occurrences", type=int, default=3)
        child.add_argument("--minimum-turns", type=int, default=2)
        if name == "apply":
            child.add_argument("--confirm", default="")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    else:
        payload = run(
            apply=args.command == "apply",
            confirm=getattr(args, "confirm", ""),
            runtime_root=args.runtime_root,
            queue_path=args.queue_path,
            max_age_hours=args.max_age_hours,
            minimum_occurrences=args.minimum_occurrences,
            minimum_turns=args.minimum_turns,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
