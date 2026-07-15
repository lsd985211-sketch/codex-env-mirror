"""Supplement, delivery-group, and follow-up regression checks.

Owns: self-tests for supplement batching, MCP supplement handoff, FIFO follow-up
ordering, delivery-group ownership, and readback rehydration behavior.
Non-goals: production worker dispatch, permission policy definitions, or final
reply delivery implementations.
State behavior: checks create synthetic queues/events and may monkeypatch CLI
helpers; each run is rebound to the CLI global namespace to preserve legacy test
semantics after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_supplement_regression_check(name: str, env: dict[str, Any]) -> dict[str, Any]:
    """Run a moved supplement regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown supplement regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound()

def queued_turn_materialized_readback_rehydrate_check() -> dict[str, Any]:
    """Temp-only check for unreadable app-server turn materialization rehydrate."""

    def insert_queued_task(queue: MobileQueue, task_id: str, thread_id: str, old: str) -> None:
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "queued after unreadable app-server dispatch",
                    hashlib.sha256(b"queued after unreadable app-server dispatch").hexdigest(),
                    task_id,
                    "L1",
                    "queued_for_codex",
                    "",
                    "",
                    "backup1",
                    thread_id,
                    "{}",
                    old,
                    old,
                    old,
                    None,
                ),
            )

    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-materialized-rehydrate-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=300)).isoformat()
        task_id = "queued-materialized-turn"
        bad_task_id = "queued-materialized-no-marker"
        ghost_task_id = "queued-materialized-ghost"
        thread_id = "thread-materialized-rehydrate"
        turn_id = "turn-materialized-rehydrate"
        bad_turn_id = "turn-materialized-no-marker"
        ghost_turn_id = "turn-materialized-ghost"
        client_message_id = "mobile-openclaw-materialized-rehydrate-batch"
        bad_client_message_id = "mobile-openclaw-materialized-no-marker-batch"
        ghost_client_message_id = "mobile-openclaw-materialized-ghost-batch"
        insert_queued_task(queue, task_id, thread_id, old)
        insert_queued_task(queue, bad_task_id, thread_id, old)
        insert_queued_task(queue, ghost_task_id, thread_id, old)

        protocols = mobile_protocols([{"id": task_id}], client_message_id)
        prompt_with_markers = " ".join(
            [
                protocols[task_id]["ack_marker"],
                protocols[task_id]["result_begin_marker"],
                f"[[mobile_task_id:{task_id}]]",
                "请处理这个任务",
                protocols[task_id]["result_end_marker"],
            ]
        )
        queue.add_event(
            "local",
            "delivery_failed_reverted_to_pending",
            {
                "thread_id": thread_id,
                "delivery": {
                    "ok": False,
                    "mode": "codex-app-server",
                    "reason": "app_server_turn_not_readable_after_dispatch",
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "client_user_message_id": client_message_id,
                    "expected_task_ids": [task_id],
                    "mobile_protocols": protocols,
                    "prompt": prompt_with_markers,
                },
            },
            task_id,
        )
        bad_protocols = mobile_protocols([{"id": bad_task_id}], bad_client_message_id)
        queue.add_event(
            "local",
            "delivery_failed_reverted_to_pending",
            {
                "thread_id": thread_id,
                "delivery": {
                    "ok": False,
                    "mode": "codex-app-server",
                    "reason": "app_server_turn_not_readable_after_dispatch",
                    "thread_id": thread_id,
                    "turn_id": bad_turn_id,
                    "client_user_message_id": bad_client_message_id,
                    "expected_task_ids": [bad_task_id],
                    "mobile_protocols": bad_protocols,
                    "prompt": f"task id only {bad_task_id}",
                },
            },
            bad_task_id,
        )
        ghost_protocols = mobile_protocols([{"id": ghost_task_id}], ghost_client_message_id)
        ghost_prompt_with_markers = " ".join(
            [
                ghost_protocols[ghost_task_id]["ack_marker"],
                ghost_protocols[ghost_task_id]["result_begin_marker"],
                f"[[mobile_task_id:{ghost_task_id}]]",
                "请处理这个任务",
                ghost_protocols[ghost_task_id]["result_end_marker"],
            ]
        )
        queue.add_event(
            "local",
            "delivery_failed_reverted_to_pending",
            {
                "thread_id": thread_id,
                "delivery": {
                    "ok": False,
                    "mode": "codex-app-server",
                    "reason": "app_server_turn_not_readable_after_dispatch",
                    "thread_id": thread_id,
                    "turn_id": ghost_turn_id,
                    "client_user_message_id": ghost_client_message_id,
                    "expected_task_ids": [ghost_task_id],
                    "mobile_protocols": ghost_protocols,
                    "prompt": ghost_prompt_with_markers,
                },
            },
            ghost_task_id,
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["user@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "queued_recovery_after_seconds": 30,
                "active_recovery_max_sent_checks_per_cycle": 10,
            },
        }
        original_poll_app_server = globals()["poll_codex_result_app_server"]

        def fake_poll_app_server(
            _config: dict[str, Any],
            _thread_id: str,
            polled_turn_id: str,
            _client_message_id: str = "",
            _expected_task_ids: list[str] | None = None,
            _expected_result_codes: dict[str, str] | None = None,
            _expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            if polled_turn_id == turn_id:
                return {
                    "ok": True,
                    "healthy": True,
                    "mode": "codex-app-server",
                    "newText": None,
                    "status": "running",
                    "turn_id": turn_id,
                    "matched_turn_id": turn_id,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": True,
                    "result_complete": False,
                    "ownership": {
                        "required": True,
                        "protocol": "mobile_result_boundary_v2",
                        "valid": False,
                        "expected_task_ids": [task_id],
                        "ack_seen": True,
                        "begin_seen": False,
                        "end_seen": False,
                        "result_complete": False,
                    },
                }
            return {
                "ok": True,
                "healthy": True,
                "mode": "codex-app-server",
                "newText": None,
                "status": "notFound",
                "turn_id": polled_turn_id,
                "protocol": "mobile_result_boundary_v2",
                "ack_seen": False,
                "result_complete": False,
                "ownership": {
                    "required": True,
                    "protocol": "mobile_result_boundary_v2",
                    "valid": False,
                    "expected_task_ids": _expected_task_ids or [],
                    "result_complete": False,
                },
            }

        try:
            globals()["poll_codex_result_app_server"] = fake_poll_app_server
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["poll_codex_result_app_server"] = original_poll_app_server
        task_after = queue.get_task(task_id) or {}
        bad_after = queue.get_task(bad_task_id) or {}
        ghost_after = queue.get_task(ghost_task_id) or {}
        batch_id, expected_task_ids = task_batch_runtime(queue, task_id, [task_id])
        ack_codes = task_ack_code_runtime(queue, expected_task_ids)
        result_codes = task_result_code_runtime(queue, expected_task_ids)
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    GROUP BY event_type
                    """
                ).fetchall()
            }
            task_events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                ).fetchall()
            }
        ok = bool(
            recovery.get("queued_rehydrated") == 1
            and recovery.get("reverted") == 2
            and task_after.get("status") == "sent_to_codex"
            and bad_after.get("status") == "pending"
            and ghost_after.get("status") == "pending"
            and queue.runtime_get(task_turn_key(task_id)) == turn_id
            and batch_id == client_message_id
            and expected_task_ids == [task_id]
            and ack_codes.get(task_id) == protocols[task_id]["ack_code"]
            and result_codes.get(task_id) == protocols[task_id]["result_code"]
            and task_events.get("codex_turn_started", 0) == 0
            and task_events.get("codex_turn_runtime_rehydrated_from_unreadable_dispatch") == 1
            and task_events.get("recovery_queued_rehydrated_from_materialized_turn") == 1
            and events.get("recovery_queued_reverted_to_pending") == 2
            and events.get("codex_turn_materialization_readback_not_confirmed") == 1
            and not get_delivery_retry(queue, task_id).get("active")
            and not get_delivery_retry(queue, bad_task_id).get("active")
            and not get_delivery_retry(queue, ghost_task_id).get("active")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "bad_task_status": bad_after.get("status"),
            "ghost_task_status": ghost_after.get("status"),
            "turn_id": queue.runtime_get(task_turn_key(task_id)),
            "batch_id": batch_id,
            "expected_task_ids": expected_task_ids,
            "ack_code": ack_codes.get(task_id),
            "result_code": result_codes.get(task_id),
            "events": events,
            "task_events": task_events,
            "good_retry_active": bool(get_delivery_retry(queue, task_id).get("active")),
            "bad_retry_active": bool(get_delivery_retry(queue, bad_task_id).get("active")),
            "ghost_retry_active": bool(get_delivery_retry(queue, ghost_task_id).get("active")),
            "assertion": "queued app-server tasks without codex_turn_started rehydrate only after app-server readback confirms an owned marker; markerless or unreadable failures revert to pending without being promoted from local prompt evidence",
        }

def pending_backlog_supplement_batch_check() -> dict[str, Any]:
    """Temp-only check that only later pending rows become MCP supplements."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-pending-backlog-supp-") as temp_root:
        temp = Path(temp_root)
        user = "primary-pending-backlog@im.wechat"
        state_dir = temp / "openclaw-state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        (accounts_dir / "primary.json").write_text(
            json.dumps({"userId": user, "token": "test-token"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "primary.context-tokens.json").write_text(
            json.dumps({user: "test-context"}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
                "phone_status_ack_events": [
                    "status_ack_delivery_queue_entered",
                    "status_ack_pending_backlog_supplement",
                    "status_ack_dispatching",
                    "status_ack_dispatched",
                ],
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "accounts": {"users": {user: {"account_id": "primary"}}},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "codex_thread_id": "thread-1",
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 10,
                "supplement_ack_grace_seconds": 30,
            },
            "threads": {
                "default_id": "visible-thread",
                "items": [{"id": "visible-thread", "name": "Visible Thread", "thread_id": "thread-1"}],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "visible-thread")
        first = queue.enqueue(
            "owner prompt only",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "pending-backlog-1", "receiver_account_id": "primary"},
        )
        second = queue.enqueue(
            "later pending supplement A",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "pending-backlog-2", "receiver_account_id": "primary"},
        )
        third = queue.enqueue(
            "later pending supplement B",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "pending-backlog-3", "receiver_account_id": "primary"},
        )
        first_id = str(first["id"])
        second_id = str(second["id"])
        third_id = str(third["id"])
        member_ids = [second_id, third_id]
        dispatched: list[dict[str, Any]] = []
        status_acks: list[dict[str, str]] = []
        pushed: list[str] = []

        original_check = globals()["check_codex_health"]
        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        original_enforce_ask_scope = globals()["enforce_ask_scope_for_task"]
        original_mcp_gate = globals()["current_mcp_session_gate_for_dispatch"]
        original_continuation = globals()["get_continuation_context"]
        original_push = globals()["push_final_reply_async"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result_cdp(_config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": False,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
                "status": "completed",
                "newText": "owner final only",
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            dispatch_config: dict[str, Any],
            continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            owner_ids = [str(item) for item in dispatch_config.get("_delivery_group_result_owner_task_ids") or [] if str(item)]
            prompt = task_prompt(tasks, continuation, config=dispatch_config)
            dispatched.append(
                {
                    "thread_id": thread_id,
                    "task_ids": task_ids,
                    "owner_ids": owner_ids,
                    "prompt": prompt,
                }
            )
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-pending-backlog",
                "client_user_message_id": "batch-pending-backlog",
                "expected_task_ids": owner_ids,
                "mobile_protocols": {task_id: mobile_protocol(task_id, "batch-pending-backlog") for task_id in owner_ids},
            }

        def fake_send_status_ack(
            queue_arg: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            event_type_arg: str,
        ) -> dict[str, Any]:
            status_acks.append(
                {
                    "task_id": str(task_arg.get("id") or ""),
                    "event_type": event_type_arg,
                    "text": text_arg,
                }
            )
            queue_arg.add_event("wecom", event_type_arg, {"ok": True, "test": True}, str(task_arg.get("id") or ""))
            return {"ok": True, "mode": "test"}

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            _text: str,
            _config: dict[str, Any],
            media: str = "",
        ) -> dict[str, Any]:
            pushed.append(str(task.get("id") or ""))
            return {"ok": True, "async": True, "media": media}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                dispatch_result = worker_once(queue, config, limit=5)
            after_dispatch = {task_id: queue.get_task(task_id) or {} for task_id in [first_id, *member_ids]}
            bridge_payload_raw = str(queue.runtime_get(bridge_supplement_key("thread-1")) or "")
            try:
                bridge_payload = json.loads(bridge_payload_raw) if bridge_payload_raw else {}
            except json.JSONDecodeError:
                bridge_payload = {}
            recovery_result = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack
            globals()["push_final_reply_async"] = original_push

        after_recovery = {task_id: queue.get_task(task_id) or {} for task_id in [first_id, *member_ids]}
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT task_id, event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id IN (?,?,?)
                GROUP BY task_id, event_type
                """,
                (first_id, second_id, third_id),
            ).fetchall()
        events = {(str(row["task_id"] or ""), str(row["event_type"] or "")): int(row["n"]) for row in rows}
        prompt = str(dispatched[0].get("prompt") or "") if dispatched else ""
        payload_item_ids = bridge_supplement_task_ids(bridge_payload if isinstance(bridge_payload, dict) else {})
        backlog_ack_count = len([item for item in status_acks if item.get("event_type") == "status_ack_pending_backlog_supplement"])
        member_event_absent = all(
            events.get((task_id, event_type), 0) == 0
            for task_id in member_ids
            for event_type in (
                "queued_for_codex",
                "sent_to_codex",
                "codex_turn_started",
                "delivery_group_member",
                "delivery_group_member_completed",
            )
        )
        ok = bool(
            dispatch_result.get("action") == "dispatched_waiting_result"
            and dispatch_result.get("processed") == 1
            and dispatch_result.get("supplement_member_count") == 2
            and dispatched
            and dispatched[0].get("task_ids") == [first_id]
            and dispatched[0].get("owner_ids") == [first_id]
            and "owner prompt only" in prompt
            and "later pending supplement A" not in prompt
            and "later pending supplement B" not in prompt
            and after_dispatch[first_id].get("status") == "sent_to_codex"
            and all(after_dispatch[task_id].get("status") == "pending" for task_id in member_ids)
            and all(after_dispatch[task_id].get("queued_for_codex_at") is None for task_id in member_ids)
            and all(after_dispatch[task_id].get("sent_to_codex_at") is None for task_id in member_ids)
            and isinstance(bridge_payload, dict)
            and str(bridge_payload.get("base_message_id") or "") == first_id
            and str(bridge_payload.get("supplement_source") or "") == "pending_backlog"
            and payload_item_ids == member_ids
            and all(task_is_supplement_context(queue, task_id) for task_id in member_ids)
            and all(pending_task_has_unacked_bridge_supplement(queue, task_id, "thread-1") for task_id in member_ids)
            and all(bridge_supplement_base_task_id_from_events(queue, task_id, "thread-1") == first_id for task_id in member_ids)
            and not queue.runtime_get(delivery_group_members_key(first_id))
            and backlog_ack_count == 1
            and member_event_absent
            and recovery_result.get("recovered") == 1
            and after_recovery[first_id].get("status") == "done"
            and after_recovery[first_id].get("result") == "owner final only"
            and all(after_recovery[task_id].get("status") == "pending" for task_id in member_ids)
            and pushed == [first_id]
        )
        return {
            "ok": ok,
            "temp_only": True,
            "dispatch_result": dispatch_result,
            "recovery_result": recovery_result,
            "dispatched": dispatched,
            "status_acks": status_acks,
            "pushed": pushed,
            "statuses_after_dispatch": {task_id: after_dispatch[task_id].get("status") for task_id in [first_id, *member_ids]},
            "statuses_after_recovery": {task_id: after_recovery[task_id].get("status") for task_id in [first_id, *member_ids]},
            "payload_item_ids": payload_item_ids,
            "bridge_payload_base": bridge_payload.get("base_message_id") if isinstance(bridge_payload, dict) else "",
            "delivery_group_members_runtime": queue.runtime_get(delivery_group_members_key(first_id)),
            "member_is_context": {task_id: task_is_supplement_context(queue, task_id) for task_id in member_ids},
            "member_has_unacked_bridge_supplement": {
                task_id: pending_task_has_unacked_bridge_supplement(queue, task_id, "thread-1") for task_id in member_ids
            },
            "events": {f"{task_id}:{event_type}": count for (task_id, event_type), count in events.items()},
            "assertion": "only the oldest pending task is delivered; later same-route pending rows stay pending and are exposed only through MCP supplement",
        }

def delivery_group_owner_check() -> dict[str, Any]:
    """Temp-only check that merged delivery batches produce one final owner."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-delivery-group-") as temp_root:
        temp = Path(temp_root)
        user = "group-owner@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {
                "account_id": "backup1",
                "phone_status_ack_events": [
                    "status_ack_delivery_queue_entered",
                    "status_ack_dispatching",
                    "status_ack_dispatched",
                    "status_ack_delivery_group_supplement",
                ],
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "cooldown_seconds": 0,
                "worker_dispatch_attempts_per_cycle": 4,
                "active_recovery_max_sent_checks_per_cycle": 10,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "group-owner-thread",
                        "name": "Group Owner Thread",
                        "aliases": [],
                        "thread_id": "thread-group-owner",
                    }
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "group-owner-thread")
        first = queue.enqueue(
            "first question",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "group-owner-1", "receiver_account_id": "backup1"},
        )
        second = queue.enqueue(
            "second supplement",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "group-owner-2", "receiver_account_id": "backup1"},
        )
        first_id = str(first["id"])
        second_id = str(second["id"])
        dispatched: list[dict[str, Any]] = []
        status_acks: list[dict[str, str]] = []
        pushed: list[str] = []

        original_dispatch = globals()["dispatch_to_codex"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "thread_name": thread_name,
                "listed": True,
                "listed_status": {"type": "idle"},
                "stabilize_name": stabilize_name,
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            dispatch_config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            owner_ids = [str(item) for item in dispatch_config.get("_delivery_group_result_owner_task_ids") or [] if str(item)]
            dispatched.append({"thread_id": thread_id, "task_ids": task_ids, "owner_ids": owner_ids})
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-group-owner",
                "client_user_message_id": "batch-group-owner",
                "expected_task_ids": owner_ids,
                "mobile_protocols": {task_id: mobile_protocol(task_id, "batch-group-owner") for task_id in owner_ids},
            }

        def fake_send_status_ack(
            queue_arg: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            event_type_arg: str,
        ) -> dict[str, Any]:
            status_acks.append(
                {
                    "task_id": str(task_arg.get("id") or ""),
                    "event_type": event_type_arg,
                    "text": text_arg,
                }
            )
            queue_arg.add_event("wecom", event_type_arg, {"ok": True, "test": True}, str(task_arg.get("id") or ""))
            return {"ok": True, "mode": "test"}

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            _expected_result_codes: dict[str, str] | None = None,
            _expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "single grouped final",
                "expected_task_ids_seen": expected_task_ids or [],
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            _text: str,
            _config: dict[str, Any],
            media: str = "",
        ) -> dict[str, Any]:
            pushed.append(str(task.get("id") or ""))
            return {"ok": True, "async": True, "media": media}

        try:
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                dispatch_result = worker_once(queue, config, limit=5)
            old_stamp = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            newer_stamp = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with queue.session() as db:
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (old_stamp, old_stamp, second_id),
                )
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (newer_stamp, newer_stamp, first_id),
                )
            recovery_member_first = recover_active_codex_tasks(queue, config, max_sent_checks=1)
            member_mid = queue.get_task(second_id) or {}
            member_mid_runtime = task_batch_runtime(queue, second_id)
            recovery_result = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["send_status_ack"] = original_status_ack
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        first_after = queue.get_task(first_id) or {}
        second_after = queue.get_task(second_id) or {}
        owner_runtime = task_batch_runtime(queue, first_id)
        member_runtime = task_batch_runtime(queue, second_id)
        with queue.session() as db:
            events = {
                (str(row["task_id"] or ""), str(row["event_type"] or "")): int(row["n"])
                for row in db.execute(
                    """
                    SELECT task_id, event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id IN (?,?)
                    GROUP BY task_id, event_type
                    """,
                    (first_id, second_id),
                ).fetchall()
            }
        group_supplement_acks = [
            item for item in status_acks if item.get("event_type") == "status_ack_delivery_group_supplement"
        ]
        ok = bool(
            dispatch_result.get("action") == "dispatched_waiting_result"
            and dispatched
            and dispatched[0].get("task_ids") == [first_id, second_id]
            and dispatched[0].get("owner_ids") == [first_id]
            and first_after.get("status") == "done"
            and first_after.get("result") == "single grouped final"
            and not first_after.get("push_status")
            and second_after.get("status") == "done"
            and str(second_after.get("result") or "").startswith("[supplement] consumed by delivery group")
            and not second_after.get("push_status")
            and pushed == [first_id]
            and len(group_supplement_acks) == 1
            and events.get((second_id, "delivery_group_member"), 0) == 1
            and events.get((second_id, "delivery_group_member_completed"), 0) == 1
            and recovery_member_first.get("recovered") == 0
            and recovery_member_first.get("still_waiting") >= 1
            and member_mid.get("status") == "sent_to_codex"
            and member_mid_runtime[0] == "batch-group-owner"
            and member_mid_runtime[1] == []
            and recovery_result.get("recovered") == 1
            and recovery_result.get("still_waiting") >= 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "dispatch_result": dispatch_result,
            "recovery_member_first": recovery_member_first,
            "recovery_result": recovery_result,
            "dispatched": dispatched,
            "status_acks": status_acks,
            "pushed": pushed,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "second_result": second_after.get("result"),
            "owner_runtime": owner_runtime,
            "member_runtime": member_runtime,
            "events": {f"{task_id}:{event_type}": count for (task_id, event_type), count in events.items()},
            "assertion": "merged delivery group has exactly one final reply owner; member is a supplement and never pushes its own final reply",
        }

def supplement_ack_gating_check() -> dict[str, Any]:
    """Temp-only check that supplements keep the received ack but suppress normal flow acks."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-ack-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        active_id = "active-task"
        supplement_id = "supplement-task"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    active_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "active question",
                    hashlib.sha256(b"active question").hexdigest(),
                    "supp-ack-active",
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    supplement_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "supplement detail",
                    hashlib.sha256(b"supplement detail").hexdigest(),
                    "supp-ack-supplement",
                    "L1",
                    "pending",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                ),
            )

        config = {
            "openclaw": {
                "account_id": "backup1",
                "phone_status_ack_events": [
                    "status_ack_received",
                    "status_ack_continuation_deferred",
                    "status_ack_pending_backlog_supplement",
                    "status_ack_delivery_queue_entered",
                    "status_ack_dispatching",
                    "status_ack_dispatched",
                ],
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }

        received_allowed_before_supplement = not should_suppress_supplement_status_ack(
            queue,
            supplement_id,
            "status_ack_received",
        )
        queue.add_event(
            "local",
            "continuation_deferred",
            {
                "route_key": task_route_key("codex-app-server", "thread-1"),
                "thread_id": "thread-1",
                "delivery_mode": "codex-app-server",
                "reason": "test supplement context marker",
            },
            supplement_id,
        )
        queue.add_event(
            "local",
            "thread_delivery_route_busy",
            {
                "route_key": task_route_key("codex-app-server", "thread-1"),
                "thread_id": "thread-1",
                "delivery_mode": "codex-app-server",
                "deferred_as_continuation": True,
            },
            supplement_id,
        )
        queue.runtime_set(
            bridge_supplement_key("thread-1"),
            json.dumps(
                {
                    "base_message_id": active_id,
                    "active_task_id": active_id,
                    "thread_id": "thread-1",
                    "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, "thread-1")],
                    "published_at": now,
                    "supplement_signature": "ack-gating",
                },
                ensure_ascii=False,
            ),
        )
        received_allowed_after_supplement = not should_suppress_supplement_status_ack(
            queue,
            supplement_id,
            "status_ack_received",
        )
        supplement_notice_allowed = not should_suppress_supplement_status_ack(
            queue,
            supplement_id,
            "status_ack_continuation_deferred",
        )
        pending_backlog_notice_enabled = phone_status_ack_enabled(
            config,
            "status_ack_pending_backlog_supplement",
        )
        pending_backlog_notice_allowed = not should_suppress_supplement_status_ack(
            queue,
            supplement_id,
            "status_ack_pending_backlog_supplement",
        )
        delivery_queue = send_status_ack(
            queue,
            queue.get_task(supplement_id) or {},
            "已进入 Codex 投递队列，正在准备投递。",
            config,
            "status_ack_delivery_queue_entered",
        )
        dispatching = send_status_ack(
            queue,
            queue.get_task(supplement_id) or {},
            "正在投递到 Codex。",
            config,
            "status_ack_dispatching",
        )
        dispatched = send_status_ack(
            queue,
            queue.get_task(supplement_id) or {},
            "已投递到 Codex，正在思考。",
            config,
            "status_ack_dispatched",
        )
        waiting = send_status_ack(
            queue,
            queue.get_task(supplement_id) or {},
            "仍在处理，已等待 60 秒。",
            config,
            "status_ack_waiting",
        )

        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (supplement_id,),
                ).fetchall()
            }
        ok = bool(
            received_allowed_before_supplement
            and received_allowed_after_supplement
            and supplement_notice_allowed
            and pending_backlog_notice_enabled
            and pending_backlog_notice_allowed
            and delivery_queue.get("suppressed")
            and dispatching.get("suppressed")
            and dispatched.get("suppressed")
            and waiting.get("suppressed")
            and events.get("status_ack_delivery_queue_entered_suppressed") == 1
            and events.get("status_ack_dispatching_suppressed") == 1
            and events.get("status_ack_dispatched_suppressed") == 1
            and events.get("status_ack_waiting_suppressed") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "events": events,
            "received_allowed_before_supplement": received_allowed_before_supplement,
            "received_allowed_after_supplement": received_allowed_after_supplement,
            "supplement_notice_allowed": supplement_notice_allowed,
            "pending_backlog_notice_enabled": pending_backlog_notice_enabled,
            "pending_backlog_notice_allowed": pending_backlog_notice_allowed,
            "delivery_queue": delivery_queue,
            "dispatching": dispatching,
            "dispatched": dispatched,
            "waiting": waiting,
            "assertion": "supplements keep status_ack_received and supplement-specific notices, including pending backlog notices, but suppress later normal delivery/thinking acknowledgements",
        }

def followup_redelivery_mcp_supplement_check() -> dict[str, Any]:
    """Temp-only check that follow-up redelivery keeps later messages as MCP supplements."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-followup-mcp-supp-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        user = "primary-followup@im.wechat"
        owner_id = "owner-redelivery"
        supplement_id = "supplement-followup"
        old = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
        new = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            for tid, text, created_at in [
                (owner_id, "original owner message", old),
                (supplement_id, "follow-up supplement", new),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        "pending",
                        "",
                        "",
                        "primary",
                        "thread-1",
                        "{}",
                        created_at,
                        created_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-1",
                "turn_id": "old-owner-turn",
                "client_message_id": "old-owner-batch",
                "expected_task_ids": [owner_id],
            },
            owner_id,
        )
        mark_delivery_retry(
            queue,
            {
                "trigger": {
                    "delivery_retry_seconds": 0,
                    "delivery_retry_reason_seconds": {"terminal_without_owned_result": 0},
                },
            },
            [owner_id],
            "terminal_without_owned_result",
            {
                "sent_to_codex_at": old,
                "created_at": old,
                "reason": "test owner released for follow-up redelivery",
            },
        )
        retry_payload = get_delivery_retry(queue, owner_id)
        retry_payload["retry_after"] = old
        queue.runtime_set(delivery_retry_key(owner_id), json.dumps(retry_payload, ensure_ascii=False))
        supplement_payload = {
            "base_message_id": owner_id,
            "active_task_id": owner_id,
            "thread_id": "thread-1",
            "delivery_mode": "codex-cdp",
            "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, "thread-1")],
            "published_at": new,
            "supplement_signature": "followup-supplement",
        }
        queue.runtime_set(bridge_supplement_key("thread-1"), json.dumps(supplement_payload, ensure_ascii=False))
        queue.runtime_set(attachment_supplement_signature_key(owner_id), "followup-supplement")
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {
                "active_task_id": owner_id,
                "thread_id": "thread-1",
                "delivery_mode": "codex-cdp",
                "signature": "followup-supplement",
            },
            supplement_id,
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
                "account_onboarding_worker_sync_enabled": False,
            },
            "openclaw_accounts": {"primary": {"userId": user, "token": "fixture-token"}},
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "codex_thread_id": "thread-1",
                "delivery_retry_seconds": 0,
                "delivery_retry_reason_seconds": {"terminal_without_owned_result": 0},
                "active_recovery_max_sent_checks_per_cycle": 0,
                "supplement_ack_grace_seconds": 30,
            },
            "threads": {
                "default_id": "visible-thread",
                "items": [{"id": "visible-thread", "name": "Visible Thread", "thread_id": "thread-1"}],
            },
        }
        queue.config = config
        release_before_worker = release_invalid_published_supplements(queue, queue.list_pending(10), config)
        set_active_thread(queue, user, "visible-thread")
        dispatches: list[dict[str, Any]] = []
        original_check = globals()["check_codex_health"]
        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        original_enforce_ask_scope = globals()["enforce_ask_scope_for_task"]
        original_mcp_gate = globals()["current_mcp_session_gate_for_dispatch"]
        original_continuation = globals()["get_continuation_context"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result_cdp(_config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": False,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            dispatches.append({"thread_id": thread_id, "task_ids": task_ids})
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-redelivery",
                "client_user_message_id": "batch-redelivery",
                "expected_task_ids": [owner_id],
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        def fake_enforce_ask_scope_for_task(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "allowed": True, "reason": "fixture focuses on supplement redelivery behavior"}

        def fake_current_mcp_session_gate_for_dispatch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "reason": "fixture isolates MCP session gate"}

        def fake_get_continuation_context(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"active": False, "reason": "fixture isolates continuation window"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["enforce_ask_scope_for_task"] = fake_enforce_ask_scope_for_task
            globals()["current_mcp_session_gate_for_dispatch"] = fake_current_mcp_session_gate_for_dispatch
            globals()["get_continuation_context"] = fake_get_continuation_context
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5, task_id=owner_id)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack
            globals()["enforce_ask_scope_for_task"] = original_enforce_ask_scope
            globals()["current_mcp_session_gate_for_dispatch"] = original_mcp_gate
            globals()["get_continuation_context"] = original_continuation

        owner_after = queue.get_task(owner_id) or {}
        supplement_after = queue.get_task(supplement_id) or {}
        bridge_payload = str(queue.runtime_get(bridge_supplement_key("thread-1")) or "")
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=?
                GROUP BY event_type
                """,
                (supplement_id,),
            ).fetchall()
        events = {str(row["event_type"]): int(row["n"]) for row in rows}
        ok = bool(
            release_before_worker.get("released_count") == 0
            and
            result.get("action") == "dispatched_waiting_result"
            and dispatches == [{"thread_id": "thread-1", "task_ids": [owner_id]}]
            and owner_after.get("status") == "sent_to_codex"
            and supplement_after.get("status") == "pending"
            and supplement_id in bridge_payload
            and task_is_supplement_context(queue, supplement_id)
            and events.get("delivery_group_member", 0) == 0
            and events.get("published_supplement_released_owner_preserved", 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "release_before_worker": release_before_worker,
            "worker_result": result,
            "dispatches": dispatches,
            "statuses": {
                owner_id: owner_after.get("status"),
                supplement_id: supplement_after.get("status"),
            },
            "bridge_supplement_present": bool(bridge_payload),
            "supplement_is_context": task_is_supplement_context(queue, supplement_id),
            "events": events,
            "assertion": "a follow-up message published for MCP remains a pending supplement while the released owner is redelivered alone",
        }

def orphaned_supplement_promotion_check() -> dict[str, Any]:
    """Temp-only check that finished-base supplements promote in FIFO order."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-orphan-supp-promotion-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        now = now_dt.isoformat()
        base_id = "base-finished"
        first_id = "supp-first"
        second_id = "supp-second"
        thread_id = "thread-1"
        user = "user@im.wechat"
        rows = [
            (base_id, "done", "base prompt", old, old, "base final result"),
            (first_id, "pending", "first orphan supplement", old, old, ""),
            (second_id, "pending", "second orphan supplement", now, now, ""),
        ]
        with queue.session() as db:
            for task_id, status, text, created_at, updated_at, result in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        completed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        result,
                        "",
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        updated_at,
                        updated_at if status == "done" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
                "delivery_mode": "codex-app-server",
            },
            base_id,
        )
        items = [
            task_supplement_snapshot(queue.get_task(first_id) or {}, thread_id),
            task_supplement_snapshot(queue.get_task(second_id) or {}, thread_id),
        ]
        payload = {
            "base_message_id": base_id,
            "thread_id": thread_id,
            "active_task_id": base_id,
            "delivery_mode": "codex-app-server",
            "items": items,
            "published_at": old,
            "supplement_signature": "finished-base-supplements",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        queue.runtime_set(attachment_supplement_signature_key(base_id), "finished-base-supplements")
        for task_id in [first_id, second_id]:
            queue.add_event(
                "local",
                "attachment_supplement_pending_published",
                {
                    "active_task_id": base_id,
                    "thread_id": thread_id,
                    "delivery_mode": "codex-app-server",
                    "signature": "finished-base-supplements",
                },
                task_id,
            )

        config = {
            "openclaw": {"account_id": "backup1", "phone_status_ack_events": [], "account_onboarding_worker_sync_enabled": False},
            "openclaw_accounts": {"primary": {"userId": user, "token": "fixture-token"}},
            "queue": {"db_path": str(temp / "queue.db")},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "auto_reply": False,
                "supplement_ack_grace_seconds": 10,
            },
            "threads": {
                "default_id": "test-thread",
                "items": [{"id": "test-thread", "name": "Test Thread", "thread_id": thread_id}],
            },
        }
        queue.config = config
        set_active_thread(queue, user, "test-thread")
        dispatches: list[list[str]] = []
        original_dispatch = globals()["dispatch_to_codex"]
        original_inspect = globals()["inspect_codex_thread_for_dispatch"]
        original_enforce_ask_scope = globals()["enforce_ask_scope_for_task"]

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            _thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            dispatches.append([str(task.get("id") or "") for task in tasks])
            return {
                "ok": True,
                "thread_id": _thread_id,
                "turn_id": "turn-promoted-first",
                "client_user_message_id": "batch-promoted-first",
                "expected_task_ids": [first_id],
            }

        def fake_inspect_codex_thread_for_dispatch(
            _config: dict[str, Any],
            inspected_thread_id: str,
            thread_name: str = "",
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "listed": True,
                "resume_ok": True,
                "turns_ok": True,
                "thread_id": inspected_thread_id,
                "thread_name": thread_name,
                "state": "idle",
                "listed_status": {"type": "idle"},
            }

        def fake_enforce_ask_scope_for_task(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "allowed": True, "reason": "fixture focuses on supplement promotion behavior"}

        try:
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["inspect_codex_thread_for_dispatch"] = fake_inspect_codex_thread_for_dispatch
            globals()["enforce_ask_scope_for_task"] = fake_enforce_ask_scope_for_task
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["inspect_codex_thread_for_dispatch"] = original_inspect
            globals()["enforce_ask_scope_for_task"] = original_enforce_ask_scope

        first_after = queue.get_task(first_id) or {}
        second_after = queue.get_task(second_id) or {}
        runtime_payload = json.loads(str(queue.runtime_get(bridge_supplement_key(thread_id)) or "{}") or "{}")
        runtime_task_ids = bridge_supplement_task_ids(runtime_payload)
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id IN (?,?)
                    GROUP BY event_type
                    """,
                    (first_id, second_id),
                ).fetchall()
            }
        ok = bool(
            result.get("ok")
            and dispatches == [[first_id]]
            and first_after.get("status") == "sent_to_codex"
            and not task_is_supplement_context(queue, first_id)
            and second_after.get("status") == "pending"
            and task_is_supplement_context(queue, second_id)
            and bridge_supplement_base_task_id(runtime_payload) == first_id
            and runtime_task_ids == [second_id]
            and int((result.get("orphaned_supplement_promotion") or {}).get("promoted_count") or 0) == 1
            and int((result.get("orphaned_supplement_promotion") or {}).get("resumed_count") or 0) == 1
            and events.get("supplement_promoted_to_owner") == 1
            and task_event_exists(queue, first_id, "supplement_owner_reschedule_requested")
            and events.get("supplement_rebased_to_promoted_owner") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "dispatches": dispatches,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "first_is_supplement": task_is_supplement_context(queue, first_id),
            "second_is_supplement": task_is_supplement_context(queue, second_id),
            "runtime_base_task_id": bridge_supplement_base_task_id(runtime_payload),
            "runtime_task_ids": runtime_task_ids,
            "events": events,
            "assertion": "when a base owner already has a final result, the first unconsumed supplement becomes the next owner and later supplements rebase behind it",
        }

def pending_visible_cdp_multi_supplement_consumption_check() -> dict[str, Any]:
    """Temp-only check that recovery consumes only matched items and preserves residual supplements."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-multi-consume-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        thread_id = "thread-1"
        owner_id = "base-owner"
        first_id = "supp-first"
        second_id = "supp-second"
        user = "user@im.wechat"
        rows = [
            (owner_id, "pending", "base prompt", old, old, "", "", ""),
            (first_id, "pending", "first supplement", old, old, "", "", ""),
            (second_id, "pending", "second supplement", now_dt.isoformat(), now_dt.isoformat(), "", "", ""),
        ]
        with queue.session() as db:
            for task_id, status, text, created_at, updated_at, result, push_status, error in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, error, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        completed_at, pushed_at, queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        result,
                        error,
                        push_status,
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        updated_at,
                        updated_at if terminal_failed_status(status) else None,
                        None,
                        None,
                        old if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "client_message_id": "batch-base-owner",
                "expected_task_ids": [owner_id],
            },
            owner_id,
        )
        delivery_payload_owner = {
            "delivery": {
                "mode": "codex-cdp",
                "reason": "cdp_visible_input_unconfirmed_observing",
                "client_user_message_id": "batch-base-owner",
                "expected_task_ids": [owner_id],
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "baseline_key": "baseline-base-owner",
                "mobile_protocols": {owner_id: mobile_protocol(owner_id, "batch-base-owner")},
            }
        }
        delivery_payload_second = {
            "delivery": {
                "mode": "codex-cdp",
                "reason": "cdp_visible_input_unconfirmed_observing",
                "client_user_message_id": "batch-base-owner",
                "expected_task_ids": [owner_id],
                "thread_id": thread_id,
                "turn_id": "turn-base-owner",
                "baseline_key": "baseline-base-owner",
                "mobile_protocols": {owner_id: mobile_protocol(owner_id, "batch-base-owner")},
            }
        }
        queue.add_event("local", "delivery_failed_reverted_to_pending", delivery_payload_owner, owner_id)
        queue.add_event("local", "delivery_failed_reverted_to_pending", delivery_payload_owner, first_id)
        queue.add_event("local", "delivery_failed_reverted_to_pending", delivery_payload_second, second_id)
        queue.add_event(
            "local",
            "pending_backlog_supplement_pending_published",
            {
                "owner_task_id": owner_id,
                "thread_id": thread_id,
                "signature": "base-owner-multi-supp",
            },
            second_id,
        )
        payload = {
            "base_message_id": owner_id,
            "thread_id": thread_id,
            "active_task_id": owner_id,
            "delivery_mode": "codex-app-server",
            "items": [
                task_supplement_snapshot(queue.get_task(first_id) or {}, thread_id),
                task_supplement_snapshot(queue.get_task(second_id) or {}, thread_id),
            ],
            "published_at": old,
            "supplement_signature": "base-owner-multi-supp",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))

        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            *_args: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            matched = expected_task_ids[0] if expected_task_ids else owner_id
            return {
                "ok": True,
                "healthy": True,
                "newText": "base recovered result",
                "ownership": {
                    "valid": True,
                    "matched_task_id": matched,
                    "expected_task_ids": expected_task_ids or [],
                },
            }

        pushed: list[dict[str, Any]] = []

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            media: str = "",
        ) -> dict[str, Any]:
            pushed.append({"task_id": str(task.get("id") or ""), "text": text, "media": media})
            return {"ok": True, "async": True, "mode": "test"}

        try:
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            result = recover_pending_visible_cdp_unconfirmed_results(
                queue,
                {
                    "trigger": {"delivery_mode": "codex-cdp"},
                },
                queue.list_pending(10),
            )
        finally:
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        first_after = queue.get_task(first_id) or {}
        second_after = queue.get_task(second_id) or {}
        runtime_raw = queue.runtime_get(bridge_supplement_key(thread_id))
        runtime_payload = json.loads(runtime_raw) if runtime_raw else {}
        runtime_ids = bridge_supplement_task_ids(runtime_payload) if runtime_payload else []
        ok = bool(
            result.get("recovered_count") == 1
            and first_after.get("status") == "done"
            and second_after.get("status") == "pending"
            and runtime_ids == []
            and runtime_payload == {}
            and pushed == [{"task_id": owner_id, "text": "base recovered result", "media": ""}]
            and task_event_exists(queue, owner_id, "pending_visible_cdp_unconfirmed_runtime_pruned")
            and task_event_exists(queue, owner_id, "pending_visible_cdp_unconfirmed_result_recovered")
            and task_event_exists(queue, first_id, "pending_visible_cdp_unconfirmed_member_consumed")
            and task_event_exists(queue, second_id, "supplement_promoted_to_owner")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": result,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "runtime_task_ids": runtime_ids,
            "runtime_payload": runtime_payload,
            "pushed": pushed,
            "assertion": "base recovery consumes matched supplement items first, prunes only consumed items, and leaves residual supplements available for later promotion",
        }

def active_visible_cdp_supplement_publish_check() -> dict[str, Any]:
    """Temp-only check that pre-delivery CDP busy is advisory, not blocking."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-cdp-supp-") as temp_root:
        temp = Path(temp_root)
        user = "primary-active-cdp@im.wechat"
        owner_id = "active-visible-owner"
        supplement_id = "active-visible-followup"
        now = datetime.now(timezone.utc).isoformat()
        state_dir = temp / "openclaw-state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        (accounts_dir / "primary.json").write_text(
            json.dumps({"userId": user, "token": "test-token"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "primary.context-tokens.json").write_text(
            json.dumps({user: "test-context"}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
                "phone_status_ack_events": [],
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "codex_thread_id": "thread-1",
                "active_recovery_max_sent_checks_per_cycle": 0,
                "supplement_ack_grace_seconds": 30,
                "delivery_retry_seconds": 0,
                "visible_cdp_busy_retry_seconds": 0,
            },
            "threads": {
                "default_id": "visible-thread",
                "items": [{"id": "visible-thread", "name": "Visible Thread", "thread_id": "thread-1"}],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "visible-thread")
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    supplement_id,
                    "openclaw-weixin",
                    user,
                    user,
                    "/ask",
                    "hello",
                    hashlib.sha256(b"hello").hexdigest(),
                    supplement_id,
                    "L1",
                    "pending",
                    "",
                    "",
                    "primary",
                    "",
                    json.dumps({"msg_id": supplement_id, "receiver_account_id": "primary"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        original_check = globals()["check_codex_health"]
        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_publish_active = globals()["publish_attachment_active_supplements"]
        original_dispatch = globals()["dispatch_to_codex"]

        dispatches: list[list[str]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result_cdp(_config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": True,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
            }

        def fake_publish_attachment_active_supplements(
            _queue: MobileQueue,
            _config: dict[str, Any],
            _pending: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {"ok": True, "published": [], "duplicates": [], "failed": [], "suppressed": ["test prepass miss"]}

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            _thread_id: str,
            _dispatch_config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            dispatches.append([str(task.get("id") or "") for task in tasks])
            return {
                "ok": True,
                "mode": "codex-cdp",
                "turn_id": "turn-advisory-busy",
                "client_user_message_id": "batch-advisory-busy",
                "baseline_key": "assistant-before",
                "expected_task_ids": [str(tasks[0].get("id") or "")] if tasks else [],
                "mobile_protocols": {
                    str(task.get("id") or ""): {
                        "ack_code": "ack-test",
                        "result_code": "result-test",
                    }
                    for task in tasks
                },
                "desktop_visible": {"confirmed": True, "body_has_exact_prompt": True},
            }

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["publish_attachment_active_supplements"] = fake_publish_attachment_active_supplements
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["publish_attachment_active_supplements"] = original_publish_active
            globals()["dispatch_to_codex"] = original_dispatch

        task_after = queue.get_task(supplement_id) or {}
        bridge_payload_raw = str(queue.runtime_get(bridge_supplement_key("thread-1")) or "")
        try:
            bridge_payload = json.loads(bridge_payload_raw) if bridge_payload_raw else {}
        except json.JSONDecodeError:
            bridge_payload = {}
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=?
                GROUP BY event_type
                """,
                (supplement_id,),
            ).fetchall()
        events = {str(row["event_type"]): int(row["n"]) for row in rows}
        retry_payload = get_delivery_retry(queue, supplement_id)
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and result.get("processed") == 1
            and dispatches == [[supplement_id]]
            and task_after.get("status") == "sent_to_codex"
            and not bridge_payload
            and not task_is_supplement_context(queue, supplement_id)
            and not pending_task_has_unacked_bridge_supplement(queue, supplement_id, "thread-1")
            and not retry_payload.get("active")
            and events.get("thread_delivery_visible_cdp_busy_observed") == 1
            and events.get("visible_cdp_busy_supplement_published", 0) == 0
            and events.get("thread_delivery_visible_cdp_busy_supplement", 0) == 0
            and events.get("thread_delivery_visible_cdp_busy", 0) == 0
            and events.get("status_ack_visible_cdp_busy", 0) == 0
            and events.get("queued_for_codex", 0) == 1
            and events.get("sent_to_codex", 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "dispatches": dispatches,
            "task_status": task_after.get("status"),
            "task_error": task_after.get("error"),
            "bridge_payload_base": bridge_supplement_base_task_id(bridge_payload if isinstance(bridge_payload, dict) else {}),
            "supplement_is_context": task_is_supplement_context(queue, supplement_id),
            "retry_active": bool(retry_payload.get("active")),
            "events": events,
            "assertion": "pre-delivery visible-CDP busy is advisory only; it records evidence but does not block dispatch or publish a supplement",
        }

def followup_redelivery_fifo_supplement_check() -> dict[str, Any]:
    """Temp-only check that a new primary follow-up triggers FIFO owner redelivery via MCP supplement."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-followup-fifo-supp-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        user = "primary-fifo-followup@im.wechat"
        owner_id = "owner-waiting-redelivery"
        followup_id = "followup-empty-thread"
        old = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
        new = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            for tid, text, status, thread_id, created_at, queued_at, sent_at in [
                (owner_id, "original owner message", "sent_to_codex", "thread-1", old, old, old),
                (followup_id, "new follow-up message", "pending", "", new, None, None),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "primary",
                        thread_id,
                        "{}",
                        created_at,
                        created_at,
                        queued_at,
                        sent_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-1",
                "turn_id": "old-owner-turn",
                "client_message_id": "old-owner-batch",
                "expected_task_ids": [owner_id],
            },
            owner_id,
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "codex_thread_id": "thread-1",
                "cooldown_seconds": 0,
                "delivery_retry_seconds": 0,
                "delivery_retry_reason_seconds": {"terminal_without_owned_result": 0},
                "active_recovery_max_sent_checks_per_cycle": 5,
                "supplement_ack_grace_seconds": 30,
            },
            "threads": {
                "default_id": "visible-thread",
                "items": [{"id": "visible-thread", "name": "Visible Thread", "thread_id": "thread-1"}],
            },
        }
        queue.config = config
        set_active_thread(queue, user, "visible-thread")
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(owner_id) or {},
            "terminal_without_owned_result",
            {"reason": "temp regression owner waits for a same-thread follow-up before redelivery"},
        )
        dispatches: list[dict[str, Any]] = []
        original_check = globals()["check_codex_health"]
        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result_cdp(_config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": False,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            dispatches.append({"thread_id": thread_id, "task_ids": task_ids})
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-fifo-redelivery",
                "client_user_message_id": "batch-fifo-redelivery",
                "expected_task_ids": [owner_id],
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        owner_after = queue.get_task(owner_id) or {}
        followup_after = queue.get_task(followup_id) or {}
        bridge_payload = str(queue.runtime_get(bridge_supplement_key("thread-1")) or "")
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=?
                GROUP BY event_type
                """,
                (followup_id,),
            ).fetchall()
        events = {str(row["event_type"]): int(row["n"]) for row in rows}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and result.get("processed") == 1
            and dispatches == [{"thread_id": "thread-1", "task_ids": [owner_id]}]
            and owner_after.get("status") == "sent_to_codex"
            and followup_after.get("status") == "pending"
            and followup_after.get("queued_for_codex_at") is None
            and followup_id in bridge_payload
            and task_is_supplement_context(queue, followup_id)
            and pending_task_has_unacked_bridge_supplement(queue, followup_id)
            and latest_followup_trigger_owner(queue, followup_id) == owner_id
            and events.get("attachment_supplement_pending_published", 0) == 1
            and events.get("followup_triggered_waiting_redelivery", 0) == 1
            and events.get("delivery_group_member", 0) == 0
            and events.get("queued_for_codex", 0) == 0
            and events.get("sent_to_codex", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "dispatches": dispatches,
            "statuses": {
                owner_id: owner_after.get("status"),
                followup_id: followup_after.get("status"),
            },
            "followup_queued_for_codex_at": followup_after.get("queued_for_codex_at"),
            "bridge_supplement_present": bool(bridge_payload),
            "followup_is_context": task_is_supplement_context(queue, followup_id),
            "followup_has_unacked_bridge_supplement": pending_task_has_unacked_bridge_supplement(queue, followup_id),
            "events": events,
            "assertion": "a same-user primary follow-up with empty codex_thread_id becomes MCP supplement and the older owner is redelivered first",
        }

def supplement_final_owner_check() -> dict[str, Any]:
    """Temp-only check that consumed supplements do not become final-reply owners."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-owner-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        base_id = "base-task-1"
        supplement_id = "supp-task-1"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status, receiver_account_id,
                    codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    base_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "base question",
                    hashlib.sha256(b"base question").hexdigest(),
                    "supp-owner-base",
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status, receiver_account_id,
                    codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    supplement_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "supplement detail",
                    hashlib.sha256(b"supplement detail").hexdigest(),
                    "supp-owner-supplement",
                    "L1",
                    "pending",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                ),
            )
        payload = {
            "acked_at": now,
            "thread_id": "thread-1",
            "base_task_id": base_id,
            "supplement_signature": "sig-1",
            "ack_source": "test",
        }
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-1",
                "turn_id": "turn-base-1",
                "client_message_id": "batch-base-1",
                "expected_task_ids": [base_id],
            },
            base_id,
        )
        queue.runtime_set(mcp_ack_key(supplement_id), json.dumps(payload, ensure_ascii=False))
        completed = process_mcp_acked_pending_supplements(queue)
        supplement = queue.get_task(supplement_id) or {}
        base = queue.get_task(base_id) or {}
        reply_pending = pending_reply_batch_tasks(queue, supplement)
        with queue.session() as db:
            event = db.execute(
                """
                SELECT payload_json
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_acked_supplement_completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (supplement_id,),
            ).fetchone()
        event_payload: dict[str, Any] = {}
        if event:
            try:
                parsed = json.loads(event["payload_json"] or "{}")
                event_payload = parsed if isinstance(parsed, dict) else {}
            except Exception:
                event_payload = {}
        ok = bool(
            completed.get("completed") == [supplement_id]
            and supplement.get("status") == "done"
            and str(supplement.get("result") or "").startswith("[supplement]")
            and not supplement.get("push_status")
            and not reply_pending
            and base.get("status") == "sent_to_codex"
            and event_payload.get("base_task_id") == base_id
            and event_payload.get("policy") == "supplement_consumed_no_final_reply"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "completed": completed,
            "base_status": base.get("status"),
            "supplement_status": supplement.get("status"),
            "supplement_result": supplement.get("result"),
            "supplement_push_status": supplement.get("push_status"),
            "reply_pending_count": len(reply_pending),
            "event_payload": event_payload,
            "assertion": "MCP-acked supplement closes as internal consumed context and cannot become a final-reply owner",
        }

def delivery_group_owner_event_fallback_check() -> dict[str, Any]:
    """Temp-only check that finished owners close members even after runtime keys are lost."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-delivery-group-fallback-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["fallback@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "owner-finished"
        member_id = "member-stranded"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at, completed_at, pushed_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    owner_id,
                    "openclaw-weixin",
                    "fallback@im.wechat",
                    "",
                    "/ask",
                    "owner question",
                    hashlib.sha256(b"owner question").hexdigest(),
                    "fallback-owner",
                    "L1",
                    "pushed_to_wecom",
                    "owner final",
                    "pushed_to_wecom",
                    "backup1",
                    "thread-fallback",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    member_id,
                    "openclaw-weixin",
                    "fallback@im.wechat",
                    "",
                    "/ask",
                    "member supplement",
                    hashlib.sha256(b"member supplement").hexdigest(),
                    "fallback-member",
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-fallback",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.add_event(
            "local",
            "delivery_group_member",
            {
                "owner_task_id": owner_id,
                "owner_task_ids": [owner_id],
                "thread_id": "thread-fallback",
                "delivery_mode": "codex-app-server",
                "delivery_group_signature": "fallback-sig",
                "policy": "supplement_member_no_final_reply",
            },
            member_id,
        )
        queue.runtime_set(task_turn_key(member_id), "turn-member")
        queue.runtime_set(task_batch_key(member_id), "batch-member")
        queue.runtime_set(task_expected_ids_key(member_id), json.dumps([], ensure_ascii=False))

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        try:
            globals()["check_codex_health"] = lambda _config: {"ok": True, "healthy": True, "mode": "test"}
            globals()["poll_codex_result"] = lambda *_args, **_kwargs: {
                "ok": True,
                "healthy": True,
                "status": "inProgress",
                "newText": "",
                "terminal_without_text": False,
            }
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll

        member_after = queue.get_task(member_id) or {}
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (member_id,),
                ).fetchall()
            }
        ok = bool(
            recovery.get("recovered") == 1
            and member_after.get("status") == "done"
            and str(member_after.get("result") or "").startswith("[supplement] consumed by delivery group")
            and not member_after.get("push_status")
            and not queue.runtime_get(task_turn_key(member_id))
            and events.get("delivery_group_member_completed") == 1
            and events.get("delivery_group_member_completed_from_finished_owner") == 1
            and events.get("final_reply_spawned", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "member_status": member_after.get("status"),
            "member_result": member_after.get("result"),
            "member_push_status": member_after.get("push_status"),
            "events": events,
            "assertion": "delivery group member is closed from event-chain owner fallback without sending a duplicate final reply",
        }

def delivery_group_stale_active_snapshot_check() -> dict[str, Any]:
    """Temp-only check that a member closed earlier in the same recovery pass is not pushed."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-stale-active-group-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["stale-active@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_slot_release_after_seconds": 1,
            },
        }
        now_dt = datetime.now(timezone.utc)
        owner_time = (now_dt - timedelta(seconds=120)).isoformat()
        member_time = (now_dt - timedelta(seconds=60)).isoformat()
        owner_id = "owner-stale-snapshot"
        member_id = "member-stale-snapshot"
        thread_id = "thread-stale-snapshot"
        batch_id = "batch-stale-snapshot"
        turn_id = "turn-stale-snapshot"
        with queue.session() as db:
            for task_id, text, fingerprint, created_at in [
                (owner_id, "owner question", "stale-owner", owner_time),
                (member_id, "member supplement", "stale-member", member_time),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "stale-active@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        fingerprint,
                        "L1",
                        "sent_to_codex",
                        "",
                        "",
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        created_at,
                        created_at,
                        created_at,
                    ),
                )
        owner_task = queue.get_task(owner_id) or {}
        member_task = queue.get_task(member_id) or {}
        group = mark_delivery_group_members(queue, [owner_task], [member_task], thread_id, "codex-app-server")
        protocol = mobile_protocol(owner_id, batch_id)
        turn_event = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "client_message_id": batch_id,
            "expected_task_ids": [owner_id],
            "mobile_protocol": {"ack_code_saved": True, "result_code_saved": True},
            "mobile_protocols": {owner_id: protocol},
            "delivery_mode": "codex-app-server",
            "delivery_group": group,
        }
        queue.add_event("local", "codex_turn_started", turn_event, owner_id)
        queue.add_event("local", "codex_turn_started", turn_event, member_id)
        queue.runtime_set(task_turn_key(owner_id), turn_id)
        queue.runtime_set(task_batch_key(owner_id), batch_id)
        queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(owner_id), str(protocol.get("ack_code") or ""))
        queue.runtime_set(task_result_code_key(owner_id), str(protocol.get("result_code") or ""))
        queue.runtime_set(delivery_group_members_key(owner_id), json.dumps([member_id], ensure_ascii=False))
        queue.runtime_set(task_turn_key(member_id), turn_id)
        queue.runtime_set(task_batch_key(member_id), batch_id)
        queue.runtime_set(task_expected_ids_key(member_id), json.dumps([], ensure_ascii=False))

        push_calls: list[dict[str, Any]] = []
        poll_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]
        try:
            globals()["check_codex_health"] = lambda _config: {"ok": True, "healthy": True, "mode": "test"}

            def fake_poll_codex_result(
                _config: dict[str, Any],
                _thread_id: str,
                _turn_id: str,
                _baseline_key: str,
                client_message_id: str,
                expected_task_ids: list[str],
                expected_result_codes: dict[str, str] | None = None,
                expected_ack_codes: dict[str, str] | None = None,
            ) -> dict[str, Any]:
                poll_calls.append(
                    {
                        "client_message_id": client_message_id,
                        "expected_task_ids": list(expected_task_ids or []),
                        "expected_result_codes": dict(expected_result_codes or {}),
                        "expected_ack_codes": dict(expected_ack_codes or {}),
                    }
                )
                return {
                    "ok": True,
                    "healthy": True,
                    "mode": "test",
                    "status": "completed",
                    "newText": "owner final answer",
                    "terminal_without_text": False,
                    "ownership": {
                        "required": True,
                        "valid": True,
                        "matched_task_id": owner_id,
                        "expected_task_ids": [owner_id],
                        "result_complete": True,
                    },
                }

            def fake_push_final_reply_async(
                _queue: MobileQueue,
                task_arg: dict[str, Any],
                text_arg: str,
                _config_arg: dict[str, Any],
            ) -> dict[str, Any]:
                push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg})
                return {"ok": True, "mode": "test"}

            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        owner_after = queue.get_task(owner_id) or {}
        member_after = queue.get_task(member_id) or {}
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (member_id,),
                ).fetchall()
            }
        ok = bool(
            recovery.get("recovered") == 1
            and owner_after.get("status") == "done"
            and member_after.get("status") == "done"
            and str(member_after.get("result") or "").startswith("[supplement] consumed by delivery group")
            and not member_after.get("push_status")
            and push_calls == [{"task_id": owner_id, "text": "owner final answer"}]
            and len(poll_calls) == 1
            and not queue.runtime_get(task_turn_key(member_id))
            and events.get("delivery_group_member_completed") == 1
            and events.get("recovery_stale_active_snapshot_skipped") == 1
            and events.get("final_reply_spawned", 0) == 0
            and events.get("push_result", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "owner_status": owner_after.get("status"),
            "member_status": member_after.get("status"),
            "member_result": member_after.get("result"),
            "member_push_status": member_after.get("push_status"),
            "push_calls": push_calls,
            "poll_calls": poll_calls,
            "member_events": events,
            "assertion": "active recovery re-reads current task state so a member completed by its owner in the same cycle is never pushed as a duplicate final reply",
        }

def orphaned_supplement_promotion_with_push_evidence_check() -> dict[str, Any]:
    """Temp-only check that pushed base evidence promotes supplements even when result column is empty."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-orphan-supp-push-evidence-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        now = now_dt.isoformat()
        base_id = "base-pushed"
        first_id = "supp-first"
        second_id = "supp-second"
        thread_id = "thread-1"
        user = "user@im.wechat"
        rows = [
            (base_id, "pushed_to_wecom", "base prompt", old, old, "", "pushed_to_wecom"),
            (first_id, "pending", "first orphan supplement", old, old, "", ""),
            (second_id, "pending", "second orphan supplement", now, now, "", ""),
        ]
        with queue.session() as db:
            for task_id, status, text, created_at, updated_at, result, push_status in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        completed_at, pushed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        result,
                        push_status,
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        updated_at,
                        updated_at if status == "pushed_to_wecom" else None,
                        updated_at if push_status == "pushed_to_wecom" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
                "delivery_mode": "codex-app-server",
            },
            base_id,
        )
        queue.add_event(
            "wecom",
            "final_reply_visibility_unconfirmed",
            {"delivery_accepted": True, "phone_visible_confirmed": False},
            base_id,
        )
        queue.add_event(
            "wecom",
            "push_result",
            {"ok": True, "push_status": "pushed_to_wecom", "detail": "{}"},
            base_id,
        )
        items = [
            task_supplement_snapshot(queue.get_task(first_id) or {}, thread_id),
            task_supplement_snapshot(queue.get_task(second_id) or {}, thread_id),
        ]
        payload = {
            "base_message_id": base_id,
            "thread_id": thread_id,
            "active_task_id": base_id,
            "delivery_mode": "codex-app-server",
            "items": items,
            "published_at": old,
            "supplement_signature": "finished-base-supplements",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        promoted = promote_orphaned_bridge_supplements(queue, {"trigger": {"supplement_ack_grace_seconds": 10}}, thread_id)
        after_first = queue.get_task(first_id) or {}
        after_second = queue.get_task(second_id) or {}
        runtime_raw = queue.runtime_get(bridge_supplement_key(thread_id))
        runtime_payload = json.loads(runtime_raw) if runtime_raw else {}
        ok = bool(
            promoted.get("promoted_count") == 1
            and after_first.get("status") == "pending"
            and task_event_exists(queue, first_id, "supplement_promoted_to_owner")
            and runtime_payload.get("base_message_id") == first_id
            and bridge_supplement_task_ids(runtime_payload) == [second_id]
            and after_second.get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "promoted": promoted,
            "base_status": (queue.get_task(base_id) or {}).get("status"),
            "base_result": (queue.get_task(base_id) or {}).get("result"),
            "base_push_status": (queue.get_task(base_id) or {}).get("push_status"),
            "runtime_payload": runtime_payload,
            "assertion": "a base with pushed final-reply evidence but empty result still promotes the oldest pending supplement",
        }

def failed_base_supplement_owner_promotion_check() -> dict[str, Any]:
    """Temp-only check that a terminal failed base without result releases its oldest supplement as owner."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-failed-base-supp-promotion-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        now = now_dt.isoformat()
        base_id = "base-failed"
        first_id = "supp-first"
        second_id = "supp-second"
        thread_id = "thread-1"
        user = "user@im.wechat"
        rows = [
            (base_id, "failed", "base prompt", old, old, "", "", "protocol violation"),
            (first_id, "pending", "first failed-base supplement", old, old, "", "", ""),
            (second_id, "pending", "second failed-base supplement", now, now, "", "", ""),
        ]
        with queue.session() as db:
            for task_id, status, text, created_at, updated_at, result, push_status, error in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, error, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        completed_at, pushed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        result,
                        error,
                        push_status,
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        updated_at,
                        updated_at if terminal_failed_status(status) else None,
                        None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
                "delivery_mode": "codex-app-server",
            },
            base_id,
        )
        items = [
            task_supplement_snapshot(queue.get_task(first_id) or {}, thread_id),
            task_supplement_snapshot(queue.get_task(second_id) or {}, thread_id),
        ]
        payload = {
            "base_message_id": base_id,
            "thread_id": thread_id,
            "active_task_id": base_id,
            "delivery_mode": "codex-app-server",
            "items": items,
            "published_at": old,
            "supplement_signature": "failed-base-supplements",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        promoted = promote_orphaned_bridge_supplements(queue, {"trigger": {"supplement_ack_grace_seconds": 10}}, thread_id)
        after_first = queue.get_task(first_id) or {}
        after_second = queue.get_task(second_id) or {}
        runtime_raw = queue.runtime_get(bridge_supplement_key(thread_id))
        runtime_payload = json.loads(runtime_raw) if runtime_raw else {}
        ok = bool(
            promoted.get("promoted_count") == 1
            and promoted.get("resumed_count") == 1
            and (promoted.get("promoted") or [{}])[0].get("base_terminal_failed") is True
            and after_first.get("status") == "pending"
            and task_event_exists(queue, first_id, "supplement_promoted_to_owner")
            and task_event_exists(queue, first_id, "supplement_owner_reschedule_requested")
            and runtime_payload.get("base_message_id") == first_id
            and bridge_supplement_task_ids(runtime_payload) == [second_id]
            and after_second.get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "promoted": promoted,
            "base_status": (queue.get_task(base_id) or {}).get("status"),
            "runtime_payload": runtime_payload,
            "assertion": "a terminal failed base without recoverable result releases the oldest pending supplement as the next owner and rebases later supplements",
        }

def completed_owner_supplement_ack_window_check() -> dict[str, Any]:
    """Temp-only check that promotion waits from owner completion, not supplement publish time."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-completed-owner-supp-window-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        just_completed = (now_dt - timedelta(seconds=2)).isoformat()
        base_id = "base-just-completed"
        supplement_id = "supp-available-after-completion"
        thread_id = "thread-1"
        user = "backup1-window@im.wechat"
        with queue.session() as db:
            for task_id, status, text, result, created_at, updated_at, completed_at in [
                (base_id, "done", "base prompt", "owner final", old, just_completed, just_completed),
                (supplement_id, "pending", "late supplement", "", old, old, None),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        completed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        result,
                        "",
                        "backup1",
                        thread_id,
                        "{}",
                        created_at,
                        updated_at,
                        completed_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": "turn-base",
                "client_message_id": "batch-base",
                "expected_task_ids": [base_id],
                "delivery_mode": "codex-app-server",
            },
            base_id,
        )
        payload = {
            "base_message_id": base_id,
            "thread_id": thread_id,
            "active_task_id": base_id,
            "delivery_mode": "codex-app-server",
            "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, thread_id)],
            "published_at": old,
            "supplement_signature": "completed-owner-window",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(payload, ensure_ascii=False))
        config = {"trigger": {"supplement_ack_grace_seconds": 10}}
        promoted = promote_orphaned_bridge_supplements(queue, config, thread_id)
        runtime_payload = json.loads(str(queue.runtime_get(bridge_supplement_key(thread_id)) or "{}"))
        supplement_after = queue.get_task(supplement_id) or {}
        ok = bool(
            promoted.get("promoted_count") == 0
            and promoted.get("preserved")
            and runtime_payload.get("base_message_id") == base_id
            and bridge_supplement_task_ids(runtime_payload) == [supplement_id]
            and supplement_after.get("status") == "pending"
            and not task_event_exists(queue, supplement_id, "supplement_promoted_to_owner")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "promoted": promoted,
            "runtime_payload": runtime_payload,
            "supplement_status": supplement_after.get("status"),
            "assertion": "unacked supplements are not promoted immediately when the owner just completed; the post-completion MCP pickup window starts at owner completion",
        }

def supplement_mcp_disconnect_no_primary_fallback_check() -> dict[str, Any]:
    """Temp-only check that fresh MCP-unacked supplements do not become primary dispatches."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-mcp-down-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        supplement_id = "supplement-task"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    supplement_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "supplement while MCP is down",
                    hashlib.sha256(b"supplement while MCP is down").hexdigest(),
                    "supp-mcp-down",
                    "L1",
                    "pending",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                ),
            )
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {
                "active_task_id": "old-active",
                "thread_id": "thread-1",
                "delivery_mode": "codex-app-server",
                "signature": "sig-mcp-down",
            },
            supplement_id,
        )
        queue.runtime_set(
            bridge_supplement_key("thread-1"),
            json.dumps(
                {
                    "base_message_id": "old-active",
                    "thread_id": "thread-1",
                    "active_task_id": "old-active",
                    "delivery_mode": "codex-app-server",
                    "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, "thread-1")],
                    "published_at": now,
                    "supplement_signature": "sig-mcp-down",
                },
                ensure_ascii=False,
            ),
        )
        config = {
            "openclaw": {
                "account_id": "backup1",
                "phone_status_ack_events": [],
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["user@im.wechat"]},
            "accounts": {"users": {"user@im.wechat": {"account_id": "backup1"}}},
            "openclaw_accounts": {"backup1": {"userId": "user@im.wechat", "token": "present"}},
            "permissions": {
                "users": {"user@im.wechat": {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "auto_reply": False,
                "continuation_window_seconds": 60,
            },
            "threads": {
                "default_id": "test-thread",
                "items": [{"id": "test-thread", "name": "Test Thread", "thread_id": "thread-1"}],
            },
        }
        set_active_thread(queue, "user@im.wechat", "test-thread")
        dispatched: list[list[str]] = []

        original_dispatch = globals()["dispatch_to_codex"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_recover_active = globals()["recover_active_codex_tasks"]
        original_recover_reply_sending = globals()["recover_stale_reply_sending_tasks"]
        original_onboarding_sync = globals()["maybe_sync_openclaw_account_onboarding"]
        original_account_sync = globals()["sync_openclaw_accounts_to_bridge_users"]
        original_reply_reconcile = globals()["reconcile_completed_replies_waiting_push"]
        original_process_mcp = globals()["process_mcp_acked_pending_supplements"]
        original_pending_reply_retries = globals()["process_pending_reply_context_retries"]
        original_queued_release = globals()["release_queued_tasks_for_active_owner_supplement"]
        original_orphan_promotion = globals()["promote_orphaned_bridge_supplements"]
        original_invalid_release = globals()["release_invalid_published_supplements"]
        original_pending_visible_recovery = globals()["recover_pending_visible_cdp_unconfirmed_results"]
        original_publish_attachment = globals()["publish_attachment_active_supplements"]
        original_send_status_ack = globals()["send_status_ack"]

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            dispatched.append([str(task.get("id") or "") for task in tasks])
            return {"ok": True, "thread_id": thread_id, "turn_id": "turn-should-not-start"}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "thread_name": thread_name,
                "listed": True,
                "listed_status": {"type": "idle"},
                "stabilize_name": stabilize_name,
            }

        try:
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["recover_active_codex_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "action": "no_active_tasks",
                "recovered": 0,
                "reverted": 0,
            }
            globals()["recover_stale_reply_sending_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["maybe_sync_openclaw_account_onboarding"] = lambda *_args, **_kwargs: {
                "ok": True,
                "updated": 0,
            }
            globals()["sync_openclaw_accounts_to_bridge_users"] = lambda *_args, **_kwargs: {
                "ok": True,
                "synced": 0,
            }
            globals()["reconcile_completed_replies_waiting_push"] = lambda *_args, **_kwargs: {
                "ok": True,
                "reconciled": 0,
            }
            globals()["process_pending_reply_context_retries"] = lambda *_args, **_kwargs: {
                "ok": True,
                "scheduled": 0,
            }
            globals()["release_queued_tasks_for_active_owner_supplement"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
            }
            globals()["promote_orphaned_bridge_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "promoted": [],
                "promoted_count": 0,
                "resumed": [],
                "resumed_count": 0,
            }
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["publish_attachment_active_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "published": [],
                "duplicates": [],
                "failed": [],
                "suppressed": [],
            }
            globals()["send_status_ack"] = lambda *_args, **_kwargs: {"ok": True, "mode": "test"}
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["recover_active_codex_tasks"] = original_recover_active
            globals()["recover_stale_reply_sending_tasks"] = original_recover_reply_sending
            globals()["maybe_sync_openclaw_account_onboarding"] = original_onboarding_sync
            globals()["sync_openclaw_accounts_to_bridge_users"] = original_account_sync
            globals()["reconcile_completed_replies_waiting_push"] = original_reply_reconcile
            globals()["process_pending_reply_context_retries"] = original_pending_reply_retries
            globals()["release_queued_tasks_for_active_owner_supplement"] = original_queued_release
            globals()["promote_orphaned_bridge_supplements"] = original_orphan_promotion
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = original_pending_visible_recovery
            globals()["publish_attachment_active_supplements"] = original_publish_attachment
            globals()["send_status_ack"] = original_send_status_ack

        task_after = queue.get_task(supplement_id) or {}
        bridge_payload = queue.runtime_get(bridge_supplement_key("thread-1"))
        with queue.session() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='published_supplement_primary_dispatch_suppressed'
                """,
                (supplement_id,),
            ).fetchone()
        suppressed_event_count = int(row["n"] if row else 0)
        ok = bool(
            result.get("action") == "idle_no_dispatchable_thread"
            and result.get("skipped_published_supplement") == 1
            and not dispatched
            and task_after.get("status") == "pending"
            and bool(bridge_payload)
            and suppressed_event_count == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "dispatched": dispatched,
            "task_status": task_after.get("status"),
            "bridge_supplement_present": bool(bridge_payload),
            "suppressed_event_count": suppressed_event_count,
            "assertion": "fresh MCP-unacked published supplements stay pending during the ack grace window and never become duplicate primary Codex dispatches",
        }

def supplement_cli_fallback_check() -> dict[str, Any]:
    """Temp-only check that local stdio fallback can read and ack supplements."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-cli-fallback-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        thread_id = "thread-fallback"
        owner_id = "owner-fallback"
        supplement_id = "supplement-fallback"
        now = datetime.now(timezone.utc).isoformat()
        for tid, status, text in [
            (owner_id, "sent_to_codex", "owner task"),
            (supplement_id, "pending", "fallback supplement text"),
        ]:
            with queue.session() as db:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        thread_id,
                        "{}",
                        now,
                        now,
                        now if status == "sent_to_codex" else None,
                        now if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event("local", "codex_turn_started", {"thread_id": thread_id, "turn_id": "turn-fallback"}, owner_id)
        queue.runtime_set(task_turn_key(owner_id), "turn-fallback")
        queue.runtime_set(task_batch_key(owner_id), "batch-fallback")
        queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        queue.add_event(
            "local",
            "pending_backlog_supplement_pending_published",
            {
                "owner_task_id": owner_id,
                "thread_id": thread_id,
                "signature": "fallback-signature",
            },
            supplement_id,
        )
        queue.runtime_set(
            bridge_supplement_key(thread_id),
            json.dumps(
                {
                    "base_message_id": owner_id,
                    "active_task_id": owner_id,
                    "thread_id": thread_id,
                    "delivery_mode": "codex-app-server",
                    "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, thread_id)],
                    "published_at": now,
                    "supplement_signature": "fallback-signature",
                    "supplement_source": "pending_backlog",
                },
                ensure_ascii=False,
            ),
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "mcp": {
                "mobile_openclaw_command": sys.executable,
                "mobile_openclaw_script": str(ROOT / "mobile_bridge_mcp_server.py"),
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "auto_reply": False,
                "supplement_ack_grace_seconds": 10,
            },
        }
        config_path = temp / "config.local.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        config["_config_path"] = str(config_path)
        get_result = supplement_fallback_get_pending_batch(config, thread_id, timeout_seconds=8)
        tool_result = get_result.get("tool_result") if isinstance(get_result.get("tool_result"), dict) else {}
        items = tool_result.get("items") if isinstance(tool_result.get("items"), list) else []
        ack_result = supplement_fallback_ack_message(config, thread_id, supplement_id, timeout_seconds=8)
        ack_payload = mcp_ack_payload(queue, supplement_id)
        with queue.session() as db:
            ack_events = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_message_acked'
                """,
                (supplement_id,),
            ).fetchone()
        ok = bool(
            get_result.get("ok")
            and len(items) == 1
            and str(items[0].get("message_id") or "") == supplement_id
            and ack_result.get("ok")
            and bool((ack_result.get("tool_result") or {}).get("acked"))
            and ack_payload.get("base_task_id") == owner_id
            and int(ack_events["n"] if ack_events else 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "get_result": get_result,
            "ack_result": ack_result,
            "ack_payload": ack_payload,
            "ack_event_count": int(ack_events["n"] if ack_events else 0),
            "assertion": "local stdio fallback uses the real MCP server path to read supplements and write the same durable mcp_message_acked evidence",
        }

def supplement_unacked_timeout_release_check() -> dict[str, Any]:
    """Temp-only check that active-owner supplements survive MCP ack delay."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-ack-timeout-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        task_id = "timeout-supplement"
        base_id = "active-owner"
        with queue.session() as db:
            for tid, status, text in [
                (base_id, "sent_to_codex", "active owner"),
                (task_id, "pending", "supplement should recover"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        "thread-1",
                        "{}",
                        old,
                        old,
                        old if status == "sent_to_codex" else None,
                        old if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {"thread_id": "thread-1", "turn_id": "turn-owner"},
            base_id,
        )
        queue.runtime_set(task_turn_key(base_id), "turn-owner")
        queue.runtime_set(task_batch_key(base_id), "batch-owner")
        queue.runtime_set(task_expected_ids_key(base_id), json.dumps([base_id], ensure_ascii=False))
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {"active_task_id": base_id, "thread_id": "thread-1", "signature": "timeout"},
            task_id,
        )
        queue.runtime_set(
            bridge_supplement_key("thread-1"),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": "thread-1",
                    "items": [task_supplement_snapshot(queue.get_task(task_id) or {}, "thread-1")],
                    "published_at": old,
                    "supplement_signature": "timeout",
                },
                ensure_ascii=False,
            ),
        )
        config = {
            "trigger": {"supplement_ack_grace_seconds": 30},
            "queue": {"db_path": str(temp / "queue.db")},
        }
        pending = queue.list_pending(10)
        before_context = task_is_supplement_context(queue, task_id)
        before_published = pending_task_is_published_bridge_supplement(queue, task_id, "thread-1")
        released = release_invalid_published_supplements(queue, pending, config)
        still_published = pending_task_is_published_bridge_supplement(queue, task_id, "thread-1")
        after_context = task_is_supplement_context(queue, task_id)
        with queue.session() as db:
            extended_ev = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='published_supplement_ack_wait_extended'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            before_published
            and released.get("released_count") == 0
            and still_published
            and after_context
            and int(extended_ev["n"] if extended_ev else 0) == 1
            and queue.get_task(task_id).get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "before_context": before_context,
            "before_published": before_published,
            "after_context": after_context,
            "released": released,
            "still_published": still_published,
            "extended_event_count": int(extended_ev["n"] if extended_ev else 0),
            "task_status": queue.get_task(task_id).get("status"),
            "assertion": "MCP-unacked supplements remain published while their final-reply owner is still active",
        }

def supplement_release_no_republish_check() -> dict[str, Any]:
    """Temp-only check that an active-owner supplement is retained and not duplicated."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-retain-dedupe-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=120)).isoformat()
        task_id = "retained-supplement"
        base_id = "active-owner"
        with queue.session() as db:
            for tid, status, text in [
                (base_id, "sent_to_codex", "active owner"),
                (task_id, "pending", "must not republish to same owner"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        "thread-1",
                        "{}",
                        old,
                        old,
                        old if status == "sent_to_codex" else None,
                        old if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {"thread_id": "thread-1", "turn_id": "turn-owner"},
            base_id,
        )
        queue.runtime_set(task_turn_key(base_id), "turn-owner")
        queue.runtime_set(task_batch_key(base_id), "batch-owner")
        queue.runtime_set(task_expected_ids_key(base_id), json.dumps([base_id], ensure_ascii=False))
        queue.runtime_set(
            attachment_supplement_signature_key(base_id),
            "timeout",
        )
        queue.runtime_set(
            bridge_supplement_key("thread-1"),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": "thread-1",
                    "items": [task_supplement_snapshot(queue.get_task(task_id) or {}, "thread-1")],
                    "published_at": old,
                    "supplement_signature": "timeout",
                },
                ensure_ascii=False,
            ),
        )
        config = {
            "openclaw": {"account_id": "backup1", "phone_status_ack_events": []},
            "trigger": {"delivery_mode": "codex-app-server", "supplement_ack_grace_seconds": 30},
            "queue": {"db_path": str(temp / "queue.db")},
        }
        pending = queue.list_pending(10)
        preserved = release_invalid_published_supplements(queue, pending, config)
        signature_after_preserve = str(queue.runtime_get(attachment_supplement_signature_key(base_id)) or "")
        published_after_preserve = publish_attachment_active_supplements(queue, config, queue.list_pending(10))
        bridge_payload_after_publish = queue.runtime_get(bridge_supplement_key("thread-1"))
        with queue.session() as db:
            republished = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='attachment_supplement_pending_published'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            preserved.get("released_count") == 0
            and signature_after_preserve == "timeout"
            and bridge_payload_after_publish
            and len(published_after_preserve.get("published") or []) == 0
            and len(published_after_preserve.get("duplicates") or []) == 0
            and len(published_after_preserve.get("suppressed") or []) == 0
            and int(republished["n"] if republished else 0) == 0
            and queue.get_task(task_id).get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "preserved": preserved,
            "signature_after_preserve": signature_after_preserve,
            "published_after_preserve": published_after_preserve,
            "bridge_supplement_present": bool(bridge_payload_after_publish),
            "republished_event_count": int(republished["n"] if republished else 0),
            "task_status": queue.get_task(task_id).get("status"),
            "assertion": "while the owner is still active, an MCP-unacked supplement remains published and duplicate publish attempts are skipped by payload identity",
        }

def active_runtime_rehydrate_check() -> dict[str, Any]:
    """Temp-only check that active turn runtime can be restored from codex_turn_started."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-rehydrate-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "active-missing-runtime"
        turn_id = "turn-active-rehydrate"
        client_message_id = "mobile-openclaw-rehydrate-batch"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "active missing runtime",
                    hashlib.sha256(b"active missing runtime").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "client_message_id": client_message_id,
                "expected_task_ids": [task_id],
                "mobile_protocols": {
                    task_id: {
                        "task_id": task_id,
                        "ack_code": "event-ack-code",
                        "result_code": "event-result-code",
                    }
                },
                "delivery_mode": "codex-app-server",
            },
            task_id,
        )
        before_turn = str(queue.runtime_get(task_turn_key(task_id)) or "")
        result = rehydrate_codex_turn_runtime_from_event(queue, task_id)
        after_turn = str(queue.runtime_get(task_turn_key(task_id)) or "")
        batch_id, expected_task_ids = task_batch_runtime(queue, task_id, [task_id])
        ack_codes = task_ack_code_runtime(queue, expected_task_ids)
        result_codes = task_result_code_runtime(queue, expected_task_ids)
        with queue.session() as db:
            ev = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='codex_turn_runtime_rehydrated'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            result.get("ok")
            and not before_turn
            and after_turn == turn_id
            and batch_id == client_message_id
            and expected_task_ids == [task_id]
            and ack_codes.get(task_id) == "event-ack-code"
            and result_codes.get(task_id) == "event-result-code"
            and int(ev["n"] if ev else 0) == 1
            and queue.get_task(task_id).get("status") == "sent_to_codex"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "before_turn": before_turn,
            "after_turn": after_turn,
            "batch_id": batch_id,
            "expected_task_ids": expected_task_ids,
            "ack_code": ack_codes.get(task_id),
            "result_code": result_codes.get(task_id),
            "result_codes_present": bool(result_codes.get(task_id)),
            "event_count": int(ev["n"] if ev else 0),
            "task_status": queue.get_task(task_id).get("status"),
            "assertion": "missing volatile runtime is restored from durable codex_turn_started without reverting active tasks",
        }

def queued_turn_rehydrate_check() -> dict[str, Any]:
    """Temp-only check that stale queued tasks with a started turn are observed, not redelivered."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-queued-rehydrate-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now_dt = datetime.now(timezone.utc)
        old = (now_dt - timedelta(seconds=300)).isoformat()
        task_id = "queued-started-turn"
        turn_id = "turn-queued-rehydrate"
        client_message_id = "mobile-openclaw-queued-rehydrate-batch"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "queued but already started",
                    hashlib.sha256(b"queued but already started").hexdigest(),
                    task_id,
                    "L1",
                    "queued_for_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    old,
                    old,
                    old,
                    None,
                ),
            )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-1",
                "turn_id": turn_id,
                "client_message_id": client_message_id,
                "expected_task_ids": [task_id],
                "mobile_protocols": {
                    task_id: {
                        "task_id": task_id,
                        "ack_code": "queued-ack-code",
                        "result_code": "queued-result-code",
                    }
                },
                "delivery_mode": "codex-app-server",
            },
            task_id,
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["user@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "queued_recovery_after_seconds": 30,
                "active_recovery_max_sent_checks_per_cycle": 10,
            },
        }
        recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        task_after = queue.get_task(task_id) or {}
        batch_id, expected_task_ids = task_batch_runtime(queue, task_id, [task_id])
        ack_codes = task_ack_code_runtime(queue, expected_task_ids)
        result_codes = task_result_code_runtime(queue, expected_task_ids)
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                ).fetchall()
            }
        ok = bool(
            recovery.get("queued_rehydrated") == 1
            and recovery.get("reverted") == 0
            and task_after.get("status") == "sent_to_codex"
            and task_after.get("sent_to_codex_at")
            and queue.runtime_get(task_turn_key(task_id)) == turn_id
            and batch_id == client_message_id
            and expected_task_ids == [task_id]
            and ack_codes.get(task_id) == "queued-ack-code"
            and result_codes.get(task_id) == "queued-result-code"
            and events.get("recovery_queued_rehydrated_to_sent") == 1
            and events.get("recovery_queued_reverted_to_pending", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "sent_to_codex_at": task_after.get("sent_to_codex_at"),
            "turn_id": queue.runtime_get(task_turn_key(task_id)),
            "batch_id": batch_id,
            "expected_task_ids": expected_task_ids,
            "ack_code": ack_codes.get(task_id),
            "result_code": result_codes.get(task_id),
            "events": events,
            "assertion": "queued_for_codex tasks with durable started-turn evidence are rehydrated into observation instead of redelivered",
        }

def supplement_non_owner_host_check() -> dict[str, Any]:
    """Temp-only check that new supplements never attach to a non-owner active task."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-supp-non-owner-host-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "owner-task"
        member_id = "member-active"
        new_id = "new-message"
        rows = [
            (owner_id, "done", "original owner", now, now),
            (member_id, "sent_to_codex", "member still active", now, now),
            (new_id, "pending", "new user message", now, now),
        ]
        with queue.session() as db:
            for task_id, status, text, created_at, updated_at in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        "thread-1",
                        "{}",
                        created_at,
                        updated_at,
                        created_at if status == "sent_to_codex" else None,
                        updated_at if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event(
            "local",
            "delivery_group_member",
            {
                "owner_task_id": owner_id,
                "owner_task_ids": [owner_id],
                "thread_id": "thread-1",
                "delivery_mode": "codex-app-server",
                "policy": "supplement_member_no_final_reply",
            },
            member_id,
        )
        queue.runtime_set(task_turn_key(member_id), "turn-member")
        queue.runtime_set(task_batch_key(member_id), "batch-member")
        queue.runtime_set(task_expected_ids_key(member_id), json.dumps([], ensure_ascii=False))
        config = {
            "openclaw": {"account_id": "backup1", "phone_status_ack_events": []},
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["user@im.wechat"]},
            "accounts": {"users": {"user@im.wechat": {"account_id": "backup1"}}},
            "openclaw_accounts": {"backup1": {"userId": "user@im.wechat", "token": "present"}},
            "permissions": {
                "users": {"user@im.wechat": {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "auto_reply": False,
                "active_recovery_max_sent_checks_per_cycle": 0,
                "active_slot_release_after_seconds": 3600,
            },
            "threads": {
                "default_id": "test-thread",
                "items": [{"id": "test-thread", "name": "Test Thread", "thread_id": "thread-1"}],
            },
        }
        set_active_thread(queue, "user@im.wechat", "test-thread")

        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_recover_active = globals()["recover_active_codex_tasks"]
        original_recover_reply_sending = globals()["recover_stale_reply_sending_tasks"]
        original_onboarding_sync = globals()["maybe_sync_openclaw_account_onboarding"]
        original_account_sync = globals()["sync_openclaw_accounts_to_bridge_users"]
        original_reply_reconcile = globals()["reconcile_completed_replies_waiting_push"]
        original_process_mcp = globals()["process_mcp_acked_pending_supplements"]
        original_pending_reply_retries = globals()["process_pending_reply_context_retries"]
        original_queued_release = globals()["release_queued_tasks_for_active_owner_supplement"]
        original_orphan_promotion = globals()["promote_orphaned_bridge_supplements"]
        original_invalid_release = globals()["release_invalid_published_supplements"]
        original_pending_visible_recovery = globals()["recover_pending_visible_cdp_unconfirmed_results"]
        original_publish_attachment = globals()["publish_attachment_active_supplements"]
        original_send_status_ack = globals()["send_status_ack"]

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "thread_name": thread_name,
                "listed": True,
                "listed_status": {"type": "idle"},
                "stabilize_name": stabilize_name,
            }

        try:
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["recover_active_codex_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "action": "no_active_tasks",
                "recovered": 0,
                "reverted": 0,
            }
            globals()["recover_stale_reply_sending_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["maybe_sync_openclaw_account_onboarding"] = lambda *_args, **_kwargs: {
                "ok": True,
                "updated": 0,
            }
            globals()["sync_openclaw_accounts_to_bridge_users"] = lambda *_args, **_kwargs: {
                "ok": True,
                "synced": 0,
            }
            globals()["reconcile_completed_replies_waiting_push"] = lambda *_args, **_kwargs: {
                "ok": True,
                "reconciled": 0,
            }
            globals()["process_mcp_acked_pending_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "completed": [],
                "completed_count": 0,
            }
            globals()["process_pending_reply_context_retries"] = lambda *_args, **_kwargs: {
                "ok": True,
                "scheduled": 0,
            }
            globals()["release_queued_tasks_for_active_owner_supplement"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
            }
            globals()["promote_orphaned_bridge_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "promoted": [],
                "promoted_count": 0,
                "resumed": [],
                "resumed_count": 0,
            }
            globals()["release_invalid_published_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
                "preserved": [],
                "preserved_count": 0,
            }
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["publish_attachment_active_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "published": [],
                "duplicates": [],
                "failed": [],
                "suppressed": [],
            }
            globals()["send_status_ack"] = lambda *_args, **_kwargs: {"ok": True, "mode": "test"}
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["recover_active_codex_tasks"] = original_recover_active
            globals()["recover_stale_reply_sending_tasks"] = original_recover_reply_sending
            globals()["maybe_sync_openclaw_account_onboarding"] = original_onboarding_sync
            globals()["sync_openclaw_accounts_to_bridge_users"] = original_account_sync
            globals()["reconcile_completed_replies_waiting_push"] = original_reply_reconcile
            globals()["process_mcp_acked_pending_supplements"] = original_process_mcp
            globals()["process_pending_reply_context_retries"] = original_pending_reply_retries
            globals()["release_queued_tasks_for_active_owner_supplement"] = original_queued_release
            globals()["promote_orphaned_bridge_supplements"] = original_orphan_promotion
            globals()["release_invalid_published_supplements"] = original_invalid_release
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = original_pending_visible_recovery
            globals()["publish_attachment_active_supplements"] = original_publish_attachment
            globals()["send_status_ack"] = original_send_status_ack

        with queue.session() as db:
            published = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='attachment_supplement_pending_published'
                """,
                (new_id,),
            ).fetchone()
            invalid_host = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='thread_delivery_route_busy_invalid_supplement_host'
                """,
                (new_id,),
            ).fetchone()
            ack = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='status_ack_continuation_deferred'
                """,
                (new_id,),
            ).fetchone()
        ok = bool(
            result.get("action") == "idle_no_dispatchable_thread"
            and int(published["n"] if published else 0) == 0
            and int(invalid_host["n"] if invalid_host else 0) == 1
            and int(ack["n"] if ack else 0) == 0
            and queue.get_task(new_id).get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "published_count": int(published["n"] if published else 0),
            "invalid_host_count": int(invalid_host["n"] if invalid_host else 0),
            "continuation_ack_count": int(ack["n"] if ack else 0),
            "task_status": queue.get_task(new_id).get("status"),
            "assertion": "pending messages must not be attached as supplements to active tasks that are delivery-group members, not final-reply owners",
        }

def queued_same_route_supplement_recovery_check() -> dict[str, Any]:
    """Temp-only check that half-queued same-route messages become supplements."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-queued-supp-recovery-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "active-owner"
        queued_id = "half-queued-supplement"
        with queue.session() as db:
            for tid, status, text, queued_at, sent_at in [
                (owner_id, "sent_to_codex", "active owner", now, now),
                (queued_id, "queued_for_codex", "queued supplement", now, None),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        "thread-1",
                        "{}",
                        now,
                        now,
                        queued_at,
                        sent_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {"thread_id": "thread-1", "turn_id": "turn-owner"},
            owner_id,
        )
        queue.runtime_set(task_turn_key(owner_id), "turn-owner")
        queue.runtime_set(task_batch_key(owner_id), "batch-owner")
        queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        config = {
            "openclaw": {"account_id": "backup1", "phone_status_ack_events": []},
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["user@im.wechat"]},
            "accounts": {"users": {"user@im.wechat": {"account_id": "backup1"}}},
            "openclaw_accounts": {"backup1": {"userId": "user@im.wechat", "token": "present"}},
            "permissions": {
                "users": {"user@im.wechat": {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 0,
                "active_slot_release_after_seconds": 3600,
            },
            "threads": {
                "default_id": "test-thread",
                "items": [{"id": "test-thread", "name": "Test Thread", "thread_id": "thread-1"}],
            },
        }
        set_active_thread(queue, "user@im.wechat", "test-thread")
        original_recover_active = globals()["recover_active_codex_tasks"]
        original_recover_reply_sending = globals()["recover_stale_reply_sending_tasks"]
        original_onboarding_sync = globals()["maybe_sync_openclaw_account_onboarding"]
        original_account_sync = globals()["sync_openclaw_accounts_to_bridge_users"]
        original_reply_reconcile = globals()["reconcile_completed_replies_waiting_push"]
        original_process_mcp = globals()["process_mcp_acked_pending_supplements"]
        original_pending_reply_retries = globals()["process_pending_reply_context_retries"]
        original_orphan_promotion = globals()["promote_orphaned_bridge_supplements"]
        original_invalid_release = globals()["release_invalid_published_supplements"]
        original_pending_visible_recovery = globals()["recover_pending_visible_cdp_unconfirmed_results"]
        original_send_status_ack = globals()["send_status_ack"]
        try:
            globals()["recover_active_codex_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "action": "no_active_tasks",
                "recovered": 0,
                "reverted": 0,
            }
            globals()["recover_stale_reply_sending_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["maybe_sync_openclaw_account_onboarding"] = lambda *_args, **_kwargs: {
                "ok": True,
                "updated": 0,
            }
            globals()["sync_openclaw_accounts_to_bridge_users"] = lambda *_args, **_kwargs: {
                "ok": True,
                "synced": 0,
            }
            globals()["reconcile_completed_replies_waiting_push"] = lambda *_args, **_kwargs: {
                "ok": True,
                "reconciled": 0,
            }
            globals()["process_mcp_acked_pending_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "completed": [],
                "completed_count": 0,
            }
            globals()["process_pending_reply_context_retries"] = lambda *_args, **_kwargs: {
                "ok": True,
                "scheduled": 0,
            }
            globals()["promote_orphaned_bridge_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "promoted": [],
                "promoted_count": 0,
                "resumed": [],
                "resumed_count": 0,
            }
            globals()["release_invalid_published_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
                "preserved": [],
                "preserved_count": 0,
            }
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["send_status_ack"] = lambda *_args, **_kwargs: {"ok": True, "mode": "test"}
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["recover_active_codex_tasks"] = original_recover_active
            globals()["recover_stale_reply_sending_tasks"] = original_recover_reply_sending
            globals()["maybe_sync_openclaw_account_onboarding"] = original_onboarding_sync
            globals()["sync_openclaw_accounts_to_bridge_users"] = original_account_sync
            globals()["reconcile_completed_replies_waiting_push"] = original_reply_reconcile
            globals()["process_mcp_acked_pending_supplements"] = original_process_mcp
            globals()["process_pending_reply_context_retries"] = original_pending_reply_retries
            globals()["promote_orphaned_bridge_supplements"] = original_orphan_promotion
            globals()["release_invalid_published_supplements"] = original_invalid_release
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = original_pending_visible_recovery
            globals()["send_status_ack"] = original_send_status_ack
        task_after = queue.get_task(queued_id) or {}
        bridge_payload = str(queue.runtime_get(bridge_supplement_key("thread-1")) or "")
        with queue.session() as db:
            released = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='queued_same_route_released_for_supplement'
                """,
                (queued_id,),
            ).fetchone()
            published = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='attachment_supplement_pending_published'
                """,
                (queued_id,),
            ).fetchone()
        ok = bool(
            result.get("action") in {"attachment_supplement_idle", "idle_no_dispatchable_thread"}
            and task_after.get("status") == "pending"
            and not task_after.get("queued_for_codex_at")
            and queued_id in bridge_payload
            and int(released["n"] if released else 0) == 1
            and int(published["n"] if published else 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "task_status": task_after.get("status"),
            "queued_for_codex_at": task_after.get("queued_for_codex_at"),
            "bridge_supplement_present": bool(bridge_payload),
            "release_event_count": int(released["n"] if released else 0),
            "published_event_count": int(published["n"] if published else 0),
            "assertion": "half-queued same-route messages with no turn runtime are recovered into the active owner's supplement payload",
        }

def supplement_invalid_published_release_check() -> dict[str, Any]:
    """Temp-only check that invalid-host supplements keep supplement identity."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-invalid-supp-release-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "stale-supplement"
        base_id = "finished-member"
        with queue.session() as db:
            for tid, status, text in [
                (base_id, "done", "finished non-owner"),
                (task_id, "pending", "new message that should recover"),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        status,
                        "",
                        "",
                        "backup1",
                        "thread-1",
                        "{}",
                        now,
                        now,
                    ),
                )
        queue.add_event(
            "local",
            "attachment_supplement_pending_published",
            {"active_task_id": base_id, "thread_id": "thread-1", "signature": "stale"},
            task_id,
        )
        queue.runtime_set(
            bridge_supplement_key("thread-1"),
            json.dumps(
                {
                    "base_message_id": base_id,
                    "active_task_id": base_id,
                    "thread_id": "thread-1",
                    "items": [task_supplement_snapshot(queue.get_task(task_id) or {}, "thread-1")],
                    "supplement_signature": "stale",
                },
                ensure_ascii=False,
            ),
        )
        pending = queue.list_pending(10)
        released = release_invalid_published_supplements(queue, pending, {})
        still_published = pending_task_is_published_bridge_supplement(queue, task_id, "thread-1")
        still_context = task_is_supplement_context(queue, task_id)
        with queue.session() as db:
            ev = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='published_supplement_invalid_host_preserved'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            released.get("released_count") == 0
            and released.get("preserved_count") == 1
            and still_published
            and still_context
            and int(ev["n"] if ev else 0) == 1
            and queue.get_task(task_id).get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "released": released,
            "still_published": still_published,
            "still_context": still_context,
            "release_event_count": int(ev["n"] if ev else 0),
            "task_status": queue.get_task(task_id).get("status"),
            "assertion": "invalid-host MCP supplement bindings stay pending supplement-context and cannot fall back to normal dispatch",
        }

def mcp_ack_does_not_complete_owner_check() -> dict[str, Any]:
    """Temp-only check that an MCP ack cannot close a current final-reply owner."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-ack-owner-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "owner-task"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "owner should stay active",
                    hashlib.sha256(b"owner should stay active").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), "turn-owner")
        queue.runtime_set(task_batch_key(task_id), "batch-owner")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(mcp_ack_key(task_id), json.dumps({"thread_id": "thread-1", "acked_at": now}, ensure_ascii=False))
        completed = process_mcp_acked_pending_supplements(queue)
        completed_again = process_mcp_acked_pending_supplements(queue)
        task_after = queue.get_task(task_id) or {}
        with queue.session() as db:
            ignored = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_ack_ignored_missing_base_owner'
                """,
                (task_id,),
            ).fetchone()
            quarantined = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_ack_invalid_quarantined'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            completed.get("completed_count") == 0
            and completed_again.get("completed_count") == 0
            and task_after.get("status") == "sent_to_codex"
            and int(ignored["n"] if ignored else 0) == 1
            and int(quarantined["n"] if quarantined else 0) == 1
            and not queue.runtime_get(mcp_ack_key(task_id))
            and bool(queue.runtime_get(invalid_mcp_ack_key(task_id)))
        )
        return {
            "ok": ok,
            "temp_only": True,
            "completed": completed,
            "completed_again": completed_again,
            "task_status": task_after.get("status"),
            "ignored_missing_base_owner_event_count": int(ignored["n"] if ignored else 0),
            "invalid_ack_quarantined_event_count": int(quarantined["n"] if quarantined else 0),
            "ack_runtime_present": bool(queue.runtime_get(mcp_ack_key(task_id))),
            "invalid_ack_runtime_present": bool(queue.runtime_get(invalid_mcp_ack_key(task_id))),
            "assertion": "MCP ack markers only complete valid supplement tasks, never ownerless or active final-reply tasks",
        }

def mcp_ack_missing_base_owner_check() -> dict[str, Any]:
    """Temp-only check that ownerless MCP ack cannot swallow a mobile task."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-mcp-ack-missing-owner-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "ownerless-ack-task"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                    queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "must not be consumed by ownerless ack",
                    hashlib.sha256(b"must not be consumed by ownerless ack").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-1",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(mcp_ack_key(task_id), json.dumps({"thread_id": "thread-1", "acked_at": now}, ensure_ascii=False))
        is_supplement = task_is_supplement_context(queue, task_id)
        completed = process_mcp_acked_pending_supplements(queue)
        completed_again = process_mcp_acked_pending_supplements(queue)
        task_after = queue.get_task(task_id) or {}
        with queue.session() as db:
            ignored = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_ack_ignored_missing_base_owner'
                """,
                (task_id,),
            ).fetchone()
            quarantined = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='mcp_ack_invalid_quarantined'
                """,
                (task_id,),
            ).fetchone()
        ok = bool(
            completed.get("completed_count") == 0
            and completed_again.get("completed_count") == 0
            and task_after.get("status") == "sent_to_codex"
            and not task_after.get("result")
            and not is_supplement
            and int(ignored["n"] if ignored else 0) == 1
            and int(quarantined["n"] if quarantined else 0) == 1
            and not queue.runtime_get(mcp_ack_key(task_id))
            and bool(queue.runtime_get(invalid_mcp_ack_key(task_id)))
        )
        return {
            "ok": ok,
            "temp_only": True,
            "completed": completed,
            "completed_again": completed_again,
            "task_status": task_after.get("status"),
            "task_result": task_after.get("result"),
            "is_supplement": is_supplement,
            "ignored_missing_base_owner_event_count": int(ignored["n"] if ignored else 0),
            "invalid_ack_quarantined_event_count": int(quarantined["n"] if quarantined else 0),
            "ack_runtime_present": bool(queue.runtime_get(mcp_ack_key(task_id))),
            "invalid_ack_runtime_present": bool(queue.runtime_get(invalid_mcp_ack_key(task_id))),
            "assertion": "MCP ack without a valid base_task_id is quarantined once and never completes a task as supplement",
        }

def invalid_mcp_ack_not_published_supplement_check() -> dict[str, Any]:
    """Temp-only check that invalid ack runtime cannot hide a pending task."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-invalid-ack-published-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "pending-with-invalid-ack"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status, result, push_status,
                    receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "user@im.wechat",
                    "",
                    "/ask",
                    "pending must stay dispatchable",
                    hashlib.sha256(b"pending must stay dispatchable").hexdigest(),
                    task_id,
                    "L1",
                    "pending",
                    "",
                    "",
                    "primary",
                    "thread-1",
                    "{}",
                    now,
                    now,
                ),
            )
        queue.runtime_set(
            mcp_ack_key(task_id),
            json.dumps({"thread_id": "thread-1", "acked_at": now, "base_task_id": ""}, ensure_ascii=False),
        )
        published_before = pending_task_is_published_bridge_supplement(queue, task_id, "thread-1")
        completed = process_mcp_acked_pending_supplements(queue)
        published_after = pending_task_is_published_bridge_supplement(queue, task_id, "thread-1")
        task_after = queue.get_task(task_id) or {}
        ok = bool(
            not published_before
            and not published_after
            and completed.get("completed_count") == 0
            and task_after.get("status") == "pending"
            and not queue.runtime_get(mcp_ack_key(task_id))
            and bool(queue.runtime_get(invalid_mcp_ack_key(task_id)))
        )
        return {
            "ok": ok,
            "temp_only": True,
            "published_before": published_before,
            "published_after": published_after,
            "completed": completed,
            "task_status": task_after.get("status"),
            "ack_runtime_present": bool(queue.runtime_get(mcp_ack_key(task_id))),
            "invalid_ack_runtime_present": bool(queue.runtime_get(invalid_mcp_ack_key(task_id))),
            "assertion": "invalid MCP ack runtime is not treated as a published supplement and is quarantined once",
        }

def followup_redelivery_stale_pending_guard_check() -> dict[str, Any]:
    """Temp-only check that stale pending rows cannot trigger a newer active redelivery."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-followup-stale-guard-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        user = "primary-stale-followup@im.wechat"
        active_id = "active-waiting"
        stale_id = "stale-pending"
        fresh_id = "fresh-risky-pending"
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        fresh = (now_dt + timedelta(seconds=2)).isoformat()
        with queue.session() as db:
            for tid, text, status, risk, created_at, queued_at, sent_at in [
                (active_id, "active owner", "sent_to_codex", "L1", now, now, now),
                (stale_id, "old pending must not trigger", "pending", "L1", old, None, None),
                (fresh_id, "new but risky pending", "pending", "L2", fresh, None, None),
            ]:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        user,
                        "",
                        "/ask",
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        tid,
                        risk,
                        status,
                        "",
                        "",
                        "primary",
                        "thread-1",
                        "{}",
                        created_at,
                        created_at,
                        queued_at,
                        sent_at,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {"thread_id": "thread-1", "turn_id": "turn-active", "expected_task_ids": [active_id]},
            active_id,
        )
        queue.runtime_set(task_turn_key(active_id), "turn-active")
        queue.runtime_set(task_batch_key(active_id), "batch-active")
        queue.runtime_set(task_expected_ids_key(active_id), json.dumps([active_id], ensure_ascii=False))
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(active_id) or {},
            "generation_active_without_owned_result",
            {"reason": "temp regression active waits for a new same-thread follow-up"},
        )
        active_tasks = [queue.get_task(active_id) or {}]
        config = {
            "openclaw": {"account_id": "primary", "phone_status_ack_events": []},
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "accounts": {"users": {user: {"account_id": "primary"}}},
            "openclaw_accounts": {"primary": {"userId": user, "token": "present"}},
            "permissions": {
                "users": {user: {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "codex_thread_id": "thread-1",
                "active_recovery_max_sent_checks_per_cycle": 0,
            },
            "threads": {
                "default_id": "visible-thread",
                "items": [{"id": "visible-thread", "name": "Visible Thread", "thread_id": "thread-1"}],
            },
        }
        queue.config = config
        set_active_thread(queue, user, "visible-thread")
        stale_match = find_waiting_followup_redelivery_active(queue, config, queue.get_task(stale_id) or {}, active_tasks)
        fresh_match = find_waiting_followup_redelivery_active(queue, config, queue.get_task(fresh_id) or {}, active_tasks)
        original_check = globals()["check_codex_health"]
        original_recover_active = globals()["recover_active_codex_tasks"]
        original_recover_reply_sending = globals()["recover_stale_reply_sending_tasks"]
        original_onboarding_sync = globals()["maybe_sync_openclaw_account_onboarding"]
        original_account_sync = globals()["sync_openclaw_accounts_to_bridge_users"]
        original_reply_reconcile = globals()["reconcile_completed_replies_waiting_push"]
        original_process_mcp = globals()["process_mcp_acked_pending_supplements"]
        original_pending_reply_retries = globals()["process_pending_reply_context_retries"]
        original_queued_release = globals()["release_queued_tasks_for_active_owner_supplement"]
        original_orphan_promotion = globals()["promote_orphaned_bridge_supplements"]
        original_invalid_release = globals()["release_invalid_published_supplements"]
        original_pending_visible_recovery = globals()["recover_pending_visible_cdp_unconfirmed_results"]
        original_send_status_ack = globals()["send_status_ack"]
        try:
            globals()["check_codex_health"] = lambda _config: {"ok": True, "healthy": True, "mode": "test"}
            globals()["recover_active_codex_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "action": "no_active_tasks",
                "recovered": 0,
                "reverted": 0,
            }
            globals()["recover_stale_reply_sending_tasks"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["maybe_sync_openclaw_account_onboarding"] = lambda *_args, **_kwargs: {
                "ok": True,
                "updated": 0,
            }
            globals()["sync_openclaw_accounts_to_bridge_users"] = lambda *_args, **_kwargs: {
                "ok": True,
                "synced": 0,
            }
            globals()["reconcile_completed_replies_waiting_push"] = lambda *_args, **_kwargs: {
                "ok": True,
                "reconciled": 0,
            }
            globals()["process_mcp_acked_pending_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "completed": [],
                "completed_count": 0,
            }
            globals()["process_pending_reply_context_retries"] = lambda *_args, **_kwargs: {
                "ok": True,
                "scheduled": 0,
            }
            globals()["release_queued_tasks_for_active_owner_supplement"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
            }
            globals()["promote_orphaned_bridge_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "promoted": [],
                "promoted_count": 0,
                "resumed": [],
                "resumed_count": 0,
            }
            globals()["release_invalid_published_supplements"] = lambda *_args, **_kwargs: {
                "ok": True,
                "released": [],
                "released_count": 0,
                "preserved": [],
                "preserved_count": 0,
            }
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = lambda *_args, **_kwargs: {
                "ok": True,
                "recovered": [],
                "recovered_count": 0,
            }
            globals()["send_status_ack"] = lambda *_args, **_kwargs: {"ok": True, "mode": "test"}
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                first = worker_once(queue, config, limit=5, task_id=fresh_id)
                second = worker_once(queue, config, limit=5, task_id=fresh_id)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["recover_active_codex_tasks"] = original_recover_active
            globals()["recover_stale_reply_sending_tasks"] = original_recover_reply_sending
            globals()["maybe_sync_openclaw_account_onboarding"] = original_onboarding_sync
            globals()["sync_openclaw_accounts_to_bridge_users"] = original_account_sync
            globals()["reconcile_completed_replies_waiting_push"] = original_reply_reconcile
            globals()["process_mcp_acked_pending_supplements"] = original_process_mcp
            globals()["process_pending_reply_context_retries"] = original_pending_reply_retries
            globals()["release_queued_tasks_for_active_owner_supplement"] = original_queued_release
            globals()["promote_orphaned_bridge_supplements"] = original_orphan_promotion
            globals()["release_invalid_published_supplements"] = original_invalid_release
            globals()["recover_pending_visible_cdp_unconfirmed_results"] = original_pending_visible_recovery
            globals()["send_status_ack"] = original_send_status_ack
        with queue.session() as db:
            failed = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='followup_redelivery_supplement_publish_failed'
                """,
                (fresh_id,),
            ).fetchone()
        failure_count = int(failed["n"] if failed else 0)
        ok = bool(
            not stale_match
            and fresh_match
            and str(fresh_match.get("id") or "") == active_id
            and failure_count == 1
            and (queue.get_task(stale_id) or {}).get("status") == "pending"
            and (queue.get_task(fresh_id) or {}).get("status") == "pending"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "stale_trigger_matched": bool(stale_match),
            "fresh_trigger_matched": str((fresh_match or {}).get("id") or ""),
            "first_worker_result": first,
            "second_worker_result": second,
            "publish_failure_event_count": failure_count,
            "statuses": {
                stale_id: (queue.get_task(stale_id) or {}).get("status"),
                fresh_id: (queue.get_task(fresh_id) or {}).get("status"),
            },
            "assertion": "only messages created after the waiting marker can trigger primary redelivery, and ineligible publish failures are coalesced",
    }


_CHECKS = {
    "followup_redelivery_stale_pending_guard_check": followup_redelivery_stale_pending_guard_check,
    "invalid_mcp_ack_not_published_supplement_check": invalid_mcp_ack_not_published_supplement_check,
    "mcp_ack_missing_base_owner_check": mcp_ack_missing_base_owner_check,
    "mcp_ack_does_not_complete_owner_check": mcp_ack_does_not_complete_owner_check,
    "supplement_invalid_published_release_check": supplement_invalid_published_release_check,
    "queued_same_route_supplement_recovery_check": queued_same_route_supplement_recovery_check,
    "supplement_non_owner_host_check": supplement_non_owner_host_check,
    "queued_turn_rehydrate_check": queued_turn_rehydrate_check,
    "active_runtime_rehydrate_check": active_runtime_rehydrate_check,
    "supplement_release_no_republish_check": supplement_release_no_republish_check,
    "supplement_unacked_timeout_release_check": supplement_unacked_timeout_release_check,
    "supplement_cli_fallback_check": supplement_cli_fallback_check,
    "supplement_mcp_disconnect_no_primary_fallback_check": supplement_mcp_disconnect_no_primary_fallback_check,
    "completed_owner_supplement_ack_window_check": completed_owner_supplement_ack_window_check,
    "failed_base_supplement_owner_promotion_check": failed_base_supplement_owner_promotion_check,
    "orphaned_supplement_promotion_with_push_evidence_check": orphaned_supplement_promotion_with_push_evidence_check,
    "delivery_group_stale_active_snapshot_check": delivery_group_stale_active_snapshot_check,
    "delivery_group_owner_event_fallback_check": delivery_group_owner_event_fallback_check,
    "supplement_final_owner_check": supplement_final_owner_check,
    "queued_turn_materialized_readback_rehydrate_check": queued_turn_materialized_readback_rehydrate_check,
    "pending_backlog_supplement_batch_check": pending_backlog_supplement_batch_check,
    "delivery_group_owner_check": delivery_group_owner_check,
    "supplement_ack_gating_check": supplement_ack_gating_check,
    "followup_redelivery_mcp_supplement_check": followup_redelivery_mcp_supplement_check,
    "orphaned_supplement_promotion_check": orphaned_supplement_promotion_check,
    "pending_visible_cdp_multi_supplement_consumption_check": pending_visible_cdp_multi_supplement_consumption_check,
    "active_visible_cdp_supplement_publish_check": active_visible_cdp_supplement_publish_check,
    "followup_redelivery_fifo_supplement_check": followup_redelivery_fifo_supplement_check,
}
