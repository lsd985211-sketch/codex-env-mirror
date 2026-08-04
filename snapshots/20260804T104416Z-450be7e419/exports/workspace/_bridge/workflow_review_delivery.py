#!/usr/bin/env python3
"""Review-package delivery transactions over the workflow review queue.

Ownership: package identity, delivery envelopes/receipts, and atomic user decisions.
Non-goals: candidate production, approval inference, owner application, or a second queue.
State behavior: reads and writes review-queue tables, and asks the single scoped-authorization owner to create, inspect, fence, and consume exact approval evidence.
Caller context: workflow_review_queue facades and closeout delivery projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_review_presentation import project_card


PACKAGE_SIZE = 10
PACKAGE_LIMIT = 12


def _queue():
    import workflow_review_queue

    return workflow_review_queue


def _db_path(db_path: Path | None) -> Path:
    return db_path or _queue().QUEUE_PATH


def _authorization():
    import scoped_authorization

    return scoped_authorization


def _consume_approved_iteration_lifecycle(
    decision_id: str,
    *,
    db_path: Path | None,
    memory_index_path: Path | None,
    backup: bool,
) -> dict[str, Any]:
    """Run the already-approved bounded iteration lifecycle through its owner."""

    from workflow_iteration_owner import consume_approved

    kwargs: dict[str, Any] = {
        "decision_id": decision_id,
        "confirm": True,
        "db_path": _db_path(db_path),
        "backup": backup,
    }
    if memory_index_path is not None:
        kwargs["memory_index_path"] = memory_index_path
    return consume_approved(**kwargs)


def _complete_iteration_lifecycle(
    result: dict[str, Any],
    *,
    decision_id: str,
    db_path: Path | None,
    memory_index_path: Path | None,
    backup: bool,
) -> dict[str, Any]:
    """Consume only the approved decision handoff; retain unresolved owners visibly."""

    handoff = result.get("lifecycle_handoff") if isinstance(result.get("lifecycle_handoff"), dict) else {}
    if not handoff.get("required"):
        return result
    consumption = _consume_approved_iteration_lifecycle(
        decision_id,
        db_path=db_path,
        memory_index_path=memory_index_path,
        backup=backup,
    )
    complete = bool(consumption.get("ok") and consumption.get("lifecycle_complete"))
    handoff["automatic_execution"] = {
        "attempted": True,
        "completed": complete,
        "reason": str(consumption.get("reason") or ""),
    }
    handoff["required"] = not complete
    if complete:
        handoff["command"] = []
    result["lifecycle_handoff"] = handoff
    result["lifecycle_complete"] = complete
    result["lifecycle_result"] = consumption
    return result


def _decision_scope(row: Any) -> dict[str, str]:
    auth = _authorization()
    raw_scope = str(row["authorization_scope_json"] or "").strip()
    if raw_scope:
        try:
            stored = json.loads(raw_scope)
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict) and all(str(stored.get(field) or "") for field in auth.SCOPE_FIELDS):
            return {field: str(stored[field]) for field in auth.SCOPE_FIELDS}
    refs = json.loads(str(row["item_refs_json"]))
    target = {
        "package_id": str(row["package_id"]),
        "action": str(row["action"]),
        "item_refs": refs,
    }
    return auth.build_scope(
        thread_id=str(row["authorization_thread_id"]),
        action=f"workflow_review.{row['action']}",
        target=target,
        risk="high" if str(row["action"]) == "approve" else "medium",
        phase="decision_apply",
        source_signature=hashlib.sha256(
            json.dumps(target, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        requested_by_owner="workflow_review_delivery",
    )


def _bound_thread_id(target_ref: str) -> str:
    value = str(target_ref or "").strip()
    return value.removeprefix("thread:") if value.startswith("thread:") else ""


def _approval_projection(planned: dict[str, Any], *, authorization_state_root: Path | str | None) -> dict[str, Any]:
    challenge = planned.get("authorization_challenge") if isinstance(planned.get("authorization_challenge"), dict) else {}
    presentation = _authorization().challenge_presentation(
        str(challenge.get("challenge_ref") or ""), response_token=str(challenge.get("response_token") or ""),
        state_root=authorization_state_root,
    )
    if not presentation.get("ok"):
        return {
            "mode": "preissued_package_approve", "decision_id": str(planned.get("decision_id") or ""),
            "challenge_ref": "", "response_token": "", "presentation_reason": str(presentation.get("reason") or ""),
            "item_refs": planned.get("item_refs") if isinstance(planned.get("item_refs"), list) else [],
        }
    return {
        "mode": "preissued_package_approve",
        "decision_id": str(planned.get("decision_id") or ""),
        "challenge_ref": str(presentation.get("challenge_ref") or ""),
        "response_token": str(presentation.get("response_token") or ""),
        "scope_signature": str(presentation.get("scope_signature") or ""),
        "status": str(presentation.get("status") or "pending"),
        "issued_at": str(presentation.get("issued_at") or ""),
        "expires_at": str(presentation.get("expires_at") or ""),
        "presentation_mode": str(presentation.get("presentation_mode") or ""),
        "item_refs": planned.get("item_refs") if isinstance(planned.get("item_refs"), list) else [],
    }


def _active_preissued_approval(approval: dict[str, Any], *, authorization_state_root: Path | str | None) -> bool:
    challenge_ref = str(approval.get("challenge_ref") or "")
    if not challenge_ref:
        return False
    snapshot = _authorization().challenge_snapshot(challenge_ref, state_root=authorization_state_root)
    return bool(snapshot.get("ok")) and str(snapshot.get("status") or "") in {"pending", "granted"}


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
        result.append(project_card({
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
        }))
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
    authorization_state_root: Path | str | None = None,
) -> dict[str, Any]:
    queue = _queue()
    resolved_db = _db_path(db_path)
    packages = prepare_delivery_packages(db_path=resolved_db)
    package = next((item for item in packages.get("packages", []) if item.get("package_id") == package_id), None)
    if not package:
        return {"schema": "workflow_review_queue.delivery_envelope.v1", "ok": False, "reason": "package_not_current", "package_id": package_id}
    cards = package["cards"]
    incomplete_cards = [
        {
            "review_id": str(card.get("review_id") or ""),
            "revision": int(card.get("revision") or 0),
            "title": str(card.get("title") or ""),
        }
        for card in cards
        if card.get("presentation_complete") is False
    ]
    if incomplete_cards:
        return {
            "schema": "workflow_review_queue.delivery_envelope.v1",
            "ok": False,
            "reason": "approval_presentation_incomplete",
            "package_id": package_id,
            "incomplete_cards": incomplete_cards,
            "required_next_action": "producing owner supplies a Chinese-primary title and summary, then prepares a new revision",
        }
    thread_id = _bound_thread_id(target_ref)
    reissue_of = ""
    if thread_id:
        conn = queue.connect(resolved_db)
        try:
            latest = conn.execute(
                "SELECT envelope_id,envelope_json,status,response_ref,response_digest "
                "FROM review_deliveries WHERE package_id=? AND target_ref=? AND channel=? "
                "ORDER BY created_at DESC LIMIT 1",
                (package_id, target_ref, channel),
            ).fetchone()
        finally:
            conn.close()
        if latest is not None:
            try:
                stored = json.loads(str(latest["envelope_json"]))
            except json.JSONDecodeError:
                stored = {}
            stored_approval = stored.get("approval") if isinstance(stored.get("approval"), dict) else {}
            if _active_preissued_approval(stored_approval, authorization_state_root=authorization_state_root):
                stored["status"] = str(latest["status"])
                if stored["status"] in {"delivered", "acknowledged"}:
                    stored["response_ref"] = str(latest["response_ref"] or "")
                    stored["response_digest"] = str(latest["response_digest"] or "")
                return {"ok": True, **stored}
            if str(latest["status"]) in {"delivered", "acknowledged"}:
                reissue_of = str(latest["envelope_id"])
    identity = {
        "schema": "workflow_review_queue.delivery_envelope.v1",
        "package_id": package_id,
        "package_signature": package["package_signature"],
        "topic": package["topic"],
        "item_refs": package["item_refs"],
        "cards": cards,
        "target_ref": target_ref,
        "channel": channel,
    }
    if reissue_of:
        identity["authorization_reissue_of"] = reissue_of
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    envelope_id = f"review-envelope:{digest[:20]}"
    approval: dict[str, Any] = {}
    if thread_id:
        planned = decision_plan(
            package_id,
            "approve",
            envelope_id=envelope_id,
            db_path=resolved_db,
            thread_id=thread_id,
            authorization_state_root=authorization_state_root,
            preissued=True,
        )
        if not planned.get("ok"):
            return {"schema": "workflow_review_queue.delivery_envelope.v1", "ok": False, "reason": "preissued_approval_plan_failed", "authorization": planned}
        approval = _approval_projection(planned, authorization_state_root=authorization_state_root)
    payload = {
        **identity,
        "approval": approval,
        "envelope_id": envelope_id,
        "envelope_digest": digest,
        "status": "prepared",
    }
    payload["markdown"] = render_delivery_markdown(cards, approval=approval)
    payload["authorization_presentation_digest"] = hashlib.sha256(
        json.dumps({"envelope_id": envelope_id, "cards": cards, "approval": approval}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    conn = queue.connect(resolved_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO review_deliveries(envelope_id,package_id,envelope_digest,envelope_json,status,target_ref,channel,created_at) VALUES(?,?,?,?, 'prepared', ?, ?, ?)",
            (envelope_id, package_id, digest, json.dumps(payload, ensure_ascii=False, sort_keys=True), target_ref, channel, queue.now_iso()),
        )
        conn.execute(
            "UPDATE review_deliveries SET envelope_json=? WHERE envelope_id=? AND status='prepared'",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), envelope_id),
        )
        conn.commit()
        persisted = conn.execute(
            "SELECT envelope_json,status,response_ref,response_digest FROM review_deliveries WHERE envelope_id=?",
            (envelope_id,),
        ).fetchone()
    finally:
        conn.close()
    if persisted is not None:
        try:
            payload = json.loads(str(persisted["envelope_json"]))
        except json.JSONDecodeError:
            return {"ok": False, "reason": "delivery_envelope_json_invalid", "envelope_id": envelope_id}
        payload["status"] = str(persisted["status"])
        if payload["status"] in {"delivered", "acknowledged"}:
            payload["response_ref"] = str(persisted["response_ref"] or "")
            payload["response_digest"] = str(persisted["response_digest"] or "")
    return {"ok": True, **payload}


def render_delivery_markdown(cards: list[dict[str, Any]], *, approval: dict[str, Any] | None = None) -> str:
    """Render exact owner-controlled evidence for final-response delivery."""

    sections: list[str] = []
    for index, card in enumerate(cards, start=1):
        summary = str(card.get("summary") or "").strip()
        summary_digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        sections.append(
            "\n".join(
                [
                    f"### 审批卡片 {index}：{card.get('title', '')}",
                    f"- 标识：{card.get('review_id', '')}",
                    f"- 修订号：{card.get('revision', '')}",
                    f"- 类型：{card.get('kind', '')}",
                    f"- 摘要：{summary}",
                    f"- 摘要 SHA-256：sha256:{summary_digest}",
                    f"- 来源：{card.get('source', '')}",
                    f"- 审批动作：{card.get('approval_action', '')}",
                    "- 必需检查：" + "；".join(str(item) for item in card.get("required_checks", [])),
                ]
            )
        )
    if approval and str(approval.get("response_token") or ""):
        refs = approval.get("item_refs") if isinstance(approval.get("item_refs"), list) else []
        scope = "、".join(f"{item.get('review_id', '')}@r{item.get('revision', '')}" for item in refs if isinstance(item, dict))
        sections.append(
            "\n".join(
                [
                    "### 本包批准授权",
                    f"- 范围：{scope}",
                    "- 回复以下单次授权码，即批准本包全部所列 revision：",
                    f"`{approval['response_token']}`",
                    f"- 状态：{approval.get('status', 'pending')}",
                ]
            )
        )
    return "\n\n".join(sections)


def mark_delivery(envelope_id: str, *, response_ref: str, db_path: Path | None = None) -> dict[str, Any]:
    """Reject the legacy unverified delivery write path.

    A non-empty reference never proves that the response contains the cards.
    Call :func:`verify_delivery` with the authoritative rollout instead.
    """

    return {
        "schema": "workflow_review_queue.delivery_verification.v2",
        "ok": False,
        "reason": "response_backed_delivery_verification_required",
        "envelope_id": str(envelope_id or ""),
        "legacy_response_ref_accepted": False,
        "required_next_action": "verify-delivery --envelope-id <id> --rollout-path <authoritative-jsonl> --thread-id <id>",
    }


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _message_text(payload: dict[str, Any]) -> str:
    values: list[str] = []
    for item in payload.get("content") if isinstance(payload.get("content"), list) else []:
        if not isinstance(item, dict) or str(item.get("type") or "") != "output_text":
            continue
        values.append(str(item.get("text") or ""))
    return "\n".join(values).strip()


def _rollout_final_responses(
    rollout_path: Path,
    *,
    expected_thread_id: str,
    created_after: datetime,
) -> dict[str, Any]:
    """Return only assistant messages confirmed by a task_complete event."""

    thread_id = ""
    messages: dict[str, dict[str, Any]] = {}
    completions: list[dict[str, Any]] = []
    try:
        with rollout_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("type") == "session_meta":
                    thread_id = str(payload.get("id") or payload.get("session_id") or "").strip()
                    continue
                timestamp = _parse_time(event.get("timestamp"))
                if timestamp is None or timestamp <= created_after:
                    continue
                if (
                    event.get("type") == "response_item"
                    and payload.get("type") == "message"
                    and payload.get("role") == "assistant"
                ):
                    metadata = payload.get("internal_chat_message_metadata_passthrough")
                    metadata = metadata if isinstance(metadata, dict) else {}
                    turn_id = str(metadata.get("turn_id") or "").strip()
                    text = _message_text(payload)
                    if turn_id and text:
                        messages[turn_id] = {
                            "turn_id": turn_id,
                            "text": text,
                            "timestamp": timestamp,
                            "message_id": str(payload.get("id") or ""),
                            "response_ref": f"{rollout_path.name}:line:{line_number}",
                        }
                elif event.get("type") == "event_msg" and payload.get("type") == "task_complete":
                    completions.append({
                        "turn_id": str(payload.get("turn_id") or "").strip(),
                        "text": str(payload.get("last_agent_message") or "").strip(),
                        "timestamp": timestamp,
                    })
    except OSError as exc:
        return {"ok": False, "reason": "rollout_read_failed", "detail": str(exc)}
    if thread_id != expected_thread_id:
        return {
            "ok": False,
            "reason": "delivery_rollout_thread_mismatch",
            "expected_thread_id": expected_thread_id,
            "actual_thread_id": thread_id,
        }
    finals: list[dict[str, Any]] = []
    for completion in completions:
        message = messages.get(completion["turn_id"])
        if not message or message["text"] != completion["text"]:
            continue
        finals.append(message)
    finals.sort(key=lambda item: item["timestamp"])
    return {"ok": True, "thread_id": thread_id, "responses": finals}


def _card_delivery_evidence(card: dict[str, Any], text: str) -> dict[str, Any]:
    review_id = str(card.get("review_id") or "").strip()
    title = str(card.get("title") or "").strip()
    summary = str(card.get("summary") or "").strip()
    summary_digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    summary_marker = f"sha256:{summary_digest}"
    return {
        "review_id": review_id,
        "id_present": bool(review_id) and review_id in text,
        "title_present": bool(title) and title in text,
        "summary_present": bool(summary) and (summary in text or summary_marker in text),
        "summary_digest": summary_digest,
    }


def verify_delivery(
    envelope_id: str,
    *,
    rollout_path: Path,
    expected_thread_id: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Atomically bind an envelope to one complete persisted final response."""

    queue = _queue()
    resolved_db = _db_path(db_path)
    conn = queue.connect(resolved_db)
    try:
        row = conn.execute(
            "SELECT envelope_json,status,response_ref,response_digest,created_at,target_ref FROM review_deliveries WHERE envelope_id=?",
            (envelope_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "reason": "delivery_envelope_not_found", "envelope_id": envelope_id}
    try:
        envelope = json.loads(str(row["envelope_json"]))
    except json.JSONDecodeError:
        return {"ok": False, "reason": "delivery_envelope_json_invalid", "envelope_id": envelope_id}
    target_ref = str(row["target_ref"] or "")
    if not target_ref.startswith("thread:"):
        return {
            "ok": False,
            "reason": "delivery_envelope_thread_unbound",
            "envelope_id": envelope_id,
            "target_ref": target_ref,
        }
    bound_thread_id = target_ref.removeprefix("thread:")
    if bound_thread_id != expected_thread_id:
        return {"ok": False, "reason": "delivery_envelope_thread_mismatch", "envelope_id": envelope_id}
    created_at = _parse_time(row["created_at"])
    if created_at is None:
        return {"ok": False, "reason": "delivery_envelope_timestamp_invalid", "envelope_id": envelope_id}
    rollout = _rollout_final_responses(
        Path(rollout_path).resolve(), expected_thread_id=expected_thread_id, created_after=created_at
    )
    if not rollout.get("ok"):
        return {"schema": "workflow_review_queue.delivery_verification.v2", "envelope_id": envelope_id, **rollout}
    cards = envelope.get("cards") if isinstance(envelope.get("cards"), list) else []
    if not cards:
        return {"ok": False, "reason": "delivery_envelope_cards_missing", "envelope_id": envelope_id}
    matched: dict[str, Any] | None = None
    matched_evidence: list[dict[str, Any]] = []
    last_evidence: list[dict[str, Any]] = []
    approval = envelope.get("approval") if isinstance(envelope.get("approval"), dict) else {}
    response_token = str(approval.get("response_token") or "")
    last_approval_evidence: dict[str, Any] = {"required": bool(response_token), "response_token_present": not bool(response_token)}
    for response in rollout.get("responses", []):
        evidence = [_card_delivery_evidence(card, response["text"]) for card in cards]
        last_evidence = evidence
        approval_evidence = {
            "required": bool(response_token),
            "challenge_ref": str(approval.get("challenge_ref") or ""),
            "response_token_present": bool(response_token) and response_token in response["text"],
        }
        last_approval_evidence = approval_evidence
        if all(item["id_present"] and item["title_present"] and item["summary_present"] for item in evidence) and (
            not response_token or approval_evidence["response_token_present"]
        ):
            matched = response
            matched_evidence = evidence
            break
    if matched is None:
        reason = "final_response_missing_preissued_authorization_evidence" if response_token and last_evidence and all(
            item["id_present"] and item["title_present"] and item["summary_present"] for item in last_evidence
        ) else "final_response_missing_review_card_evidence"
        return {
            "schema": "workflow_review_queue.delivery_verification.v2",
            "ok": False,
            "reason": reason,
            "envelope_id": envelope_id,
            "final_response_count": len(rollout.get("responses", [])),
            "card_evidence": last_evidence,
            "approval_evidence": last_approval_evidence,
        }
    response_digest = hashlib.sha256(matched["text"].encode("utf-8")).hexdigest()
    verification = {
        "schema": "workflow_review_queue.delivery_evidence.v2",
        "thread_id": expected_thread_id,
        "turn_id": matched["turn_id"],
        "message_id": matched["message_id"],
        "response_digest": response_digest,
        "card_evidence": matched_evidence,
        "approval_evidence": last_approval_evidence,
    }
    queue = _queue()
    conn = queue.connect(resolved_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status,response_ref,response_digest FROM review_deliveries WHERE envelope_id=?", (envelope_id,)
        ).fetchone()
        if current is None:
            conn.rollback()
            return {"ok": False, "reason": "delivery_envelope_not_found", "envelope_id": envelope_id}
        if str(current["status"]) in {"delivered", "acknowledged"}:
            conn.rollback()
            if str(current["response_digest"] or "") == response_digest:
                return {
                    "schema": "workflow_review_queue.delivery_verification.v2",
                    "ok": True,
                    "envelope_id": envelope_id,
                    "status": str(current["status"]),
                    "response_ref": str(current["response_ref"]),
                    "response_digest": response_digest,
                    "reused": True,
                }
            return {"ok": False, "reason": "delivered_response_binding_conflict", "envelope_id": envelope_id}
        now = queue.now_iso()
        conn.execute(
            "UPDATE review_deliveries SET status='delivered',response_ref=?,response_digest=?,verification_json=?,delivered_at=? WHERE envelope_id=? AND status='prepared'",
            (matched["response_ref"], response_digest, json.dumps(verification, ensure_ascii=False, sort_keys=True), now, envelope_id),
        )
        conn.commit()
        return {
            "schema": "workflow_review_queue.delivery_verification.v2",
            "ok": True,
            "envelope_id": envelope_id,
            "from_status": str(current["status"]),
            "status": "delivered",
            "response_ref": matched["response_ref"],
            "response_digest": response_digest,
            "turn_id": matched["turn_id"],
            "verified_card_count": len(matched_evidence),
            "reused": False,
        }
    finally:
        conn.close()


def reconcile_prepared_deliveries(
    *,
    rollout_path: Path,
    expected_thread_id: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Verify prior envelopes bound to this task before creating another."""

    queue = _queue()
    conn = queue.connect(_db_path(db_path))
    try:
        rows = conn.execute(
            "SELECT envelope_id FROM review_deliveries WHERE status='prepared' AND target_ref=? ORDER BY created_at",
            (f"thread:{expected_thread_id}",),
        ).fetchall()
    finally:
        conn.close()
    results = [
        verify_delivery(
            str(row["envelope_id"]),
            rollout_path=rollout_path,
            expected_thread_id=expected_thread_id,
            db_path=db_path,
        )
        for row in rows
    ]
    return {
        "schema": "workflow_review_queue.delivery_reconciliation.v1",
        "ok": all(item.get("ok") for item in results),
        "thread_id": expected_thread_id,
        "prepared_count": len(rows),
        "delivered_count": sum(1 for item in results if item.get("ok")),
        "results": results,
    }


def resolve_rollout_path(expected_thread_id: str, *, codex_home: Path | None = None) -> dict[str, Any]:
    """Resolve the current task rollout by stable thread identity."""

    thread_id = str(expected_thread_id or "").strip()
    if not thread_id:
        return {"ok": False, "reason": "delivery_thread_id_required"}
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()
    candidates = [
        *home.glob(f"sessions/**/rollout-*{thread_id}.jsonl"),
        *home.glob(f"archived_sessions/rollout-*{thread_id}.jsonl"),
    ]
    files = [path.resolve() for path in candidates if path.is_file()]
    if not files:
        return {"ok": False, "reason": "delivery_rollout_not_found", "thread_id": thread_id}
    selected = max(files, key=lambda path: path.stat().st_mtime_ns)
    return {"ok": True, "thread_id": thread_id, "rollout_path": str(selected)}


def decision_plan(
    package_id: str,
    action: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    envelope_id: str = "",
    db_path: Path | None = None,
    thread_id: str = "",
    authorization_state_root: Path | str | None = None,
    preissued: bool = False,
) -> dict[str, Any]:
    queue = _queue()
    action = str(action or "").strip().lower()
    target_status = {"approve": "approved", "reject": "rejected", "revise": "revised", "defer": "deferred"}.get(action)
    if not target_status:
        return {"ok": False, "reason": "invalid_decision_action", "allowed": ["approve", "reject", "revise", "defer"]}
    conn = queue.connect(_db_path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT item_refs_json,package_signature FROM review_packages WHERE package_id=?", (package_id,)).fetchone()
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
        authorization_thread_id = str(thread_id or os.environ.get("CODEX_THREAD_ID") or "").strip()
        if not authorization_thread_id:
            return {"ok": False, "reason": "authorization_thread_id_required"}
        if preissued and not envelope_id:
            return {"ok": False, "reason": "preissued_approval_envelope_required"}
        if preissued:
            selected_ids = [str(ref["review_id"]) for ref in selected]
            item_rows = conn.execute(
                f"SELECT review_id,revision,content_digest FROM review_items WHERE review_id IN ({','.join('?' for _ in selected_ids)})",
                tuple(selected_ids),
            ).fetchall()
            digests = {str(item["review_id"]): item for item in item_rows}
            if len(digests) != len(selected_ids):
                return {"ok": False, "reason": "preissued_approval_items_missing"}
            scoped_refs = [
                {"review_id": str(ref["review_id"]), "revision": int(ref["revision"]), "content_digest": str(digests[str(ref["review_id"])]["content_digest"])}
                for ref in selected
            ]
            target = {
                "package_id": package_id,
                "package_signature": str(row["package_signature"]),
                "envelope_id": envelope_id,
                "action": action,
                "item_refs": scoped_refs,
            }
            existing = conn.execute(
                "SELECT * FROM review_decisions WHERE package_id=? AND envelope_id=? AND action=? AND status='planned' ORDER BY created_at DESC",
                (package_id, envelope_id, action),
            ).fetchall()
            for prior in existing:
                prior_refs = json.loads(str(prior["item_refs_json"]))
                if prior_refs != selected:
                    continue
                snapshot = _authorization().challenge_snapshot(
                    str(prior["authorization_challenge_ref"]), state_root=authorization_state_root
                )
                if snapshot.get("ok") and str(snapshot.get("status")) in {"pending", "granted"}:
                    return {
                        "schema": "workflow_review_queue.decision_plan.v2",
                        "ok": True,
                        "decision_id": str(prior["decision_id"]),
                        "package_id": package_id,
                        "action": action,
                        "target_status": target_status,
                        "item_refs": selected,
                        "confirm_token": str(prior["confirm_token"]),
                        "authorization_challenge": snapshot,
                        "reused": True,
                    }
                conn.execute("UPDATE review_decisions SET status='superseded' WHERE decision_id=?", (prior["decision_id"],))
            if len(selected) < len(refs):
                for prior in existing:
                    prior_refs = json.loads(str(prior["item_refs_json"]))
                    if prior_refs == refs:
                        fenced = _authorization().cancel_challenge(
                            str(prior["authorization_challenge_ref"]),
                            reason="partial_approval_replaced_full_package_scope",
                            state_root=authorization_state_root,
                        )
                        if not fenced.get("ok"):
                            return {"ok": False, "reason": "full_package_approval_fence_failed", "fence": fenced}
                        conn.execute("UPDATE review_decisions SET status='superseded' WHERE decision_id=?", (prior["decision_id"],))
        else:
            target = {"package_id": package_id, "action": action, "item_refs": selected}
        decision_id = f"review-decision:{uuid.uuid4().hex[:20]}"
        token_payload = {"decision_id": decision_id, "package_id": package_id, "action": action, "item_refs": selected}
        token = hashlib.sha256(json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        scope = _authorization().build_scope(
            thread_id=authorization_thread_id,
            action=f"workflow_review.{action}",
            target=target,
            risk="high" if action == "approve" else "medium",
            phase="decision_apply",
            source_signature=hashlib.sha256(
                json.dumps(target, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            requested_by_owner="workflow_review_delivery",
        )
        challenge = _authorization().create_challenge(
            scope,
            target_summary=f"review package {package_id}: {action} {len(selected)} exact revision(s)",
            state_root=authorization_state_root,
        )
        if not challenge.get("ok"):
            return {"ok": False, "reason": "authorization_challenge_failed", "authorization": challenge}
        conn.execute(
            "INSERT INTO review_decisions(decision_id,confirm_token,package_id,envelope_id,action,item_refs_json,status,created_at,authorization_thread_id,authorization_challenge_ref,authorization_scope_json) VALUES(?,?,?,?,?,?, 'planned', ?,?,?,?)",
            (decision_id, token, package_id, envelope_id, action, json.dumps(selected, sort_keys=True), queue.now_iso(), authorization_thread_id, challenge["challenge_ref"], json.dumps(scope, sort_keys=True)),
        )
        conn.commit()
        return {"schema": "workflow_review_queue.decision_plan.v2", "ok": True, "decision_id": decision_id, "package_id": package_id, "action": action, "target_status": target_status, "item_refs": selected, "confirm_token": token, "authorization_challenge": challenge}
    finally:
        conn.close()


def decision_apply(
    confirm_token: str,
    *,
    authorization_grant_ref: str = "",
    user_evidence_ref: str = "",
    note: str = "",
    db_path: Path | None = None,
    authorization_state_root: Path | str | None = None,
    iteration_memory_index_path: Path | None = None,
    iteration_backup: bool = True,
) -> dict[str, Any]:
    if not str(authorization_grant_ref or "").strip():
        return {"ok": False, "reason": "scoped_authorization_grant_required", "legacy_user_evidence_ref_accepted": False}
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
            recovered = json.loads(str(row["result_json"]))
            effect_ref = str(recovered.get("authorization_effect_ref") or f"workflow-review-decision:{row['decision_id']}:{row['applied_at']}")
            observed = _authorization().record_effect(
                str(row["decision_id"]), executor="workflow_review_delivery",
                status="effect_observed", effect_receipt_ref=effect_ref,
                details={"database": str(_db_path(db_path)), "decision_status": "applied", "recovered": True},
                state_root=authorization_state_root,
            )
            completed = (
                _authorization().record_effect(
                    str(row["decision_id"]), executor="workflow_review_delivery",
                    status="completed", effect_receipt_ref=effect_ref,
                    state_root=authorization_state_root,
                )
                if observed.get("ok") else observed
            )
            recovered["authorization_effect_ref"] = effect_ref
            recovered["authorization_effect_status"] = completed.get("status", completed.get("reason", ""))
            recovered["authorization_effect_recovered"] = True
            recovered = _complete_iteration_lifecycle(
                recovered,
                decision_id=str(row["decision_id"]),
                db_path=db_path,
                memory_index_path=iteration_memory_index_path,
                backup=iteration_backup,
            )
            conn.execute(
                "UPDATE review_decisions SET result_json=? WHERE decision_id=?",
                (json.dumps(recovered, ensure_ascii=False, sort_keys=True), row["decision_id"]),
            )
            conn.commit()
            return recovered
        if str(row["status"]) != "planned":
            conn.rollback()
            return {"ok": False, "reason": "decision_not_active", "status": str(row["status"])}
        refs = json.loads(str(row["item_refs_json"]))
        action = str(row["action"])
        target_status = {"approve": "approved", "reject": "rejected", "revise": "revised", "defer": "deferred"}[action]
        current_rows: list[tuple[dict[str, Any], Any]] = []
        for ref in refs:
            current = conn.execute("SELECT status,revision,kind FROM review_items WHERE review_id=?", (ref["review_id"],)).fetchone()
            if current is None or str(current["status"]) != "pending" or int(current["revision"]) != int(ref["revision"]):
                conn.rollback()
                return {"ok": False, "reason": "decision_atomic_revision_conflict", "review_id": ref["review_id"]}
            current_rows.append((ref, current))
        decision_id = str(row["decision_id"])
        authorization = _authorization().consume_grant(
            authorization_grant_ref,
            _decision_scope(row),
            consumer_owner="workflow_review_delivery",
            operation_id=decision_id,
            state_root=authorization_state_root,
        )
        if not authorization.get("ok"):
            conn.rollback()
            return {"ok": False, "reason": "scoped_authorization_rejected", "authorization": authorization}
        effect_started = _authorization().record_effect(
            decision_id,
            executor="workflow_review_delivery",
            status="effect_started",
            state_root=authorization_state_root,
        )
        if not effect_started.get("ok"):
            conn.rollback()
            return {"ok": False, "reason": "authorization_effect_journal_failed", "authorization_effect": effect_started}
        trusted_user_evidence_ref = str(authorization.get("user_message_ref") or "")
        results: list[dict[str, Any]] = []
        for ref, current in current_rows:
            now = queue.now_iso()
            disposed_at = now if target_status in queue.TERMINAL_STATUSES else ""
            conn.execute("UPDATE review_items SET status=?,updated_at=?,disposed_at=?,disposition_note=? WHERE review_id=?", (target_status, now, disposed_at, str(note or trusted_user_evidence_ref)[:2000], ref["review_id"]))
            results.append({
                "review_id": ref["review_id"],
                "revision": ref["revision"],
                "kind": str(current["kind"]),
                "from_status": "pending",
                "status": target_status,
            })
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
            "authorization_consumption_ref": authorization.get("consumption_ref", ""),
        }
        applied_at = queue.now_iso()
        conn.execute("UPDATE review_decisions SET status='applied',user_evidence_ref=?,authorization_grant_ref=?,authorization_consumption_ref=?,result_json=?,applied_at=? WHERE decision_id=?", (trusted_user_evidence_ref, authorization_grant_ref, authorization.get("consumption_ref", ""), json.dumps(result, ensure_ascii=False, sort_keys=True), applied_at, row["decision_id"]))
        if str(row["envelope_id"]):
            conn.execute(
                "UPDATE review_deliveries SET status='acknowledged',acknowledged_at=? "
                "WHERE envelope_id=? AND status='delivered' AND response_digest<>''",
                (applied_at, row["envelope_id"]),
            )
        conn.commit()
        effect_ref = f"workflow-review-decision:{decision_id}:{applied_at}"
        observed = _authorization().record_effect(
            decision_id,
            executor="workflow_review_delivery",
            status="effect_observed",
            effect_receipt_ref=effect_ref,
            details={"database": str(_db_path(db_path)), "decision_status": "applied"},
            state_root=authorization_state_root,
        )
        completed = (
            _authorization().record_effect(
                decision_id,
                executor="workflow_review_delivery",
                status="completed",
                effect_receipt_ref=effect_ref,
                state_root=authorization_state_root,
            )
            if observed.get("ok")
            else observed
        )
        result["authorization_effect_ref"] = effect_ref
        result["authorization_effect_status"] = completed.get("status", completed.get("reason", ""))
        result = _complete_iteration_lifecycle(
            result,
            decision_id=decision_id,
            db_path=db_path,
            memory_index_path=iteration_memory_index_path,
            backup=iteration_backup,
        )
        conn.execute(
            "UPDATE review_decisions SET result_json=? WHERE decision_id=?",
            (json.dumps(result, ensure_ascii=False, sort_keys=True), row["decision_id"]),
        )
        conn.commit()
        return result
    finally:
        conn.close()


def consume_preissued_authorization(
    *,
    thread_id: str,
    rollout_path: Path,
    db_path: Path | None = None,
    authorization_state_root: Path | str | None = None,
    iteration_memory_index_path: Path | None = None,
    iteration_backup: bool = True,
) -> dict[str, Any]:
    """Consume the one preissued package approval token present in this task rollout."""

    queue = _queue()
    resolved_db = _db_path(db_path)
    conn = queue.connect(resolved_db)
    try:
        rows = conn.execute(
            "SELECT d.decision_id,d.confirm_token,d.authorization_challenge_ref,d.envelope_id "
            "FROM review_decisions d JOIN review_deliveries e ON e.envelope_id=d.envelope_id "
            "WHERE d.authorization_thread_id=? AND d.action='approve' AND d.status='planned' "
            "AND e.target_ref=? ORDER BY d.created_at",
            (thread_id, f"thread:{thread_id}"),
        ).fetchall()
    finally:
        conn.close()
    attempts: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        delivery = verify_delivery(
            str(row["envelope_id"]),
            rollout_path=Path(rollout_path),
            expected_thread_id=thread_id,
            db_path=resolved_db,
        )
        if not delivery.get("ok"):
            attempts.append({"decision_id": str(row["decision_id"]), "stage": "delivery", "result": delivery})
            continue
        grant = _authorization().grant_from_thread(
            str(row["authorization_challenge_ref"]),
            rollout_path=Path(rollout_path),
            state_root=authorization_state_root,
        )
        if not grant.get("ok"):
            attempts.append({"decision_id": str(row["decision_id"]), "stage": "authorization", "result": grant})
            continue
        result = decision_apply(
            str(row["confirm_token"]),
            authorization_grant_ref=str(grant["grant_ref"]),
            db_path=resolved_db,
            authorization_state_root=authorization_state_root,
            iteration_memory_index_path=iteration_memory_index_path,
            iteration_backup=iteration_backup,
        )
        attempts.append({"decision_id": str(row["decision_id"]), "stage": "decision", "result": result})
        if result.get("ok"):
            applied.append(result)
    if len(applied) > 1:
        return {"ok": False, "reason": "multiple_preissued_approvals_consumed", "attempts": attempts}
    return {
        "schema": "workflow_review_queue.preissued_approval_consume.v1",
        "ok": len(applied) == 1,
        "reason": "" if applied else "matching_preissued_approval_not_found",
        "thread_id": thread_id,
        "decision_count": len(rows),
        "applied_count": len(applied),
        "result": applied[0] if applied else {},
        "attempts": attempts,
    }


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
