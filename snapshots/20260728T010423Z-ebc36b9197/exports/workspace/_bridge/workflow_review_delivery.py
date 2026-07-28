#!/usr/bin/env python3
"""Review-package delivery transactions over the workflow review queue.

Ownership: package identity, delivery envelopes/receipts, and atomic user decisions.
Non-goals: candidate production, approval inference, owner application, or a second queue.
State behavior: reads and writes only tables in the workflow_review_queue SQLite database.
Caller context: workflow_review_queue facades and closeout delivery projection.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any


PACKAGE_SIZE = 10
PACKAGE_LIMIT = 12


def _queue():
    import workflow_review_queue

    return workflow_review_queue


def _db_path(db_path: Path | None) -> Path:
    return db_path or _queue().QUEUE_PATH


def _pending_delivery_rows(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    queue = _queue()
    conn = queue.connect(_db_path(db_path))
    try:
        rows = conn.execute(
            "SELECT review_id,kind,item_json,revision,created_at FROM review_items "
            "WHERE status='pending' ORDER BY kind,created_at,review_id"
        ).fetchall()
    finally:
        conn.close()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(str(row["item_json"]))
        except json.JSONDecodeError:
            item = {}
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        result.append({
            "review_id": str(row["review_id"]),
            "revision": int(row["revision"]),
            "kind": str(row["kind"]),
            "topic": "/".join(filter(None, [
                str(attributes.get("owner") or ""),
                str(row["kind"]),
                str(attributes.get("signal_kind") or ""),
            ])),
            "title": str(item.get("title") or row["review_id"]),
            "summary": str(item.get("summary") or item.get("detail") or ""),
            "source": str(item.get("source_url") or item.get("path") or "local queue"),
            "approval_action": str(item.get("approval_action") or "approve|reject|revise|defer"),
            "required_checks": item.get("required_checks") if isinstance(item.get("required_checks"), list) else [],
            "attributes": attributes,
        })
    return result


def prepare_delivery_packages(
    *,
    db_path: Path | None = None,
    package_size: int = PACKAGE_SIZE,
    package_limit: int = PACKAGE_LIMIT,
) -> dict[str, Any]:
    """Materialize stable bounded packages for current pending revisions."""

    queue = _queue()
    resolved_db = _db_path(db_path)
    rows = _pending_delivery_rows(db_path=resolved_db)
    safe_size = min(10, max(5, int(package_size)))
    safe_limit = min(12, max(1, int(package_limit)))
    if len(rows) > safe_size * safe_limit:
        return {
            "schema": "workflow_review_queue.packages.v1",
            "ok": False,
            "reason": "review_backlog_exceeds_package_capacity",
            "pending_count": len(rows),
            "capacity": safe_size * safe_limit,
        }
    memory_rows = [
        row for row in rows
        if row["kind"] == "iteration_candidates"
        and str(row.get("attributes", {}).get("owner") or "") == "memory_governance"
    ]
    technical_rows = [row for row in rows if row not in memory_rows]
    chunks = [
        cohort[index:index + safe_size]
        for cohort in (memory_rows, technical_rows)
        for index in range(0, len(cohort), safe_size)
    ]
    now = queue.now_iso()
    packages: list[dict[str, Any]] = []
    conn = queue.connect(resolved_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for chunk in chunks:
            refs = [{"review_id": row["review_id"], "revision": row["revision"]} for row in chunk]
            signature = hashlib.sha256(
                json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            package_id = f"review-package:{signature[:20]}"
            topics = sorted({str(row.get("topic") or row.get("kind") or "review") for row in chunk})
            topic = topics[0] if len(topics) == 1 else "mixed:" + ",".join(topics[:3])
            conn.execute(
                "INSERT INTO review_packages(package_id,package_signature,topic,item_refs_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?, 'prepared', ?, ?) ON CONFLICT(package_signature) DO UPDATE SET updated_at=excluded.updated_at",
                (package_id, signature, topic, json.dumps(refs, ensure_ascii=False, sort_keys=True), now, now),
            )
            packages.append({
                "package_id": package_id,
                "package_signature": signature,
                "topic": topic,
                "count": len(chunk),
                "item_refs": refs,
                "cards": chunk,
            })
        conn.commit()
    finally:
        conn.close()
    return {
        "schema": "workflow_review_queue.packages.v1",
        "ok": True,
        "pending_count": len(rows),
        "package_count": len(packages),
        "package_size": safe_size,
        "packages": packages,
    }


def prepare_delivery_envelope(
    package_id: str,
    *,
    target_ref: str = "current_task",
    channel: str = "codex_final_reply",
    db_path: Path | None = None,
) -> dict[str, Any]:
    queue = _queue()
    resolved_db = _db_path(db_path)
    packages = prepare_delivery_packages(db_path=resolved_db)
    package = next((item for item in packages.get("packages", []) if item.get("package_id") == package_id), None)
    if not package:
        return {"schema": "workflow_review_queue.delivery_envelope.v1", "ok": False, "reason": "package_not_current", "package_id": package_id}
    payload = {
        "schema": "workflow_review_queue.delivery_envelope.v1",
        "package_id": package_id,
        "package_signature": package["package_signature"],
        "topic": package["topic"],
        "item_refs": package["item_refs"],
        "cards": package["cards"],
        "target_ref": target_ref,
        "channel": channel,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    envelope_id = f"review-envelope:{digest[:20]}"
    payload.update({"envelope_id": envelope_id, "envelope_digest": digest, "status": "prepared"})
    conn = queue.connect(resolved_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO review_deliveries(envelope_id,package_id,envelope_digest,envelope_json,status,target_ref,channel,created_at) VALUES(?,?,?,?, 'prepared', ?, ?, ?)",
            (envelope_id, package_id, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True), target_ref, channel, queue.now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **payload}


def mark_delivery(envelope_id: str, *, response_ref: str, db_path: Path | None = None) -> dict[str, Any]:
    if not str(response_ref or "").strip():
        return {"ok": False, "reason": "response_persistence_ref_required"}
    queue = _queue()
    conn = queue.connect(_db_path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM review_deliveries WHERE envelope_id=?", (envelope_id,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "delivery_envelope_not_found", "envelope_id": envelope_id}
        now = queue.now_iso()
        conn.execute(
            "UPDATE review_deliveries SET status='delivered',response_ref=?,delivered_at=? WHERE envelope_id=?",
            (response_ref, now, envelope_id),
        )
        conn.commit()
        return {"ok": True, "envelope_id": envelope_id, "from_status": str(row["status"]), "status": "delivered", "response_ref": response_ref}
    finally:
        conn.close()


def decision_plan(
    package_id: str,
    action: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    envelope_id: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    queue = _queue()
    action = str(action or "").strip().lower()
    target_status = {"approve": "approved", "reject": "rejected", "revise": "revised", "defer": "deferred"}.get(action)
    if not target_status:
        return {"ok": False, "reason": "invalid_decision_action", "allowed": ["approve", "reject", "revise", "defer"]}
    conn = queue.connect(_db_path(db_path))
    try:
        row = conn.execute("SELECT item_refs_json FROM review_packages WHERE package_id=?", (package_id,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "package_not_found", "package_id": package_id}
        refs = json.loads(str(row["item_refs_json"]))
        include_set = {str(item).strip().lower() for item in include or [] if str(item).strip()}
        exclude_set = {str(item).strip().lower() for item in exclude or [] if str(item).strip()}
        selected = [ref for ref in refs if (not include_set or str(ref["review_id"]).lower() in include_set) and str(ref["review_id"]).lower() not in exclude_set]
        if not selected:
            return {"ok": False, "reason": "decision_selection_empty"}
        current = {
            str(item["review_id"]): item
            for item in conn.execute(
                f"SELECT review_id,status,revision FROM review_items WHERE review_id IN ({','.join('?' for _ in selected)})",
                tuple(ref["review_id"] for ref in selected),
            ).fetchall()
        }
        stale = [ref for ref in selected if str(ref["review_id"]) not in current or int(current[str(ref["review_id"])]["revision"]) != int(ref["revision"]) or str(current[str(ref["review_id"])]["status"]) != "pending"]
        if stale:
            return {"ok": False, "reason": "package_revision_or_status_stale", "stale_item_refs": stale}
        decision_id = f"review-decision:{uuid.uuid4().hex[:20]}"
        token_payload = {"decision_id": decision_id, "package_id": package_id, "action": action, "item_refs": selected}
        token = hashlib.sha256(json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO review_decisions(decision_id,confirm_token,package_id,envelope_id,action,item_refs_json,status,created_at) VALUES(?,?,?,?,?,?, 'planned', ?)",
            (decision_id, token, package_id, envelope_id, action, json.dumps(selected, sort_keys=True), queue.now_iso()),
        )
        conn.commit()
        return {"schema": "workflow_review_queue.decision_plan.v1", "ok": True, "decision_id": decision_id, "package_id": package_id, "action": action, "target_status": target_status, "item_refs": selected, "confirm_token": token}
    finally:
        conn.close()


def decision_apply(
    confirm_token: str,
    *,
    user_evidence_ref: str,
    note: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not str(user_evidence_ref or "").strip():
        return {"ok": False, "reason": "user_evidence_ref_required"}
    queue = _queue()
    conn = queue.connect(_db_path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM review_decisions WHERE confirm_token=?", (confirm_token,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "decision_confirmation_not_found"}
        if str(row["status"]) == "applied":
            conn.rollback()
            return json.loads(str(row["result_json"]))
        refs = json.loads(str(row["item_refs_json"]))
        action = str(row["action"])
        target_status = {"approve": "approved", "reject": "rejected", "revise": "revised", "defer": "deferred"}[action]
        results: list[dict[str, Any]] = []
        for ref in refs:
            current = conn.execute("SELECT status,revision,kind FROM review_items WHERE review_id=?", (ref["review_id"],)).fetchone()
            if current is None or str(current["status"]) != "pending" or int(current["revision"]) != int(ref["revision"]):
                conn.rollback()
                return {"ok": False, "reason": "decision_atomic_revision_conflict", "review_id": ref["review_id"]}
            now = queue.now_iso()
            disposed_at = now if target_status in queue.TERMINAL_STATUSES else ""
            conn.execute("UPDATE review_items SET status=?,updated_at=?,disposed_at=?,disposition_note=? WHERE review_id=?", (target_status, now, disposed_at, str(note or user_evidence_ref)[:2000], ref["review_id"]))
            results.append({
                "review_id": ref["review_id"],
                "revision": ref["revision"],
                "kind": str(current["kind"]),
                "from_status": "pending",
                "status": target_status,
            })
        decision_id = str(row["decision_id"])
        iteration_ids = [
            item["review_id"]
            for item in results
            if action == "approve" and item["kind"] == "iteration_candidates"
        ]
        lifecycle_handoff = {
            "required": bool(iteration_ids),
            "owner": "workflow_iteration_owner" if iteration_ids else "review_item_owner",
            "command": [
                "python",
                "_bridge/workflow_iteration_owner.py",
                "consume-approved",
                "--decision-id",
                decision_id,
                "--confirm-consume-approved",
            ] if iteration_ids else [],
            "review_ids": iteration_ids,
            "acceptance": "all selected iteration candidates read back status=resolved",
        }
        result = {
            "schema": "workflow_review_queue.decision_apply.v1",
            "ok": True,
            "decision_id": decision_id,
            "package_id": str(row["package_id"]),
            "action": action,
            "results": results,
            "lifecycle_handoff": lifecycle_handoff,
            "lifecycle_complete": not lifecycle_handoff["required"],
        }
        applied_at = queue.now_iso()
        conn.execute("UPDATE review_decisions SET status='applied',user_evidence_ref=?,result_json=?,applied_at=? WHERE decision_id=?", (user_evidence_ref, json.dumps(result, ensure_ascii=False, sort_keys=True), applied_at, row["decision_id"]))
        if str(row["envelope_id"]):
            conn.execute("UPDATE review_deliveries SET status='acknowledged',acknowledged_at=? WHERE envelope_id=?", (applied_at, row["envelope_id"]))
        conn.commit()
        return result
    finally:
        conn.close()


def decision_readback(decision_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    queue = _queue()
    conn = queue.connect(_db_path(db_path))
    try:
        row = conn.execute("SELECT * FROM review_decisions WHERE decision_id=?", (decision_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "reason": "decision_not_found", "decision_id": decision_id}
    result = json.loads(str(row["result_json"])) if str(row["result_json"]) else {}
    refs = json.loads(str(row["item_refs_json"]))
    conn = queue.connect(_db_path(db_path))
    try:
        current = conn.execute(
            f"SELECT review_id,kind,status,revision FROM review_items WHERE review_id IN ({','.join('?' for _ in refs)})",
            tuple(ref["review_id"] for ref in refs),
        ).fetchall() if refs else []
    finally:
        conn.close()
    statuses = [
        {
            "review_id": str(item["review_id"]),
            "kind": str(item["kind"]),
            "status": str(item["status"]),
            "revision": int(item["revision"]),
        }
        for item in current
    ]
    approved_iteration_statuses = [
        item["status"]
        for item in statuses
        if str(row["action"]) == "approve" and item["kind"] == "iteration_candidates"
    ]
    lifecycle_complete = bool(statuses) and all(status == "resolved" for status in approved_iteration_statuses)
    if not approved_iteration_statuses:
        lifecycle_complete = str(row["status"]) == "applied"
    return {
        "schema": "workflow_review_queue.decision_readback.v1",
        "ok": True,
        "decision_id": decision_id,
        "package_id": str(row["package_id"]),
        "status": str(row["status"]),
        "action": str(row["action"]),
        "user_evidence_ref": str(row["user_evidence_ref"]),
        "result": result,
        "item_statuses": statuses,
        "lifecycle_complete": lifecycle_complete,
        "required_next_action": (
            result.get("lifecycle_handoff", {}).get("command", [])
            if not lifecycle_complete
            else []
        ),
    }
