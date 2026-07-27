#!/usr/bin/env python3
"""Dashboard-to-Weixin direct delivery boundary.

Ownership: creates the dashboard outbound anchor task, invokes the OpenClaw
final-reply sender, classifies the send result, and records delivery events.
Non-goals: dashboard HTTP parsing, permission decisions, account discovery, and
operator identity selection stay in mobile_dashboard.py.
State behavior: writes only through the provided MobileQueue instance and keeps
the existing direct-send task/event semantics.
Caller context: mobile_dashboard.dashboard_send_to_weixin after request
validation and permission approval.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_dashboard_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in attachments:
        path_text = str(item.get("local_path") or item.get("path") or "")
        path = Path(path_text) if path_text else None
        size = item.get("size")
        if size in (None, "") and path and path.exists():
            try:
                size = path.stat().st_size
            except OSError:
                size = None
        summary.append(
            {
                "name": str(item.get("name") or (path.name if path else "") or "附件"),
                "mime": str(item.get("mime") or item.get("content_type") or ""),
                "size": size,
                "local_path": path_text,
            }
        )
    return summary


def summarize_direct_weixin_error(result: dict[str, Any]) -> str:
    details: list[str] = []
    reply = result.get("reply") if isinstance(result, dict) else None
    items = reply.get("items") if isinstance(reply, dict) else None
    candidates = items if isinstance(items, list) else [reply] if isinstance(reply, dict) else []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        final = item.get("final")
        if not isinstance(final, dict):
            final = item
        stdout = final.get("stdout") if isinstance(final.get("stdout"), dict) else {}
        response = stdout.get("response") if isinstance(stdout.get("response"), dict) else {}
        upload = response.get("upload") if isinstance(response.get("upload"), dict) else {}
        media = str(final.get("media") or stdout.get("media") or "")
        name = str(item.get("name") or (Path(media).name if media else "") or "附件")
        transport = str(stdout.get("transport") or final.get("transport") or response.get("transport") or "")
        ret = final.get("weixin_ret", stdout.get("weixinRet", response.get("weixinRet")))
        http = final.get("httpStatus", stdout.get("httpStatus", response.get("httpStatus")))
        size = upload.get("fileSize")
        error = str(stdout.get("error") or final.get("reason") or "")
        piece = name
        if size:
            piece += f" size={size}"
        if transport:
            piece += f" transport={transport}"
        if http is not None:
            piece += f" http={http}"
        if ret is not None:
            piece += f" ret={ret}"
        if error:
            piece += f" error={error}"
        details.append(piece)
    if details:
        return "微信通道未确认发送成功：" + "；".join(details[:3])
    return "微信通道未确认发送成功"


def _push_text_or_attachments(
    queue: Any,
    reply_task: dict[str, Any],
    text: str,
    config: dict[str, Any],
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    from mobile_openclaw_cli import push_final_reply  # type: ignore

    if not attachments:
        reply = push_final_reply(queue, reply_task, text, config)
        return {"ok": bool(reply.get("ok")), "reply": reply}

    sends: list[dict[str, Any]] = []
    all_ok = True
    recoverable = False
    for index, attachment in enumerate(attachments):
        media = str(attachment.get("local_path") or "")
        name = str(attachment.get("name") or Path(media).name or "附件")
        if not media:
            item = {"ok": False, "reason": "attachment local_path is missing", "name": name}
        else:
            caption = text if index == 0 and text else f"附件：{name}"
            item = push_final_reply(queue, reply_task, caption, config, media=media)
            item["name"] = name
            item["media"] = media
        sends.append(item)
        all_ok = all_ok and bool(item.get("ok"))
        recoverable = recoverable or bool(item.get("recoverable"))
    return {"ok": all_ok, "recoverable": recoverable, "reply": {"ok": all_ok, "recoverable": recoverable, "items": sends}}


def _mark_delivery_result(queue: Any, result: dict[str, Any]) -> None:
    task_id = str(result.get("task_id") or "")
    if not task_id:
        return
    payload = json.dumps(result, ensure_ascii=False)
    if result.get("ok"):
        queue.mark_pushed(task_id, True, payload)
    elif result.get("recoverable"):
        queue.mark_reply_pending(task_id, payload)
    else:
        queue.mark_pushed(task_id, False, payload)


def send_dashboard_weixin_direct(
    *,
    queue: Any,
    config: dict[str, Any],
    text: str,
    external_user: str,
    receiver_account_id: str,
    attachments: list[dict[str, Any]],
    record_to_chat: bool,
    direct_id: str,
) -> dict[str, Any]:
    """Send a dashboard-originated message through the Weixin final-reply path."""
    attachment_summary = summarize_dashboard_attachments(attachments)
    try:
        metadata = {
            "msg_id": direct_id,
            "transport": "dashboard-weixin-direct",
            "receiver_account_id": receiver_account_id,
            "dashboard_proxy_user": external_user,
            "outbound_only": True,
            "attachment_count": len(attachments),
            "record_to_chat": bool(record_to_chat),
            "chat_record_text": text or ("附件" if attachments else ""),
        }
        anchor = queue.enqueue(
            text or "[dashboard-outbound] Send attachment(s) to Weixin.",
            source="dashboard-weixin",
            external_user=external_user,
            external_conversation=external_user,
            metadata=metadata,
            attachments=attachments,
        )
        anchor_id = str(anchor.get("id") or "")
        if anchor_id:
            now = utc_now()
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='done', completed_at=?, updated_at=?
                    WHERE id=? AND status='pending'
                    """,
                    (now, now, anchor_id),
                )
            queue.add_event(
                "dashboard",
                "dashboard_weixin_direct_anchor_created",
                {"direct_id": direct_id, "receiver_account_id": receiver_account_id},
                anchor_id,
            )
        reply_task = queue.get_task(anchor_id) if anchor_id else None
        if not reply_task:
            raise RuntimeError("failed to create dashboard outbound anchor task")
        result = _push_text_or_attachments(queue, reply_task, text, config, attachments)
        result.update({"id": direct_id, "task_id": anchor_id})
    except Exception as exc:
        result = {"ok": False, "id": direct_id, "error": str(exc)}

    if result.get("ok"):
        result["status_message"] = f"微信通道已接受：{direct_id}"
    elif result.get("recoverable"):
        result["status_message"] = f"微信通道暂未接受，已等待该用户上下文恢复：{result.get('task_id') or direct_id}"
    elif "error" not in result:
        result["error"] = summarize_direct_weixin_error(result)
    _mark_delivery_result(queue, result)

    if record_to_chat and result.get("status_message"):
        result["status_message"] = f"{result['status_message']}，并已记录到对话流"
    queue.add_event(
        "dashboard",
        "dashboard_weixin_direct_sent"
        if result.get("ok")
        else ("dashboard_weixin_direct_waiting_context" if result.get("recoverable") else "dashboard_weixin_direct_failed"),
        {
            "external_user": external_user,
            "receiver_account_id": receiver_account_id,
            "text_length": len(text),
            "attachment_count": len(attachments),
            "attachments": attachment_summary,
            "direct_id": direct_id,
            "result": result,
            "record_to_chat": bool(record_to_chat),
            "chat_record_text": text or ("附件" if attachments else ""),
        },
        None,
    )
    if record_to_chat:
        queue.add_event(
            "dashboard",
            "dashboard_weixin_direct_chat_recorded",
            {
                "external_user": external_user,
                "receiver_account_id": receiver_account_id,
                "direct_id": direct_id,
                "status": "ok" if result.get("ok") else ("recoverable" if result.get("recoverable") else "failed"),
                "text": text,
                "attachments": attachment_summary,
                "result": result,
            },
            None,
        )
    return result
