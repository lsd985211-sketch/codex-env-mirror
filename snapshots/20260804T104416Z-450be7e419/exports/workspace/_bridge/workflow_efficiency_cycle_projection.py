#!/usr/bin/env python3
"""Derive redacted end-to-end efficiency-cycle evidence from existing owners.

This module is a read-only projector. Rollout JSONL and owner receipts remain
authoritative; this module never stores prompts, commands, paths, bearer
tokens, full receipts, or authorization decisions and never changes a gate.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "workflow_efficiency_cycle_projection.v1"
EVENT_SCHEMA = "workflow_efficiency_cycle_projection.event.v1"
CYCLE_TYPES = (
    "routing_rebuild",
    "authorization_round_trip",
    "validation_replay",
    "source_drift_rework",
    "coordination_wait",
    "closeout_replay",
)

_TOOL_PATTERNS = {
    "routing_rebuild": ("workflow_orchestrator", "execution_route_pack", "workflow_route"),
    "authorization_round_trip": ("scoped_authorization", "request_user_input"),
    "validation_replay": ("workflow_validation", "rule_governance", "system_membership"),
    "coordination_wait": ("wait", "wait_agent", "wait_threads", "wait_thread"),
    "closeout_replay": ("codex_workflow_entry", "workflow_closeout"),
}


def _digest(value: Any, *, length: int = 24) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _json_rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _tool_names(rollout_path: Path) -> list[str]:
    names: list[str] = []
    for row in _json_rows(rollout_path):
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if str(payload.get("type") or "") not in {"custom_tool_call", "function_call", "mcp_tool_call"}:
            continue
        name = str(payload.get("name") or "").strip().lower()
        if name:
            names.append(name[:160])
    return names


def _read_receipts(receipt_root: Path, task_ref: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not receipt_root.is_dir():
        return rows
    for path in sorted(receipt_root.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
        task_id = str(plan.get("task_id") or "")
        branch = str(plan.get("branch") or "")
        if task_ref and task_ref not in {task_id, branch.removeprefix("codex/task/")}:
            continue
        rows.append(item)
    return rows


def _receipt_facts(receipts: list[dict[str, Any]]) -> tuple[Counter[str], dict[str, Any]]:
    counts: Counter[str] = Counter()
    steps: Counter[str] = Counter()
    source_heads: set[str] = set()
    lifecycle_keys: set[str] = set()
    for item in receipts:
        schema = str(item.get("schema") or "")
        step = next((name for name in ("start", "commit", "sync", "integrate") if f".{name}." in schema), "")
        if step:
            steps[step] += 1
        plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
        after = item.get("after") if isinstance(item.get("after"), dict) else {}
        base = str(plan.get("base_commit") or "")
        head = str(after.get("head") or plan.get("head") or "")
        if base:
            source_heads.add(base)
        if head:
            source_heads.add(head)
        lifecycle_keys.add(str(plan.get("task_id") or plan.get("branch") or schema))
    repeated_steps = sum(max(0, value - 1) for value in steps.values())
    if repeated_steps:
        counts["closeout_replay"] += repeated_steps
    if len(source_heads) > 2 or (steps["start"] > 1 and steps["commit"] > 1):
        counts["source_drift_rework"] += max(1, len(source_heads) - 2, steps["start"] - 1)
    return counts, {
        "receipt_count": len(receipts),
        "lifecycle_count": len(lifecycle_keys),
        "source_generation_count": len(source_heads),
        "completed_step_count": sum(steps.values()),
    }


def project_efficiency_cycles(
    *, rollout_path: Path, task_ref: str, receipt_root: Path | None = None
) -> dict[str, Any]:
    """Return a stable redacted projection; this function performs no writes."""

    rollout_path = Path(rollout_path)
    if not rollout_path.is_file():
        return {
            "schema": SCHEMA,
            "ok": False,
            "reason": "rollout_unavailable",
            "events": [],
            "writes_observer_events": False,
        }
    task_key = _digest({"task_ref": task_ref or rollout_path.stem})
    tool_names = _tool_names(rollout_path)
    counts: Counter[str] = Counter()
    for cycle_type, patterns in _TOOL_PATTERNS.items():
        matched = sum(any(pattern in name for pattern in patterns) for name in tool_names)
        if matched > 1:
            counts[cycle_type] += matched - 1
    receipts = _read_receipts(Path(receipt_root), task_ref) if receipt_root else []
    receipt_counts, receipt_summary = _receipt_facts(receipts)
    counts.update(receipt_counts)
    events: list[dict[str, Any]] = []
    for cycle_type in CYCLE_TYPES:
        occurrence_count = int(counts.get(cycle_type, 0))
        if occurrence_count <= 0:
            continue
        evidence_summary = {
            "rollout_tool_event_count": len(tool_names),
            **receipt_summary,
        }
        identity = {"task_key": task_key, "cycle_type": cycle_type}
        events.append(
            {
                "schema": EVENT_SCHEMA,
                "event": "EfficiencyCycle",
                "event_id": _digest(identity),
                "task_key": task_key,
                "cycle_type": cycle_type,
                "occurrence_count": occurrence_count,
                "measurement_confidence": "high" if receipts else "medium",
                "evidence_signature": _digest({
                    **identity,
                    "occurrences": occurrence_count,
                    "evidence": evidence_summary,
                }),
                "evidence_summary": evidence_summary,
                "stores_prompt": False,
                "stores_command": False,
                "stores_path": False,
                "stores_authorization_token": False,
                "stores_full_receipt": False,
                "changes_authorization": False,
            }
        )
    return {
        "schema": SCHEMA,
        "ok": True,
        "task_key": task_key,
        "event_count": len(events),
        "events": events,
        "writes_observer_events": False,
        "writes_review_queue": False,
        "contracts": {
            "rollout_owner_retained": True,
            "receipt_owner_retained": True,
            "projection_is_business_acceptance": False,
            "authorization_is_not_reinterpreted": True,
        },
    }
