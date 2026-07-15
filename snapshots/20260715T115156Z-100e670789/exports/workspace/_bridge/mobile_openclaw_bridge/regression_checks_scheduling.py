"""Scheduling and route-fairness regression checks for the mobile bridge.

Owns: temp-only self-tests for fair scheduling, route busy gates, active-slot
release, and recovery fairness behavior.
Non-goals: production scheduling implementation, permission policy, or reply
sending.
State behavior: checks use synthetic queues and may monkeypatch CLI helpers;
each check is rebound to the CLI global namespace to preserve legacy fixture
behavior after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_scheduling_regression_check(name: str, env: dict[str, Any]) -> dict[str, Any]:
    """Run a moved scheduling regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown scheduling regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound()

def fair_scheduling_check() -> dict[str, Any]:
    """Temp-only regression check for route-scoped scheduling fairness."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-fair-") as temp_root:
        temp = Path(temp_root)
        users = {
            "active": "fair-active@im.wechat",
            "same_route": "fair-same-route@im.wechat",
            "other_route": "fair-other-route@im.wechat",
        }
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": list(users.values())},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 1,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "route-a",
                        "name": "Route A",
                        "description": "fair scheduling route A",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                    {
                        "id": "route-b",
                        "name": "Route B",
                        "description": "fair scheduling route B",
                        "aliases": [],
                        "thread_id": "thread-b",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["active"], "route-a")
        set_active_thread(queue, users["same_route"], "route-a")
        set_active_thread(queue, users["other_route"], "route-b")

        active = queue.enqueue(
            "active route task",
            source="openclaw-weixin",
            external_user=users["active"],
            metadata={"msg_id": "fair-active", "receiver_account_id": "backup1"},
        )
        same_route = queue.enqueue(
            "same route pending",
            source="openclaw-weixin",
            external_user=users["same_route"],
            metadata={"msg_id": "fair-same-route", "receiver_account_id": "backup1"},
        )
        other_route = queue.enqueue(
            "other route pending",
            source="openclaw-weixin",
            external_user=users["other_route"],
            metadata={"msg_id": "fair-other-route", "receiver_account_id": "backup2"},
        )
        active_id = str(active["id"])
        same_route_id = str(same_route["id"])
        other_route_id = str(other_route["id"])
        queued_ok, queued_message = queue.queue_for_codex([active_id], "thread-a", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex([active_id])
            queue.runtime_set(task_turn_key(active_id), "turn-active")
            queue.runtime_set(task_batch_key(active_id), "batch-active")
            queue.runtime_set(task_expected_ids_key(active_id), json.dumps([active_id], ensure_ascii=False))

        original_check = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        status_ack_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "newText": "", "status": "running"}

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
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-" + thread_id,
                "client_user_message_id": "batch-" + thread_id,
                "expected_task_ids": task_ids,
            }

        def fake_send_status_ack(
            queue_arg: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            event_type_arg: str,
        ) -> dict[str, Any]:
            status_ack_calls.append(
                {
                    "task_id": str(task_arg.get("id") or ""),
                    "event_type": event_type_arg,
                    "text": text_arg,
                }
            )
            queue_arg.add_event(
                "wecom",
                f"{event_type_arg}_test",
                {"ok": True, "text": text_arg},
                str(task_arg.get("id") or ""),
            )
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
            result_second = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result"] = original_poll
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        active_after = queue.get_task(active_id) or {}
        same_after = queue.get_task(same_route_id) or {}
        other_after = queue.get_task(other_route_id) or {}
        with queue.session() as db:
            continuation_deferred_count = db.execute(
                "SELECT COUNT(*) AS n FROM mobile_events WHERE task_id=? AND event_type='continuation_deferred'",
                (same_route_id,),
            ).fetchone()["n"]
            route_busy_count = db.execute(
                "SELECT COUNT(*) AS n FROM mobile_events WHERE task_id=? AND event_type='thread_delivery_route_busy'",
                (same_route_id,),
            ).fetchone()["n"]
        continuation_ack_calls = [
            call for call in status_ack_calls if call.get("event_type") == "status_ack_continuation_deferred"
        ]
        ok = bool(
            queued_ok
            and active_after.get("status") == "sent_to_codex"
            and same_after.get("status") == "pending"
            and other_after.get("status") == "sent_to_codex"
            and result.get("action") == "dispatched_waiting_result"
            and result.get("thread_id") == "thread-b"
            and result_second.get("action") == "idle_no_dispatchable_thread"
            and int(continuation_deferred_count or 0) == 1
            and int(route_busy_count or 0) == 1
            and len(continuation_ack_calls) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "queued_active": {"ok": queued_ok, "message": queued_message},
            "worker_result": result,
            "worker_result_second": result_second,
            "statuses": {
                active_id: active_after.get("status"),
                same_route_id: same_after.get("status"),
                other_route_id: other_after.get("status"),
            },
            "continuation_deferred_count": int(continuation_deferred_count or 0),
            "route_busy_count": int(route_busy_count or 0),
            "continuation_ack_calls": continuation_ack_calls,
            "assertion": "same route is deferred once while other route dispatches",
        }

def waiting_redelivery_gate_route_fairness_check() -> dict[str, Any]:
    """Temp-only check that a deferred waiting-redelivery gate does not starve other routes."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-gate-fair-") as temp_root:
        temp = Path(temp_root)
        users = {
            "primary": "gate-fair-primary@im.wechat",
            "backup": "gate-fair-backup@im.wechat",
        }
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": list(users.values())},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_id": "primary", "phone_status_ack_events": [], "account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "codex_thread_id": "visible-primary-thread",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 0,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "primary-route",
                        "name": "Primary Route",
                        "aliases": [],
                        "thread_id": "visible-primary-thread",
                    },
                    {
                        "id": "backup-route",
                        "name": "Backup Route",
                        "aliases": [],
                        "thread_id": "backup-thread",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["primary"], "primary-route")
        set_active_thread(queue, users["backup"], "backup-route")

        owner = queue.enqueue(
            "primary owner still generating",
            source="openclaw-weixin",
            external_user=users["primary"],
            metadata={"msg_id": "gate-owner", "receiver_account_id": "primary"},
        )
        owner_id = str(owner["id"])
        queued_ok, queued_message = queue.queue_for_codex([owner_id], "visible-primary-thread", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex([owner_id])
            queue.runtime_set(task_turn_key(owner_id), "turn-primary")
            queue.runtime_set(task_batch_key(owner_id), "batch-primary")
            queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
            queue.runtime_set(task_ack_code_key(owner_id), "owner-ack")
            queue.runtime_set(task_result_code_key(owner_id), "owner-result")
            mark_waiting_followup_redelivery(
                queue,
                queue.get_task(owner_id) or {},
                "generation_active_without_owned_result",
                {"test": "gate should defer without blocking backup route"},
            )
        followup = queue.enqueue(
            "same-thread follow-up",
            source="openclaw-weixin",
            external_user=users["primary"],
            metadata={"msg_id": "gate-followup", "receiver_account_id": "primary"},
        )
        backup = queue.enqueue(
            "backup should still dispatch",
            source="openclaw-weixin",
            external_user=users["backup"],
            metadata={"msg_id": "gate-backup", "receiver_account_id": "backup1"},
        )
        followup_id = str(followup["id"])
        backup_id = str(backup["id"])

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_try_gate = globals()["try_complete_owned_result_before_redelivery"]
        original_active_publish = globals()["publish_attachment_active_supplements"]
        original_publish = globals()["publish_attachment_supplement_for_active"]
        original_inspect = globals()["inspect_codex_thread_for_dispatch"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        status_ack_calls: list[dict[str, Any]] = []
        dispatch_calls: list[dict[str, Any]] = []

        def fake_poll_codex_result_cdp(_config: dict[str, Any], _baseline_key: str = "", *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": True,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
            }

        def fake_try_complete_owned_result_before_redelivery(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "completed": False,
                "defer_redelivery": True,
                "reason": "owned_result_not_complete",
                "generation_active": True,
                "ack_seen": False,
            }

        def fake_publish_attachment_active_supplements(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "published": [], "duplicates": [], "failed": [], "suppressed": ["test prepass disabled"]}

        def fake_publish_attachment_supplement_for_active(
            queue_arg: MobileQueue,
            _config_arg: dict[str, Any],
            active_task: dict[str, Any],
            thread_id: str,
            tasks: list[dict[str, Any]],
            delivery_mode: str,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            for task_id in task_ids:
                queue_arg.add_event(
                    "local",
                    "attachment_supplement_pending_published",
                    {
                        "active_task_id": str(active_task.get("id") or ""),
                        "thread_id": thread_id,
                        "delivery_mode": delivery_mode,
                        "test": True,
                    },
                    task_id,
                )
            return {"ok": True, "published": task_ids, "published_count": len(task_ids)}

        def fake_inspect_codex_thread_for_dispatch(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            dispatch_calls.append({"thread_id": thread_id, "task_ids": task_ids})
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-" + thread_id,
                "client_user_message_id": "batch-" + thread_id,
                "expected_task_ids": task_ids,
            }

        def fake_send_status_ack(
            queue_arg: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            event_type_arg: str,
        ) -> dict[str, Any]:
            status_ack_calls.append(
                {
                    "task_id": str(task_arg.get("id") or ""),
                    "event_type": event_type_arg,
                    "text": text_arg,
                }
            )
            queue_arg.add_event(
                "wecom",
                f"{event_type_arg}_test",
                {"ok": True, "text": text_arg},
                str(task_arg.get("id") or ""),
            )
            return {"ok": True, "mode": "test"}

        try:
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["try_complete_owned_result_before_redelivery"] = fake_try_complete_owned_result_before_redelivery
            globals()["publish_attachment_active_supplements"] = fake_publish_attachment_active_supplements
            globals()["publish_attachment_supplement_for_active"] = fake_publish_attachment_supplement_for_active
            globals()["inspect_codex_thread_for_dispatch"] = fake_inspect_codex_thread_for_dispatch
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["try_complete_owned_result_before_redelivery"] = original_try_gate
            globals()["publish_attachment_active_supplements"] = original_active_publish
            globals()["publish_attachment_supplement_for_active"] = original_publish
            globals()["inspect_codex_thread_for_dispatch"] = original_inspect
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        owner_after = queue.get_task(owner_id) or {}
        followup_after = queue.get_task(followup_id) or {}
        backup_after = queue.get_task(backup_id) or {}
        with queue.session() as db:
            event_counts = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id IN (?,?,?)
                    GROUP BY event_type
                    """,
                    (owner_id, followup_id, backup_id),
                ).fetchall()
            }
        ok = bool(
            queued_ok
            and result.get("action") == "dispatched_waiting_result"
            and result.get("thread_id") == "backup-thread"
            and result.get("delivery_mode") == "codex-app-server"
            and result.get("waiting_redelivery_gate_deferred") in {None, 1}
            and owner_after.get("status") == "sent_to_codex"
            and followup_after.get("status") == "pending"
            and backup_after.get("status") == "sent_to_codex"
            and dispatch_calls == [{"thread_id": "backup-thread", "task_ids": [backup_id]}]
            and event_counts.get("followup_triggered_waiting_redelivery_deferred", 0) == 1
            and event_counts.get("dispatch_scan_gate_deferred_continue", 0) == 1
            and event_counts.get("dispatch_fairness_after_gate_defer", 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "queued_owner": {"ok": queued_ok, "message": queued_message},
            "worker_result": result,
            "statuses": {
                owner_id: owner_after.get("status"),
                followup_id: followup_after.get("status"),
                backup_id: backup_after.get("status"),
            },
            "dispatch_calls": dispatch_calls,
            "status_ack_calls": status_ack_calls,
            "event_counts": event_counts,
            "assertion": "deferred waiting-redelivery gate keeps the same-thread follow-up pending while allowing an independent backup route to dispatch",
        }

def same_route_expired_active_order_check() -> dict[str, Any]:
    """Temp-only check that an expired same-route active is retried before newer pending messages."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-order-") as temp_root:
        temp = Path(temp_root)
        users = {
            "active": "order-active@im.wechat",
            "later": "order-later@im.wechat",
        }
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": list(users.values())},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 1,
                "active_slot_release_after_seconds": 30,
                "delivery_retry_seconds": 0,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "route-a",
                        "name": "Route A",
                        "description": "same route ordering",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["active"], "route-a")
        set_active_thread(queue, users["later"], "route-a")
        active = queue.enqueue(
            "old active route task",
            source="openclaw-weixin",
            external_user=users["active"],
            metadata={"msg_id": "order-active", "receiver_account_id": "backup1"},
        )
        later = queue.enqueue(
            "later same route pending",
            source="openclaw-weixin",
            external_user=users["later"],
            metadata={"msg_id": "order-later", "receiver_account_id": "backup1"},
        )
        active_id = str(active["id"])
        later_id = str(later["id"])
        queued_ok, queued_message = queue.queue_for_codex([active_id], "thread-a", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex([active_id])
            old_sent_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            with queue.session() as db:
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (old_sent_at, old_sent_at, active_id),
                )
            queue.runtime_set(task_turn_key(active_id), "turn-active")
            queue.runtime_set(task_batch_key(active_id), "batch-active")
            queue.runtime_set(task_expected_ids_key(active_id), json.dumps([active_id], ensure_ascii=False))
            queue.add_event(
                "local",
                "codex_turn_started",
                {
                    "thread_id": "thread-a",
                    "turn_id": "turn-active",
                    "client_message_id": "batch-active",
                    "expected_task_ids": [active_id],
                },
                active_id,
            )
            queue.add_event(
                "local",
                "attachment_supplement_pending_published",
                {
                    "active_task_id": later_id,
                    "thread_id": "thread-a",
                    "signature": "stale-owner-inversion-marker",
                },
                active_id,
            )

        original_check = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        dispatches: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "newText": "", "status": "running"}

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
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
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
                "turn_id": "turn-" + thread_id,
                "client_user_message_id": "batch-" + thread_id,
                "expected_task_ids": task_ids,
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            first = worker_once(queue, config, limit=5)
            second = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result"] = original_poll
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        active_after_first = first.get("recovery") or {}
        active_after = queue.get_task(active_id) or {}
        later_after = queue.get_task(later_id) or {}
        bridge_payload = queue.runtime_get(bridge_supplement_key("thread-a"))
        ok = bool(
            queued_ok
            and active_after_first.get("reverted") == 1
            and dispatches
            and dispatches[0]["task_ids"] == [active_id]
            and active_after.get("status") == "sent_to_codex"
            and later_after.get("status") == "pending"
            and not task_is_supplement_context(queue, active_id)
            and not bridge_payload
            and first.get("action") == "dispatched_waiting_result"
            and second.get("action") == "idle_no_dispatchable_thread"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "queued_active": {"ok": queued_ok, "message": queued_message},
            "first": first,
            "second": second,
            "dispatches": dispatches,
            "statuses": {
                active_id: active_after.get("status"),
                later_id: later_after.get("status"),
            },
            "owner_is_supplement": task_is_supplement_context(queue, active_id),
            "bridge_supplement_present": bool(bridge_payload),
            "assertion": "same-route ordering is preserved: expired old active is requeued as final-reply owner and cannot be inverted into a later task's supplement",
        }

def active_slot_release_check() -> dict[str, Any]:
    """Temp-only check that expired active slots requeue and keep FIFO priority."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-release-") as temp_root:
        temp = Path(temp_root)
        users = {
            "active": "release-active@im.wechat",
            "other_route": "release-other@im.wechat",
        }
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": list(users.values())},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 1,
                "active_slot_release_after_seconds": 30,
                "delivery_retry_seconds": 3,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "route-a",
                        "name": "Route A",
                        "description": "active release route A",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                    {
                        "id": "route-b",
                        "name": "Route B",
                        "description": "active release route B",
                        "aliases": [],
                        "thread_id": "thread-b",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["active"], "route-a")
        set_active_thread(queue, users["other_route"], "route-b")

        active = queue.enqueue(
            "old active route task",
            source="openclaw-weixin",
            external_user=users["active"],
            metadata={"msg_id": "release-active", "receiver_account_id": "backup1"},
        )
        other = queue.enqueue(
            "other route pending",
            source="openclaw-weixin",
            external_user=users["other_route"],
            metadata={"msg_id": "release-other", "receiver_account_id": "backup2"},
        )
        active_id = str(active["id"])
        other_id = str(other["id"])
        queued_ok, queued_message = queue.queue_for_codex([active_id], "thread-a", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex([active_id])
            old_sent_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            with queue.session() as db:
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (old_sent_at, old_sent_at, active_id),
                )
            queue.runtime_set(task_turn_key(active_id), "turn-active")
            queue.runtime_set(task_batch_key(active_id), "batch-active")
            queue.runtime_set(task_expected_ids_key(active_id), json.dumps([active_id], ensure_ascii=False))

        original_check = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        dispatches: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "newText": "", "status": "running"}

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
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
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
                "turn_id": "turn-" + thread_id,
                "client_user_message_id": "batch-" + thread_id,
                "expected_task_ids": task_ids,
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result"] = original_poll
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        active_after = queue.get_task(active_id) or {}
        other_after = queue.get_task(other_id) or {}
        retry = get_delivery_retry(queue, active_id)
        runtime_retained = all(
            queue.runtime_get(key)
            for key in (task_turn_key(active_id), task_batch_key(active_id), task_expected_ids_key(active_id))
        )
        ok = bool(
            queued_ok
            and dispatches
            and dispatches[0]["thread_id"] == "thread-a"
            and dispatches[0]["task_ids"] == [active_id]
            and active_after.get("status") == "sent_to_codex"
            and other_after.get("status") == "pending"
            and not retry.get("active")
            and runtime_retained
            and result.get("action") == "dispatched_waiting_result"
            and result.get("thread_id") == "thread-a"
            and int((result.get("recovery") or {}).get("lease_released") or 0) == 1
            and int((result.get("recovery") or {}).get("reverted") or 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "queued_active": {"ok": queued_ok, "message": queued_message},
            "worker_result": result,
            "statuses": {
                active_id: active_after.get("status"),
                other_id: other_after.get("status"),
            },
            "dispatches": dispatches,
            "delivery_retry": retry,
            "runtime_retained": runtime_retained,
            "assertion": "expired active route lease requeues the old active task and retries it before later route work when it is dispatchable",
        }

def active_generation_preserves_supplement_check() -> dict[str, Any]:
    """Temp-only check that a progressing active turn is not redelivered after its lease."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-generation-supp-") as temp_root:
        temp = Path(temp_root)
        user = "generation-active@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 2,
                "active_slot_release_after_seconds": 30,
                "delivery_retry_seconds": 0,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "route-a",
                        "name": "Route A",
                        "description": "active generation supplement route",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "route-a")
        active = queue.enqueue(
            "old active still generating",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "generation-active-owner", "receiver_account_id": "backup1"},
        )
        supplement = queue.enqueue(
            "new supplement while active",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "generation-active-supplement", "receiver_account_id": "backup1"},
        )
        active_id = str(active["id"])
        supplement_id = str(supplement["id"])
        queued_ok, queued_message = queue.queue_for_codex([active_id], "thread-a", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex([active_id])
            old_sent_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            with queue.session() as db:
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (old_sent_at, old_sent_at, active_id),
                )
            queue.runtime_set(task_turn_key(active_id), "turn-active")
            queue.runtime_set(task_batch_key(active_id), "batch-active")
            queue.runtime_set(task_expected_ids_key(active_id), json.dumps([active_id], ensure_ascii=False))
            queue.add_event(
                "local",
                "codex_turn_started",
                {
                    "thread_id": "thread-a",
                    "turn_id": "turn-active",
                    "client_message_id": "batch-active",
                    "expected_task_ids": [active_id],
                },
                active_id,
            )

        original_check = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        dispatches: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "newText": "",
                "status": "inProgress",
            }

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
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            _config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            dispatches.append(
                {
                    "thread_id": thread_id,
                    "task_ids": [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")],
                }
            )
            return {"ok": True, "mode": "test", "thread_id": thread_id}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            active_task = queue.get_task(active_id) or {}
            supplement_task = queue.get_task(supplement_id) or {}
            published = publish_attachment_supplement_for_active(
                queue,
                config,
                active_task,
                "thread-a",
                [supplement_task],
                "codex-app-server",
            )
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result"] = original_poll
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        active_after = queue.get_task(active_id) or {}
        supplement_after = queue.get_task(supplement_id) or {}
        bridge_payload = queue.runtime_get(bridge_supplement_key("thread-a"))
        release_deferred = task_event_exists(queue, active_id, "active_slot_release_deferred_generation_active")
        ok = bool(
            queued_ok
            and published.get("published")
            and not dispatches
            and active_after.get("status") == "sent_to_codex"
            and supplement_after.get("status") == "pending"
            and bridge_payload
            and task_is_supplement_context(queue, supplement_id)
            and release_deferred
            and result.get("action") == "idle_no_dispatchable_thread"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "queued_active": {"ok": queued_ok, "message": queued_message},
            "published": published,
            "worker_result": result,
            "dispatches": dispatches,
            "statuses": {
                active_id: active_after.get("status"),
                supplement_id: supplement_after.get("status"),
            },
            "bridge_supplement_present": bool(bridge_payload),
            "release_deferred_generation_active": release_deferred,
            "assertion": "lease expiry must not redeliver a still-generating owner or invalidate pending MCP supplements",
        }

def active_recovery_route_fairness_check() -> dict[str, Any]:
    """Temp-only check that active result recovery is fair across routes."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-recovery-fair-") as temp_root:
        temp = Path(temp_root)
        users = {
            "a1": "active-fair-a1@im.wechat",
            "a2": "active-fair-a2@im.wechat",
            "b1": "active-fair-b1@im.wechat",
        }
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": list(users.values())},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "active_recovery_max_sent_checks_per_cycle": 2,
                "active_recovery_cooldown_seconds": 5,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "route-a",
                        "name": "Route A",
                        "description": "active recovery fairness route A",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                    {
                        "id": "route-b",
                        "name": "Route B",
                        "description": "active recovery fairness route B",
                        "aliases": [],
                        "thread_id": "thread-b",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["a1"], "route-a")
        set_active_thread(queue, users["a2"], "route-a")
        set_active_thread(queue, users["b1"], "route-b")

        ids: dict[str, str] = {}
        for label, user, thread_id, account_id in (
            ("a1", users["a1"], "thread-a", "backup1"),
            ("a2", users["a2"], "thread-a", "backup1"),
            ("b1", users["b1"], "thread-b", "backup2"),
        ):
            enqueued = queue.enqueue(
                f"active recovery {label}",
                source="openclaw-weixin",
                external_user=user,
                metadata={"msg_id": f"active-recovery-{label}", "receiver_account_id": account_id},
            )
            tid = str(enqueued["id"])
            ids[label] = tid
            now = datetime.now(timezone.utc).isoformat()
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET status='sent_to_codex',
                        codex_thread_id=?,
                        queued_for_codex_at=?,
                        sent_to_codex_at=?,
                        trigger_attempts=trigger_attempts+1,
                        updated_at=?
                    WHERE id=?
                    """,
                    (thread_id, now, now, now, tid),
                )
            queue.add_event("local", "queued_for_codex", {"thread_id": thread_id, "fixture": True}, tid)
            queue.add_event("local", "sent_to_codex", {"sent_at": now, "fixture": True}, tid)
            queue.runtime_set(task_turn_key(tid), f"turn-{label}")
            queue.runtime_set(task_batch_key(tid), f"batch-{label}")
            queue.runtime_set(task_expected_ids_key(tid), json.dumps([tid], ensure_ascii=False))

        old_base = datetime.now(timezone.utc) - timedelta(minutes=5)
        with queue.session() as db:
            for offset, label in enumerate(("a1", "a2", "b1")):
                stamp = (old_base + timedelta(seconds=offset)).isoformat()
                db.execute(
                    "UPDATE mobile_tasks SET sent_to_codex_at=?, updated_at=? WHERE id=?",
                    (stamp, stamp, ids[label]),
                )

        original_check = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]
        polled: list[str] = []
        pushed: list[str] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            turn_id: str,
            *_args: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            label = str(turn_id).replace("turn-", "")
            polled.append(label)
            if label == "b1":
                return {"ok": True, "healthy": True, "newText": "route B result", "status": "completed"}
            return {"ok": True, "healthy": True, "newText": "", "status": "running"}

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            _text: str,
            _config: dict[str, Any],
            media: str = "",
        ) -> dict[str, Any]:
            pushed.append(str(task.get("id") or ""))
            return {"ok": True, "async": True, "mode": "test", "media": media}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            result = recover_active_codex_tasks(queue, config)
        finally:
            globals()["check_codex_health"] = original_check
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        b_after = queue.get_task(ids["b1"]) or {}
        a2_after = queue.get_task(ids["a2"]) or {}
        ok = bool(
            result.get("checked_sent") == 2
            and result.get("sent_active") == 3
            and polled == ["a1", "b1"]
            and str(b_after.get("status") or "") == "done"
            and str(b_after.get("result") or "") == "route B result"
            and str(a2_after.get("status") or "") == "sent_to_codex"
            and ids["b1"] in pushed
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "polled": polled,
            "pushed": pushed,
            "statuses": {
                ids["a1"]: (queue.get_task(ids["a1"]) or {}).get("status"),
                ids["a2"]: a2_after.get("status"),
                ids["b1"]: b_after.get("status"),
            },
            "assertion": "active recovery checks one task per route before checking a second task from the same route",
        }

_CHECKS = {
    "fair_scheduling_check": fair_scheduling_check,
    "waiting_redelivery_gate_route_fairness_check": waiting_redelivery_gate_route_fairness_check,
    "same_route_expired_active_order_check": same_route_expired_active_order_check,
    "active_slot_release_check": active_slot_release_check,
    "active_generation_preserves_supplement_check": active_generation_preserves_supplement_check,
    "active_recovery_route_fairness_check": active_recovery_route_fairness_check,
}
