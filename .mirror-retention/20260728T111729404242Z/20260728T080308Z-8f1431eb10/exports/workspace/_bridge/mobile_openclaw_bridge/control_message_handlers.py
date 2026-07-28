"""Control-command reply helpers for the mobile bridge.

Owns: durable control reply task envelopes, receipt ids, and auditable control
reply sending.
Non-goals: permission decisions, repair execution, bridge pause/resume state
changes, thread selection state, or normal mobile task dispatch.
State behavior: writes only control reply outbox/sent/failed audit events
through the provided queue and sends the requested control reply through the
provided reply function.
Normal callers: mobile_openclaw_cli control-command facade and control receipt
regression checks.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


def control_reply_task(
    external_user: str,
    external_conversation: str = "",
    account_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    task = {
        "id": "control",
        "receiver_account_id": account_id,
        "external_user": external_user,
        "external_conversation": external_conversation,
    }
    if run_id:
        task["metadata_json"] = json.dumps({"run_id": run_id}, ensure_ascii=False)
    return task


def control_receipt_id(command: str, text: str, external_user: str, account_id: str, external_conversation: str = "") -> str:
    normalized_text = " ".join(str(text or "").strip().split())
    payload = {
        "command": str(command or "").strip().lower(),
        "text": normalized_text,
        "external_user": str(external_user or "").strip(),
        "account_id": str(account_id or "").strip(),
        "external_conversation": str(external_conversation or "").strip(),
    }
    return "ctrl-" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def send_control_reply(
    queue: Any,
    reply_task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    command: str,
    *,
    reply_func: Callable[[dict[str, Any], str, dict[str, Any]], dict[str, Any]],
    delivery_accepted_func: Callable[[dict[str, Any]], bool],
    phone_visible_func: Callable[[dict[str, Any]], bool],
    receipt_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a control-command reply while preserving a durable receipt trail."""
    account_id = str(reply_task.get("receiver_account_id") or "")
    external_user = str(reply_task.get("external_user") or "")
    external_conversation = str(reply_task.get("external_conversation") or "")
    receipt_id = receipt_id or control_receipt_id(command, text, external_user, account_id, external_conversation)
    base_payload = {
        "receipt_id": receipt_id,
        "command": str(command or ""),
        "trigger_user": external_user,
        "trigger_account": account_id,
        "external_conversation": external_conversation,
        "text_chars": len(str(text or "")),
        "extra": extra or {},
        "policy": "control command replies must be durable and auditable even when they do not create a normal mobile task",
    }
    queue.add_event("openclaw-weixin", "control_reply_outbox_created", base_payload)
    reply = reply_func(reply_task, text, config)
    event_payload = {
        **base_payload,
        "ok": bool(reply.get("ok")),
        "delivery_accepted": delivery_accepted_func(reply),
        "phone_visible_confirmed": phone_visible_func(reply),
        "reply": reply,
    }
    queue.add_event(
        "wecom",
        "control_reply_sent" if reply.get("ok") else "control_reply_failed",
        event_payload,
    )
    result = dict(reply)
    result["control_receipt_id"] = receipt_id
    return result
