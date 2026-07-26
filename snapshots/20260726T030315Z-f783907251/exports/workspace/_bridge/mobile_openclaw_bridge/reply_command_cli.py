"""Reply-related CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for reply, status-ack, and
final-reply commands.
Non-goals: Weixin transport implementation, final-reply classification,
ownership policy, queue schema, or retry semantics.
State behavior: mirrors existing command behavior; reply sends only with
--send, status-ack writes an acknowledgement event through its callback, and
final-reply preserves duplicate suppression, owner guard, reply_sending lease,
and push-status recording.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

import json
from argparse import SUPPRESS
from pathlib import Path
from typing import Any, Callable


def register_reply_command_parsers(subparsers: Any) -> None:
    reply = subparsers.add_parser("reply", help="Reply to a mobile task through OpenClaw Weixin")
    reply.add_argument("task_id")
    reply.add_argument("--text", required=True)
    reply.add_argument("--media", default="")
    reply.add_argument("--send", action="store_true", help="Actually send to Weixin; omit for dry-run")

    status_ack = subparsers.add_parser("status-ack", help=SUPPRESS)
    status_ack.add_argument("task_id")
    status_ack.add_argument("--text", required=True)
    status_ack.add_argument("--event-type", required=True)

    final_reply = subparsers.add_parser("final-reply", help=SUPPRESS)
    final_reply.add_argument("task_id")
    final_reply.add_argument("--text", default="")
    final_reply.add_argument("--text-file", default="")
    final_reply.add_argument("--media", default="")


def run_reply_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    reply_to_weixin: Callable[..., dict[str, Any]],
    send_status_ack_sync: Callable[..., dict[str, Any]],
    task_event_exists: Callable[..., bool],
    guard_final_reply_owner_ready: Callable[..., dict[str, Any] | None],
    push_final_reply: Callable[..., dict[str, Any]],
    clear_task_reply_sending: Callable[..., None],
    utc_now: Callable[[], str],
) -> tuple[dict[str, Any], int]:
    task = queue.get_task(args.task_id)
    if not task:
        return {"ok": False, "reason": "task not found", "task_id": args.task_id}, 1

    if args.cmd == "reply":
        result = reply_to_weixin(task, args.text, config, args.send, media=args.media or None)
        if args.send:
            queue.mark_pushed(args.task_id, bool(result.get("ok")), json.dumps(result, ensure_ascii=False))
        return result, 0

    if args.cmd == "status-ack":
        result = send_status_ack_sync(queue, task, args.text, config, args.event_type)
        return result, 0 if result.get("ok") else 1

    if task_event_exists(queue, args.task_id, "final_reply_weixin_accepted") or task_event_exists(queue, args.task_id, "push_result"):
        result = {
            "ok": True,
            "suppressed": True,
            "duplicate": True,
            "reason": "final_reply_already_sent_or_accepted",
        }
        queue.add_event("wecom", "final_reply_duplicate_suppressed", result, args.task_id)
        return result, 0

    guard_result = guard_final_reply_owner_ready(queue, task)
    if guard_result:
        return guard_result, 1

    text = str(args.text or "")
    text_file = Path(args.text_file) if args.text_file else None
    if text_file:
        try:
            text = text_file.read_text(encoding="utf-8")
        except Exception as exc:
            result = {"ok": False, "reason": f"failed to read final reply payload: {exc}"}
            queue.mark_pushed(args.task_id, False, json.dumps(result, ensure_ascii=False))
            return result, 1

    with queue.session() as db:
        db.execute(
            """
            UPDATE mobile_tasks
            SET push_status='reply_sending', updated_at=?
            WHERE id=? AND push_status IN ('reply_pending','reply_retrying','push_failed')
            """,
            (utc_now(), args.task_id),
        )
    result = push_final_reply(queue, task, text, config, media=args.media or None)
    if result.get("push_status_recorded"):
        pass
    elif result.get("reason") == "waiting_weixin_context":
        queue.mark_reply_pending(args.task_id, json.dumps(result, ensure_ascii=False))
    else:
        queue.mark_pushed(args.task_id, bool(result.get("ok")), json.dumps(result, ensure_ascii=False))
    clear_task_reply_sending(queue, args.task_id)
    if text_file:
        try:
            text_file.unlink()
        except Exception:
            pass
    return result, 0 if result.get("ok") else 1
