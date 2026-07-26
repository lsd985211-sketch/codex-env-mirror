#!/usr/bin/env python3
"""Persistent review queue for workflow closeout.

Ownership: deterministic keys, de-duplication, and review dispositions.
Non-goals: decide whether an item is valuable, write memory, apply proposals,
or render final user prose.
State behavior: SQLite-backed pending/disposed state; no external actions.
Caller context: closeout package assembly and external knowledge pending plans
use this so multiple machine views do not become duplicate user-facing queues.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEY_FIELDS = ("source_item_id", "path", "source_url", "title")
QUEUE_PATH = Path(__file__).resolve().parent / "runtime" / "workflow_review_queue.sqlite"
DISPOSITIONS = {"approved", "revised", "rejected", "applied", "validated", "resolved", "deferred", "discarded"}
STATUS_TRANSITIONS = {
    "pending": {"approved", "revised", "rejected", "resolved", "deferred", "discarded"},
    "revised": {"pending", "approved", "rejected", "deferred", "discarded"},
    "approved": {"applied", "rejected", "deferred"},
    "applied": {"validated"},
    "validated": {"resolved"},
    "rejected": set(),
    "resolved": set(),
    "deferred": set(),
    "discarded": set(),
}
TERMINAL_STATUSES = {"rejected", "resolved", "deferred", "discarded"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_review_key(item: dict[str, Any], *, kind: str = "") -> str:
    """Return a stable identity key for one review item."""

    candidate_id = str(item.get("candidate_id") or "").strip().lower()
    if kind == "iteration_candidates" and re_full_iteration_id(candidate_id):
        return candidate_id
    for field in KEY_FIELDS:
        value = str(item.get(field) or "").strip()
        if value:
            return f"{kind}:{field}:{value}".lower()
    fallback = "\n".join(
        str(item.get(field) or "").strip()
        for field in ("summary", "detail", "proposed_destination_namespace")
        if str(item.get(field) or "").strip()
    )
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:16] if fallback else "empty"
    return f"{kind}:digest:{digest}"


def re_full_iteration_id(value: str) -> bool:
    return value.startswith("iteration:") and len(value) == len("iteration:") + 24 and all(
        char in "0123456789abcdef" for char in value.split(":", 1)[1]
    )


def unique_review_items(values: list[dict[str, Any]], *, kind: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Return review items de-duplicated by stable identity, preserving order."""

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        key = stable_review_key(value, kind=kind)
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
        if len(items) >= max(1, int(limit)):
            break
    return items


def content_digest(item: dict[str, Any]) -> str:
    content = {
        key: item.get(key)
        for key in (
            "title",
            "summary",
            "source_url",
            "path",
            "proposed_destination_namespace",
            "approval_action",
            "required_checks",
            "attributes",
            "candidate_id",
            "source_checkpoint",
            "stable_conclusion",
            "target_namespace",
            "affected_system",
        )
    }
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def connect(path: Path = QUEUE_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_items (
          review_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          content_digest TEXT NOT NULL,
          item_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          revision INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          disposed_at TEXT NOT NULL DEFAULT '',
          disposition_note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status, updated_at);
        """
    )
    return conn


def sync_review_groups(
    groups: list[dict[str, Any]],
    *,
    db_path: Path = QUEUE_PATH,
    authoritative_scopes: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        now = now_iso()
        current_ids: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                continue
            kind = str(group.get("kind") or "review")
            values = group.get("review_items") if isinstance(group.get("review_items"), list) else []
            for item in unique_review_items(values, kind=kind, limit=200):
                review_id = stable_review_key(item, kind=kind)
                current_ids.add(review_id)
                digest = content_digest(item)
                existing = conn.execute(
                    "SELECT status, content_digest, revision FROM review_items WHERE review_id = ?",
                    (review_id,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """INSERT INTO review_items
                        (review_id,kind,content_digest,item_json,status,revision,created_at,updated_at)
                        VALUES (?,?,?,?, 'pending', 1, ?, ?)""",
                        (review_id, kind, digest, json.dumps(item, ensure_ascii=False, sort_keys=True), now, now),
                    )
                elif str(existing["status"]) == "pending":
                    conn.execute(
                        "UPDATE review_items SET content_digest=?, item_json=?, updated_at=? WHERE review_id=?",
                        (digest, json.dumps(item, ensure_ascii=False, sort_keys=True), now, review_id),
                    )
                elif (
                    str(existing["status"]) == "resolved"
                    and str(
                        conn.execute(
                            "SELECT disposition_note FROM review_items WHERE review_id = ?",
                            (review_id,),
                        ).fetchone()["disposition_note"]
                    ).startswith("auto_resolved:")
                ) or str(existing["content_digest"]) != digest:
                    conn.execute(
                        """UPDATE review_items SET content_digest=?, item_json=?, status='pending',
                        revision=?, updated_at=?, disposed_at='', disposition_note='' WHERE review_id=?""",
                        (
                            digest,
                            json.dumps(item, ensure_ascii=False, sort_keys=True),
                            int(existing["revision"]) + 1,
                            now,
                            review_id,
                        ),
                    )
        for scope in authoritative_scopes or []:
            kind = str(scope.get("kind") or "").strip()
            source_prefix = str(scope.get("source_item_prefix") or "").strip().lower()
            if not kind or not source_prefix:
                continue
            scoped_rows = conn.execute(
                "SELECT review_id, item_json FROM review_items WHERE status='pending' AND kind=?",
                (kind,),
            ).fetchall()
            for row in scoped_rows:
                review_id = str(row["review_id"])
                if review_id in current_ids:
                    continue
                try:
                    item = json.loads(str(row["item_json"]))
                except json.JSONDecodeError:
                    continue
                source_item_id = str(item.get("source_item_id") or "").strip().lower()
                if not source_item_id.startswith(source_prefix):
                    continue
                conn.execute(
                    """UPDATE review_items SET status='resolved', updated_at=?, disposed_at=?,
                    disposition_note=? WHERE review_id=?""",
                    (
                        now,
                        now,
                        "auto_resolved: absent from fresh authoritative owner result",
                        review_id,
                    ),
                )
        conn.commit()
        rows = conn.execute(
            "SELECT review_id, kind, item_json FROM review_items WHERE status='pending' ORDER BY created_at, review_id"
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            item = json.loads(str(row["item_json"]))
        except json.JSONDecodeError:
            continue
        item["review_queue_id"] = str(row["review_id"])
        grouped.setdefault(str(row["kind"]), []).append(item)
    result: list[dict[str, Any]] = []
    templates = {str(group.get("kind") or "review"): group for group in groups if isinstance(group, dict)}
    for kind, items in grouped.items():
        template = dict(templates.get(kind) or {})
        template.update({"kind": kind, "count": len(items), "review_items": items})
        result.append(template)
    return result


def get_review_item(review_id: str, *, db_path: Path = QUEUE_PATH) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT review_id,kind,item_json,status,revision,created_at,updated_at,disposed_at,disposition_note "
            "FROM review_items WHERE review_id=?",
            (str(review_id or "").strip().lower(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "reason": "review_item_not_found", "review_id": review_id}
    try:
        item = json.loads(str(row["item_json"]))
    except json.JSONDecodeError:
        return {"ok": False, "reason": "review_item_json_invalid", "review_id": review_id}
    return {
        "ok": True,
        "review_id": str(row["review_id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "disposed_at": str(row["disposed_at"]),
        "disposition_note": str(row["disposition_note"]),
        "item": item,
    }


def transition(
    review_id: str,
    status: str,
    *,
    note: str = "",
    db_path: Path = QUEUE_PATH,
) -> dict[str, Any]:
    """Move one item through the guarded review lifecycle atomically."""

    review_id = str(review_id or "").strip().lower()
    status = str(status or "").strip().lower()
    if status not in STATUS_TRANSITIONS:
        return {"ok": False, "reason": "invalid_status", "allowed": sorted(STATUS_TRANSITIONS)}
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status FROM review_items WHERE review_id=?", (review_id,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "review_item_not_found", "review_id": review_id}
        current = str(row["status"])
        if status == current:
            conn.rollback()
            return {"ok": True, "review_id": review_id, "status": status, "unchanged": True}
        allowed = STATUS_TRANSITIONS.get(current, set())
        if status not in allowed:
            conn.rollback()
            return {
                "ok": False,
                "reason": "invalid_status_transition",
                "review_id": review_id,
                "from_status": current,
                "to_status": status,
                "allowed": sorted(allowed),
            }
        now = now_iso()
        disposed_at = now if status in TERMINAL_STATUSES else ""
        conn.execute(
            "UPDATE review_items SET status=?, updated_at=?, disposed_at=?, disposition_note=? WHERE review_id=?",
            (status, now, disposed_at, str(note or "")[:2000], review_id),
        )
        conn.commit()
        return {
            "ok": True,
            "review_id": review_id,
            "from_status": current,
            "status": status,
            "updated_at": now,
            "disposed_at": disposed_at,
        }
    finally:
        conn.close()


def dispose(review_id: str, disposition: str, *, note: str = "", db_path: Path = QUEUE_PATH) -> dict[str, Any]:
    """Compatibility facade for legacy callers; transitions remain guarded."""

    disposition = str(disposition or "").strip().lower()
    if disposition not in DISPOSITIONS:
        return {"ok": False, "reason": "invalid_disposition", "allowed": sorted(DISPOSITIONS)}
    return transition(review_id, disposition, note=note, db_path=db_path)


def snapshot(*, db_path: Path = QUEUE_PATH) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM review_items GROUP BY status").fetchall()
        pending = conn.execute(
            "SELECT review_id,kind,item_json,revision,updated_at FROM review_items WHERE status='pending' ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return {
        "schema": "workflow_review_queue.snapshot.v1",
        "ok": True,
        "path": str(db_path),
        "counts": {str(row["status"]): int(row["count"]) for row in rows},
        "pending": [
            {
                "review_id": str(row["review_id"]),
                "kind": str(row["kind"]),
                "revision": int(row["revision"]),
                "updated_at": str(row["updated_at"]),
                "item": json.loads(str(row["item_json"])),
            }
            for row in pending
        ],
    }


def validate(*, db_path: Path = QUEUE_PATH) -> dict[str, Any]:
    snap = snapshot(db_path=db_path)
    return {
        "schema": "workflow_review_queue.validate.v1",
        "ok": bool(snap.get("ok")) and Path(str(snap.get("path"))).suffix == ".sqlite",
        "counts": snap.get("counts", {}),
        "pending_count": len(snap.get("pending", [])),
        "contract": "draft storage is not a queue; closeout shows only persistent pending review items",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent workflow review queue")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("validate")
    get_item = sub.add_parser("get")
    get_item.add_argument("--review-id", required=True)
    move = sub.add_parser("transition")
    move.add_argument("--review-id", required=True)
    move.add_argument("--status", required=True, choices=sorted(STATUS_TRANSITIONS))
    move.add_argument("--note", default="")
    disposition = sub.add_parser("dispose")
    disposition.add_argument("--review-id", required=True)
    disposition.add_argument("--disposition", required=True, choices=sorted(DISPOSITIONS))
    disposition.add_argument("--note", default="")
    args = parser.parse_args()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "get":
        payload = get_review_item(args.review_id)
    elif args.command == "transition":
        payload = transition(args.review_id, args.status, note=args.note)
    else:
        payload = dispose(args.review_id, args.disposition, note=args.note)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
