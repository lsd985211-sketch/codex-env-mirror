#!/usr/bin/env python3
"""Runtime projection for global maintenance convergence plans and results.

Ownership: derived plan artifacts, bounded history, result classification, and
handoff of complete approval cards to the existing workflow review queue.
Non-goals: owner command execution, approval inference, scheduling, retries,
or a second maintenance/review state machine.
State behavior: writes replaceable derived artifacts and review-queue rows only
for concrete approval-required results; owner runtime remains authoritative.
Caller context: the unified scheduler shadow task and Codex maintenance facade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maintenance_upgrade_governance import build_registry_convergence_plan, normalize_maintenance_result
from platform_paths import scheduler_state_root
from workflow_review_queue import QUEUE_PATH, prepare_delivery_packages, sync_review_groups


STATE_ROOT = scheduler_state_root() / "maintenance-convergence"
OUTPUT_ONLY_SIGNAL_PREFIXES = (
    "mirror.output",
    "mirror.snapshot",
    "mirror.release",
    "recovery.snapshot",
)
APPROVAL_REQUIRED_FIELDS = ("object", "risk", "recommended_action", "alternatives", "evidence_ref")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_plan_name(plan_id: str) -> str:
    digest = hashlib.sha256(str(plan_id or "").encode("utf-8")).hexdigest()[:24]
    return f"plan-{digest}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize_signals(signals: list[str]) -> dict[str, Any]:
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for raw in signals:
        signal = str(raw or "").strip()
        if not signal:
            continue
        if signal.startswith(OUTPUT_ONLY_SIGNAL_PREFIXES):
            rejected.append({"signal": signal, "reason": "downstream_mirror_output_cannot_trigger_source_maintenance"})
            continue
        if signal not in accepted:
            accepted.append(signal)
    return {"accepted": accepted, "rejected": rejected}


def persist_plan(plan: dict[str, Any], *, state_root: Path = STATE_ROOT) -> dict[str, Any]:
    plan_id = str(plan.get("plan_id") or "")
    if not plan_id:
        return {"ok": False, "reason": "plan_id_missing"}
    payload = {**plan, "persisted_at": now_iso(), "derived_runtime": True}
    artifact = state_root / "plans" / _safe_plan_name(plan_id)
    _write_json_atomic(artifact, payload)
    current = state_root / "current-plan.json"
    _write_json_atomic(
        current,
        {
            "schema": "maintenance_convergence.current_plan.v1",
            "ok": bool(plan.get("ok")),
            "plan_id": plan_id,
            "status": str(plan.get("status") or ""),
            "source_generation": str(plan.get("source_generation") or ""),
            "artifact_ref": str(artifact),
            "updated_at": payload["persisted_at"],
        },
    )
    return {"ok": True, "plan_id": plan_id, "artifact_ref": str(artifact), "current_ref": str(current)}


def load_plan(plan_id: str, *, state_root: Path = STATE_ROOT) -> dict[str, Any]:
    path = state_root / "plans" / _safe_plan_name(plan_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "reason": "plan_not_found", "plan_id": plan_id}
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "plan_artifact_invalid", "plan_id": plan_id, "error": str(exc)}
    if str(payload.get("plan_id") or "") != str(plan_id or ""):
        return {"ok": False, "reason": "plan_identity_mismatch", "plan_id": plan_id}
    return payload


def plan_history(*, state_root: Path = STATE_ROOT, limit: int = 20) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted((state_root / "plans").glob("plan-*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "plan_id": str(payload.get("plan_id") or ""),
                "status": str(payload.get("status") or ""),
                "system": str(payload.get("system") or ""),
                "source_generation": str(payload.get("source_generation") or ""),
                "persisted_at": str(payload.get("persisted_at") or ""),
                "artifact_ref": str(path),
            }
        )
        if len(rows) >= max(1, min(int(limit), 100)):
            break
    return {"schema": "maintenance_convergence.history.v1", "ok": True, "count": len(rows), "items": rows}


def build_lifecycle_projection(
    plan: dict[str, Any],
    *,
    tombstones: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive lifecycle action from membership and maintenance authorities."""

    if tombstones is None:
        from system_membership import retirement_tombstones

        tombstones = retirement_tombstones()
    coverage = plan.get("coverage") if isinstance(plan.get("coverage"), dict) else {}
    underutilized = {str(item) for item in coverage.get("underutilized_members", [])}
    items: list[dict[str, Any]] = []
    for disposition in coverage.get("member_dispositions", []):
        if not isinstance(disposition, dict):
            continue
        member_id = str(disposition.get("member_id") or "")
        needs_adaptation = member_id in underutilized
        items.append({
            "system": str(disposition.get("system") or ""),
            "member_id": member_id,
            "classification": "adapt_required" if needs_adaptation else "active_effective",
            "owner_evidence": str(disposition.get("disposition") or ""),
            "recommended_action": (
                "improve route, utilization evidence, or owner integration; preserve capability"
                if needs_adaptation
                else "reuse current owner and receipt by input signature"
            ),
        })
    known = {str(item.get("member_id") or "") for item in items}
    for member_id in sorted({str(item) for item in coverage.get("unmapped_members", []) if str(item)} - known):
        items.append({
            "system": "",
            "member_id": member_id,
            "classification": "blocked_unclassified",
            "owner_evidence": "maintenance_coverage_unmapped",
            "recommended_action": "resolve membership owner before adaptation or retirement",
        })
    for tombstone in tombstones or []:
        if not isinstance(tombstone, dict):
            continue
        lifecycle = str(tombstone.get("lifecycle") or "")
        classification = "retirement_candidate" if lifecycle == "retirement_candidate" else "historical_evidence_only"
        items.append({
            "system": str(tombstone.get("system") or ""),
            "member_id": str(tombstone.get("member_id") or tombstone.get("member") or ""),
            "classification": classification,
            "owner_evidence": str(tombstone.get("owner") or "membership_retirement_tombstone"),
            "replacement": str(tombstone.get("replacement") or ""),
            "recommended_action": (
                "consume system_membership retirement-plan before any write"
                if classification == "retirement_candidate"
                else "keep isolated as negative guard; do not route or reactivate"
            ),
        })
    names = (
        "active_effective",
        "adapt_required",
        "retirement_candidate",
        "historical_evidence_only",
        "blocked_unclassified",
    )
    counts = {name: sum(1 for item in items if item["classification"] == name) for name in names}
    return {
        "schema": "maintenance_convergence.lifecycle_projection.v1",
        "ok": not bool(counts["blocked_unclassified"]),
        "plan_id": str(plan.get("plan_id") or ""),
        "classification_basis": "membership_and_maintenance_authorities_only",
        "counts": counts,
        "items": items,
        "retirement_candidates": [item for item in items if item["classification"] == "retirement_candidate"],
        "rule": "classification is derived only; retirement still requires the membership owner plan, replacement or no-replacement evidence, data disposition, exit surfaces, receipt, and negative guard",
    }


def lifecycle_status(plan_id: str, *, state_root: Path = STATE_ROOT) -> dict[str, Any]:
    plan = load_plan(plan_id, state_root=state_root)
    if not plan.get("ok"):
        return plan
    from maintenance_capability_registry import global_coverage, parse_surface_map, source_signature

    rows = parse_surface_map()
    current_generation = source_signature(rows)
    projection = build_lifecycle_projection({**plan, "coverage": global_coverage(rows)})
    projection.update({
        "plan_source_generation": str(plan.get("source_generation") or ""),
        "current_source_generation": current_generation,
        "plan_stale": str(plan.get("source_generation") or "") != current_generation,
        "coverage_freshness": "live_authority_readback",
    })
    return projection


def load_terminal_receipts(*, state_root: Path = STATE_ROOT, limit: int = 2000) -> list[dict[str, Any]]:
    """Return the newest terminal receipt per node from the derived event log."""

    path = state_root / "result-events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 10000)) :]
    except OSError:
        return []
    newest: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        node_id = str(event.get("node_id") or "")
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        status = str(result.get("status") or "")
        signature = str(event.get("input_signature") or "")
        if not node_id or not signature or status not in {"healthy", "converged", "blocked", "failed"}:
            continue
        newest[node_id] = {
            "node_id": node_id,
            "input_signature": signature,
            "status": status,
            "recorded_at": str(event.get("recorded_at") or ""),
            "artifact_ref": str(result.get("artifact_ref") or event.get("owner_state_ref") or ""),
        }
    return [newest[node_id] for node_id in sorted(newest)]


def shadow_scan(
    *,
    intent: str,
    system: str = "",
    signals: list[str] | None = None,
    state_root: Path = STATE_ROOT,
) -> dict[str, Any]:
    signal_result = normalize_signals(list(signals or ["coverage.freshness"]))
    plan = build_registry_convergence_plan(
        intent=intent,
        system=system,
        signals=signal_result["accepted"],
        receipts=load_terminal_receipts(state_root=state_root),
        root_limit=32,
    )
    persistence = persist_plan(plan, state_root=state_root) if plan.get("plan_id") else {"ok": False, "reason": "plan_not_persisted"}
    return {
        "schema": "maintenance_convergence.shadow_scan.v1",
        "ok": bool(plan.get("ok")) and bool(persistence.get("ok")),
        "mode": "read-only-plan-derived-artifact",
        "auto_apply": False,
        "plan_id": str(plan.get("plan_id") or ""),
        "status": str(plan.get("status") or ""),
        "next_action": plan.get("next_action"),
        "block": plan.get("block"),
        "signals": signal_result,
        "coverage": plan.get("coverage"),
        "artifact_ref": str(persistence.get("artifact_ref") or ""),
        "rule": "the scheduler produces one plan artifact and never executes owner repair commands",
    }


def _complete_approval_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    complete: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for item in items:
        missing = [field for field in APPROVAL_REQUIRED_FIELDS if item.get(field) in (None, "", [])]
        if missing:
            incomplete.append({"item": item, "missing_fields": missing})
        else:
            complete.append(item)
    return complete, incomplete


def route_result(
    raw_result: dict[str, Any],
    *,
    plan_id: str,
    node_id: str,
    input_signature: str = "",
    db_path: Path = QUEUE_PATH,
    state_root: Path = STATE_ROOT,
) -> dict[str, Any]:
    normalized = normalize_maintenance_result(raw_result)
    status = str(normalized.get("status") or "failed")
    if status == "approval_required":
        complete, incomplete = _complete_approval_items(list(normalized.get("review_items") or []))
        if incomplete or not complete:
            status = "blocked"
            normalized = {
                **normalized,
                "ok": False,
                "status": status,
                "enqueue_review": False,
                "review_items": [],
                "reason": "approval_evidence_incomplete",
                "incomplete_items": incomplete,
            }
        else:
            queue_items: list[dict[str, Any]] = []
            for index, item in enumerate(complete):
                queue_items.append(
                    {
                        **item,
                        "source_item_id": str(item.get("source_item_id") or f"maintenance:{plan_id}:{node_id}:{index}"),
                        "title": str(item.get("title") or item.get("object") or node_id),
                        "summary": str(item.get("summary") or item.get("recommended_action") or ""),
                        "approval_action": str(item.get("approval_action") or "approve|reject|revise|defer"),
                        "source_checkpoint": str(item.get("source_checkpoint") or normalized.get("artifact_ref") or ""),
                        "affected_system": str(item.get("affected_system") or raw_result.get("system") or ""),
                    }
                )
            groups = sync_review_groups(
                [{"kind": "maintenance_approval", "review_items": queue_items}],
                db_path=db_path,
            )
            packages = prepare_delivery_packages(db_path=db_path)
            return {
                "schema": "maintenance_convergence.result_route.v1",
                "ok": True,
                "status": status,
                "plan_id": plan_id,
                "node_id": node_id,
                "review_groups": groups,
                "delivery_packages": packages,
                "immediate_handoff": True,
            }
    if status == "deferred":
        required = [field for field in ("owner", "deadline", "wake_condition") if raw_result.get(field) in (None, "")]
        if required:
            normalized = {**normalized, "ok": False, "status": "blocked", "reason": "deferred_contract_incomplete", "missing_fields": required}
            status = "blocked"
    event = {
        "schema": "maintenance_convergence.result_event.v1",
        "recorded_at": now_iso(),
        "plan_id": plan_id,
        "node_id": node_id,
        "input_signature": input_signature,
        "result": normalized,
        "owner_state_ref": str(raw_result.get("owner_state_ref") or ""),
    }
    event_path = state_root / "result-events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "schema": "maintenance_convergence.result_route.v1",
        "ok": status in {"healthy", "converged", "stale", "deferred"},
        "status": status,
        "plan_id": plan_id,
        "node_id": node_id,
        "enqueue_review": False,
        "event_ref": str(event_path),
        "result": normalized,
    }


def validate() -> dict[str, Any]:
    signals = normalize_signals(["coverage.freshness", "mirror.snapshot.created"])
    checks = [
        {"name": "mirror_output_negative_guard", "ok": len(signals["rejected"]) == 1},
        {"name": "source_signal_retained", "ok": signals["accepted"] == ["coverage.freshness"]},
        {"name": "approval_fields_declared", "ok": len(APPROVAL_REQUIRED_FIELDS) == 5},
    ]
    return {"schema": "maintenance_convergence_runtime.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintenance convergence runtime projection")
    sub = parser.add_subparsers(dest="command", required=True)
    shadow = sub.add_parser("shadow-scan")
    shadow.add_argument("--message", default="scheduled global maintenance coverage")
    shadow.add_argument("--system", default="")
    shadow.add_argument("--signal", action="append", default=[])
    status = sub.add_parser("status")
    status.add_argument("--plan-id", required=True)
    lifecycle = sub.add_parser("lifecycle-status")
    lifecycle.add_argument("--plan-id", required=True)
    history = sub.add_parser("history")
    history.add_argument("--limit", type=int, default=20)
    route = sub.add_parser("route-result")
    route.add_argument("--plan-id", required=True)
    route.add_argument("--node-id", required=True)
    route.add_argument("--input-signature", default="")
    source = route.add_mutually_exclusive_group(required=True)
    source.add_argument("--result-json", default="")
    source.add_argument("--result-path", default="")
    sub.add_parser("validate")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "shadow-scan":
        payload = shadow_scan(intent=args.message, system=args.system, signals=args.signal)
    elif args.command == "status":
        payload = load_plan(args.plan_id)
    elif args.command == "lifecycle-status":
        payload = lifecycle_status(args.plan_id)
    elif args.command == "history":
        payload = plan_history(limit=args.limit)
    elif args.command == "route-result":
        try:
            raw = json.loads(args.result_json) if args.result_json else json.loads(Path(args.result_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload = {"ok": False, "reason": "result_input_invalid", "error": str(exc)}
        else:
            payload = route_result(
                raw,
                plan_id=args.plan_id,
                node_id=args.node_id,
                input_signature=args.input_signature,
            )
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
