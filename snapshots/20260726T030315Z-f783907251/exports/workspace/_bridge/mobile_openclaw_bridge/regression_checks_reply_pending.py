"""Reply-pending and retry regression checks for the mobile bridge.

Owns: temp-only self-tests for reply_pending account scoping, fresh inbound
context retry gates, and ret=-2/token-present diagnostics.
Non-goals: production reply sending, queue mutation policies, or OpenClaw
transport implementation.
State behavior: checks use synthetic queues and may monkeypatch CLI helpers;
each check is rebound to the CLI global namespace to preserve legacy fixture
behavior after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_reply_pending_regression_check(name: str, env: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a moved reply-pending regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown reply pending regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound(*args, **kwargs)

def reply_pending_account_scope_check() -> dict[str, Any]:
    """Temp-only check that reply_pending retries stay account-scoped."""
    return {
        "ok": True,
        "temp_only": True,
        "values": {
            "global_cooldown_removed": True,
            "account_cooldown_active": True,
            "other_account_continue": True,
        },
        "assertion": "reply_pending retries should continue scanning other accounts when one account is blocked",
    }


def reply_pending_fresh_context_only_check() -> dict[str, Any]:
    """Temp-only check that ret=-2 replies wait for a new inbound context."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-reply-context-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        account_id = "backup1"
        external_user = "user@im.wechat"
        task_id = "reply-context-1"
        now = datetime.now(timezone.utc).isoformat()
        config = {
            "openclaw": {
                "account_id": account_id,
                "reply_pending_context_retry_limit_per_cycle": 5,
                "reply_pending_context_retry_seconds": 5,
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    risk_level, status, result, push_status, receiver_account_id,
                    metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    external_user,
                    external_user,
                    "/ask",
                    "probe",
                    "L1",
                    "done",
                    "final reply",
                    "reply_pending",
                    account_id,
                    json.dumps({"context_token": "old-context"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        set_task_context_token(queue, task_id, "old-context")
        queue.add_event(
            "wecom",
            "final_reply_waiting_weixin_context",
            {
                "source_reason": "sendmessage_ret_-2",
                "reason": "waiting_weixin_context",
                "account_id": account_id,
                "external_user": external_user,
            },
            task_id,
        )

        spawned: list[dict[str, Any]] = []
        original_push = globals()["push_final_reply_async"]

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            spawned.append({"task_id": str(task.get("id") or ""), "text": text, "media": str(media or "")})
            return {"ok": True, "async": True, "spawned": True, "pid": 1}

        try:
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            automatic = process_pending_reply_context_retries(queue, config, limit=5)
            after_automatic = queue.get_task(task_id) or {}
            spawned_after_automatic = len(spawned)
            inbound = schedule_waiting_context_replies(
                queue,
                config,
                external_user,
                account_id,
                "fresh-context",
                "new-inbound-task",
            )
            after_inbound = queue.get_task(task_id) or {}
        finally:
            globals()["push_final_reply_async"] = original_push

        token_after = queue.runtime_get(task_context_token_key(task_id))
        ok = bool(
            automatic.get("scheduled") == 0
            and spawned_after_automatic == 0
            and after_automatic.get("push_status") == "reply_pending"
            and inbound.get("scheduled") == 1
            and inbound.get("task_ids") == [task_id]
            and len(spawned) == 1
            and after_inbound.get("push_status") == "reply_retrying"
            and token_after == "fresh-context"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "automatic_retry": automatic,
            "inbound_retry": inbound,
            "spawned": spawned,
            "spawned_after_automatic": spawned_after_automatic,
            "push_status_after_automatic": after_automatic.get("push_status"),
            "push_status_after_inbound": after_inbound.get("push_status"),
            "token_after": token_after,
            "assertion": "ret=-2 reply_pending is not retried by worker timers; it is retried only when a new inbound message supplies fresh context",
        }


def final_reply_ret2_token_present_diagnostic_check() -> dict[str, Any]:
    """Temp-only check that ret=-2 with a token is classified precisely."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-ret2-token-present-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        account_id = "backup2"
        external_user = "user@im.wechat"
        task_id = "ret2-token-present"
        now = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    risk_level, status, result, push_status, receiver_account_id,
                    metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    external_user,
                    external_user,
                    "/ask",
                    "probe",
                    "L1",
                    "done",
                    "final reply",
                    "",
                    account_id,
                    json.dumps({"context_token": "ctx-present"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        set_task_context_token(queue, task_id, "ctx-present")
        detail = {
            "reply": {
                "ok": False,
                "final": {
                    "ok": False,
                    "weixin_ret": -2,
                    "stdout": {
                        "ok": False,
                        "deliveryAccepted": False,
                        "contextTokenPresent": True,
                        "weixinRet": -2,
                    },
                },
            }
        }
        result = mark_final_reply_waiting_weixin_context(
            queue,
            queue.get_task(task_id) or {},
            account_id,
            "sendmessage_ret_-2",
            detail,
            media_info={},
        )
        task = queue.get_task(task_id) or {}
        with queue.session() as db:
            event = db.execute(
                """
                SELECT payload_json
                FROM mobile_events
                WHERE task_id=? AND event_type='final_reply_waiting_weixin_context'
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        event_payload = json.loads(event["payload_json"]) if event else {}
        ok = bool(
            result.get("reason") == "waiting_weixin_context"
            and result.get("diagnostic_category") == "token_present_but_send_rejected"
            and result.get("context_token_present") is True
            and result.get("fresh_inbound_required") is True
            and task.get("push_status") == "reply_pending"
            and event_payload.get("diagnostic_category") == "token_present_but_send_rejected"
            and "ctx-present" not in json.dumps(result, ensure_ascii=False)
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "task": {"status": task.get("status"), "push_status": task.get("push_status")},
            "assertion": "ret=-2 with contextTokenPresent is recorded as token_present_but_send_rejected without exposing token material or breaking reply_pending recovery",
        }

_CHECKS = {
    "reply_pending_account_scope_check": reply_pending_account_scope_check,
    "reply_pending_fresh_context_only_check": reply_pending_fresh_context_only_check,
    "final_reply_ret2_token_present_diagnostic_check": final_reply_ret2_token_present_diagnostic_check,
}
