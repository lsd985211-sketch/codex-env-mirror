"""CDP delivery and final-reply regression checks for the mobile bridge.

Owns: temp-only self-tests for visible CDP delivery, unconfirmed submission
recovery, final reply visibility policy, media/text split behavior, ret=-2
classification, and Weixin session-timeout retry boundaries.
Non-goals: production CDP dispatch, final reply sending, queue recovery, or
permission decisions.
State behavior: checks use synthetic queues and monkeypatch CLI helpers; each
check is rebound to the CLI global namespace to preserve legacy fixture behavior
after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_cdp_delivery_regression_check(name: str, env: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a moved CDP/final-reply regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown CDP delivery regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound(*args, **kwargs)

def cdp_visible_delivery_check() -> dict[str, Any]:
    """Temp-only check that CDP delivery records visible prompt evidence."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-visible-") as temp_root:
        temp = Path(temp_root)
        cdp_tools = temp / "cdp-tools"
        cdp_tools.mkdir(parents=True, exist_ok=True)
        script = cdp_tools / "fake_cdp_send.js"
        script.write_text(
            """
const mode = process.env.FAKE_CDP_MODE || 'invisible';
const exactVisible = mode === 'exact';
const markerVisible = mode === 'marker';
process.stdout.write(JSON.stringify({
  ok: true,
  bodyHasPrompt: exactVisible || markerVisible,
  bodyHasExactPrompt: exactVisible,
  submissionConfirmed: exactVisible || markerVisible,
  submissionCheck: {
    bodyHasPrompt: exactVisible,
    markerVisible,
    userMarkerVisible: markerVisible,
    confirmed: exactVisible || markerVisible
  },
  target: { title: 'Codex', url: 'app://-/index.html' },
  baselineKey: 'baseline-test',
  baselineCount: 1,
  filledChars: 12,
  composerAfter: ''
}));
""".strip()
            + "\n",
            encoding="utf-8",
        )
        tasks = [
            {
                "id": "cdpvisible1",
                "risk_level": "L1",
                "external_user": "primary-probe@im.wechat",
                "command": "/ask",
                "text": "你好",
                "attachments_json": "[]",
            }
        ]
        config = {
            "trigger": {
                "delivery_mode": "codex-cdp",
                "node_path": "node",
                "codex_cdp_script": str(script),
                "codex_cdp_port": 9229,
                "delivery_timeout_seconds": 5,
            }
        }
        original_env = dict()
        original_ensure = globals()["ensure_codex_cdp"]
        for key in ("FAKE_CDP_MODE",):
            if key in os.environ:
                original_env[key] = os.environ[key]
        def fake_ensure_codex_cdp(_config: dict[str, Any]) -> dict[str, Any]:
            return {
                "ok": True,
                "started": False,
                "host": "127.0.0.1",
                "port": 9229,
                "transport_ready": True,
                "version_ready": True,
            }
        try:
            globals()["ensure_codex_cdp"] = fake_ensure_codex_cdp
            os.environ["FAKE_CDP_MODE"] = "invisible"
            invisible = dispatch_to_codex_cdp(tasks, "thread-visible", config)
            os.environ["FAKE_CDP_MODE"] = "exact"
            exact = dispatch_to_codex_cdp(tasks, "thread-visible", config)
            os.environ["FAKE_CDP_MODE"] = "marker"
            marker = dispatch_to_codex_cdp(tasks, "thread-visible", config)
        finally:
            globals()["ensure_codex_cdp"] = original_ensure
            if "FAKE_CDP_MODE" in original_env:
                os.environ["FAKE_CDP_MODE"] = original_env["FAKE_CDP_MODE"]
            else:
                os.environ.pop("FAKE_CDP_MODE", None)
        ok = bool(
            invisible.get("ok") is True
            and invisible.get("delivery_accepted") is True
            and invisible.get("submission_unconfirmed") is True
            and invisible.get("desktop_visible", {}).get("confirmed") is False
            and invisible.get("reason") == "cdp_visible_input_unconfirmed_observing"
            and invisible.get("diagnostic_only") is True
            and exact.get("ok")
            and exact.get("submission_confirmed") is True
            and exact.get("desktop_visible", {}).get("confirmed") is True
            and exact.get("desktop_visible", {}).get("body_has_exact_prompt") is True
            and marker.get("ok")
            and marker.get("submission_confirmed") is True
            and marker.get("desktop_visible", {}).get("confirmed") is True
            and marker.get("desktop_visible", {}).get("body_has_exact_prompt") is False
        )
        return {
            "ok": ok,
            "temp_only": True,
            "invisible": {
                "ok": invisible.get("ok"),
                "reason": invisible.get("reason"),
                "delivery_accepted": invisible.get("delivery_accepted"),
                "submission_unconfirmed": invisible.get("submission_unconfirmed"),
                "diagnostic_only": invisible.get("diagnostic_only"),
                "desktop_visible": invisible.get("desktop_visible"),
            },
            "exact": {
                "ok": exact.get("ok"),
                "desktop_visible": exact.get("desktop_visible"),
            },
            "marker": {
                "ok": marker.get("ok"),
                "desktop_visible": marker.get("desktop_visible"),
            },
            "assertion": "CDP delivery treats unconfirmed visible submissions as accepted transport with diagnostic-only visible evidence",
        }


def visible_cdp_unconfirmed_observation_check() -> dict[str, Any]:
    """Temp-only check that unconfirmed visible-CDP submissions need visible prompt evidence."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-unconfirmed-") as temp_root:
        temp = Path(temp_root)
        user = "visible-cdp-unconfirmed@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
                "account_onboarding_worker_sync_enabled": False,
            },
            "trigger": {
                "codex_thread_id": "visible-thread",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "worker_dispatch_attempts_per_cycle": 4,
                "active_recovery_max_sent_checks_per_cycle": 0,
            },
            "threads": {
                "default_id": "primary-thread",
                "items": [
                    {
                        "id": "primary-thread",
                        "name": "Primary Visible Thread",
                        "thread_id": "configured-thread",
                    }
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "primary-thread")
        first = queue.enqueue(
            "first visible message",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "visible-unconfirmed-1", "receiver_account_id": "primary"},
        )
        second = queue.enqueue(
            "second should be supplement",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "visible-unconfirmed-2", "receiver_account_id": "primary"},
        )
        first_id = str(first["id"])
        second_id = str(second["id"])
        dispatched: list[dict[str, Any]] = []
        status_acks: list[dict[str, str]] = []

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        original_check_health = globals()["check_codex_health"]
        dispatch_mode = "no_evidence"

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"healthy": True, "ok": True, "mode": "test"}

        def fake_poll_codex_result_cdp(_config: dict[str, Any], _baseline_key: str = "", *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "generationActive": False,
                "startup": {"ok": True, "host": "localhost", "port": 9229},
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            dispatch_config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            owner_ids = [str(item) for item in dispatch_config.get("_delivery_group_result_owner_task_ids") or [] if str(item)]
            batch_id = "batch-visible-unconfirmed"
            dispatched.append({"thread_id": thread_id, "task_ids": task_ids, "owner_ids": owner_ids})
            return {
                "ok": True,
                "delivery_accepted": True,
                "submission_confirmed": False,
                "submission_unconfirmed": True,
                "reason": "cdp_visible_input_unconfirmed_observing",
                "mode": "codex-cdp",
                "thread_id": thread_id,
                "turn_id": "cdp-visible-turn",
                "baseline_key": "baseline-visible-unconfirmed",
                "client_user_message_id": batch_id,
                "expected_task_ids": owner_ids,
                "mobile_protocols": {task_id: mobile_protocol(task_id, batch_id) for task_id in owner_ids},
                "desktop_visible": {
                    "confirmed": dispatch_mode == "marker_evidence",
                    "body_has_exact_prompt": False,
                    "submission_check": {"reason": "submission_confirmation_timeout"},
                },
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

        try:
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["check_codex_health"] = fake_check_codex_health
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                worker_result = worker_once(queue, config, limit=5)
                clear_delivery_retry(queue, [first_id, second_id])
                dispatch_mode = "marker_evidence"
                worker_result_with_marker = worker_once(queue, config, limit=5)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack
            globals()["check_codex_health"] = original_check_health

        first_after = queue.get_task(first_id) or {}
        second_after = queue.get_task(second_id) or {}
        first_runtime = {
            "turn_id": queue.runtime_get(task_turn_key(first_id)),
            "batch_id": queue.runtime_get(task_batch_key(first_id)),
            "expected_ids": task_batch_runtime(queue, first_id)[1],
            "result_code": queue.runtime_get(task_result_code_key(first_id)),
        }
        second_runtime = {
            "turn_id": queue.runtime_get(task_turn_key(second_id)),
            "batch_id": queue.runtime_get(task_batch_key(second_id)),
            "expected_ids": task_batch_runtime(queue, second_id)[1],
            "result_code": queue.runtime_get(task_result_code_key(second_id)),
            "is_supplement": task_is_supplement_context(queue, second_id),
        }
        with queue.session() as db:
            events = {
                row["event_type"]: int(row["n"])
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
            worker_result.get("action") == "dispatched_waiting_result"
            and worker_result.get("processed") == 1
            and first_after.get("status") == "sent_to_codex"
            and second_after.get("status") == "pending"
            and worker_result_with_marker.get("processed") == 0
            and first_after.get("status") == "sent_to_codex"
            and second_after.get("status") == "pending"
            and first_runtime["turn_id"] == "cdp-visible-turn"
            and second_runtime["turn_id"] == ""
            and first_runtime["expected_ids"] == [first_id]
            and second_runtime["expected_ids"] == []
            and bool(first_runtime["result_code"])
            and not bool(second_runtime["result_code"])
            and second_runtime["is_supplement"]
            and len(dispatched) == 1
            and dispatched[0].get("owner_ids") == [first_id]
            and events.get("cdp_visible_submission_unverified_observed", 0) == 1
            and events.get("delivery_group_member_released", 0) == 0
            and events.get("delivery_failed_reverted_to_pending", 0) == 0
            and events.get("pending_backlog_supplement_published", 0) >= 1
            and events.get("codex_turn_started", 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result_without_evidence": worker_result,
            "worker_result_with_marker": worker_result_with_marker,
            "dispatched": dispatched,
            "status_acks": status_acks,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "first_runtime": first_runtime,
            "second_runtime": second_runtime,
            "event_counts": events,
            "assertion": "primary visible-CDP unconfirmed submission keeps original owner/runtime and does not redispatch solely because visible evidence is missing",
        }


def pending_visible_cdp_result_recovery_check(reason: str = "cdp_visible_submission_needs_attention") -> dict[str, Any]:
    """Temp-only check that old visible-CDP unconfirmed rollback rows can be recovered."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-pending-cdp-result-") as temp_root:
        temp = Path(temp_root)
        user = "pending-cdp-result@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
                "account_onboarding_worker_sync_enabled": False,
            },
            "trigger": {"delivery_mode": "codex-cdp", "codex_thread_id": "visible-thread"},
        }
        queue = queue_from_config(config)
        first = queue.enqueue(
            "hello",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "pending-cdp-result-1", "receiver_account_id": "primary"},
        )
        second = queue.enqueue(
            "supplement",
            source="openclaw-weixin",
            external_user=user,
            external_conversation=user,
            metadata={"msg_id": "pending-cdp-result-2", "receiver_account_id": "primary"},
        )
        first_id = str(first["id"])
        second_id = str(second["id"])
        batch_id = "batch-pending-cdp-result"
        protocols = mobile_protocols([{"id": first_id}], batch_id)
        delivery = {
            "ok": False,
            "delivery_accepted": False,
            "reason": reason,
            "thread_id": "visible-thread",
            "mode": "codex-cdp",
            "turn_id": "cdp-visible-turn",
            "baseline_key": "baseline-old",
            "client_user_message_id": batch_id,
            "expected_task_ids": [first_id],
            "mobile_protocols": protocols,
            "desktop_visible": {"confirmed": False},
        }
        for tid in (first_id, second_id):
            queue.add_event(
                "local",
                "delivery_failed_reverted_to_pending",
                {"thread_id": "visible-thread", "delivery": delivery},
                tid,
            )
        publish_pending_backlog_supplement_for_owner(
            queue,
            config,
            queue.get_task(first_id) or {},
            "visible-thread",
            [queue.get_task(second_id) or {}],
            "codex-cdp",
        )
        pushed: list[dict[str, str]] = []
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]

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
                "newText": "combined recovered result",
                "ownership": {
                    "valid": True,
                    "matched_task_id": first_id,
                    "expected_task_ids": expected_task_ids or [],
                },
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            pushed.append({"task_id": str(task.get("id") or ""), "text": text, "media": str(media or "")})
            return {"ok": True, "async": True, "mode": "test"}

        try:
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            result = recover_pending_visible_cdp_unconfirmed_results(queue, config, queue.list_pending(10))
        finally:
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        first_after = queue.get_task(first_id) or {}
        second_after = queue.get_task(second_id) or {}
        runtime_raw = queue.runtime_get(bridge_supplement_key("visible-thread"))
        runtime_payload = json.loads(runtime_raw) if runtime_raw else {}
        with queue.session() as db:
            events = {
                row["event_type"]: int(row["n"])
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
            result.get("recovered_count") == 1
            and first_after.get("status") == "done"
            and first_after.get("result") == "combined recovered result"
            and second_after.get("status") == "pending"
            and second_after.get("result") == ""
            and task_event_exists(queue, second_id, "supplement_promoted_to_owner")
            and runtime_payload == {}
            and pushed == [{"task_id": first_id, "text": "combined recovered result", "media": ""}]
            and events.get("delivery_group_owner", 0) == 0
            and events.get("delivery_group_member", 0) == 0
            and events.get("pending_visible_cdp_unconfirmed_result_recovered") == 1
            and events.get("pending_visible_cdp_unconfirmed_member_consumed", 0) == 0
            and events.get("pending_visible_cdp_unconfirmed_member_deferred_for_promotion") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": result,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "second_result": second_after.get("result"),
            "runtime_payload": runtime_payload,
            "pushed": pushed,
            "event_counts": events,
            "reason": reason,
            "assertion": "owner result recovery must not complete unacked pending backlog supplements; they remain pending and are promoted for their own final reply",
        }


def cdp_route_doctor_check() -> dict[str, Any]:
    """Temp-only check for CDP route maintenance boundaries and route-local failure handling."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-doctor-", ignore_cleanup_errors=True) as temp_root:
        temp = Path(temp_root)
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": ["primary-cdp@im.wechat", "backup-cdp@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "codex_thread_id": "thread-primary-visible",
                "codex_cdp_host": "127.0.0.1",
                "codex_cdp_port": 1,
                "codex_cdp_script": str(temp / "missing_cdp_send.js"),
                "codex_cdp_start_script": str(temp / "missing-start.ps1"),
                "codex_cdp_process_discovery": False,
                "codex_cdp_runtime_state": False,
                "maintenance_deep_probe_allowlist": ["cdp_os_port", "cdp_route"],
                "mcp_session_gate_for_dispatch_enabled": False,
                "worker_dispatch_fallback_depth": 1,
            },
            "threads": {
                "default_id": "primary-route",
                "items": [
                    {
                        "id": "primary-route",
                        "name": "Primary Visible Route",
                        "description": "primary route for primary-cdp@im.wechat",
                        "aliases": [],
                        "thread_id": "thread-primary-visible",
                    },
                    {
                        "id": "backup-route",
                        "name": "Backup Route",
                        "description": "backup route for backup-cdp@im.wechat",
                        "aliases": [],
                        "thread_id": "thread-backup-app",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, "primary-cdp@im.wechat", "primary-route")
        set_active_thread(queue, "backup-cdp@im.wechat", "backup-route")
        primary = queue.enqueue(
            "primary cdp backlog",
            source="openclaw-weixin",
            external_user="primary-cdp@im.wechat",
            metadata={"msg_id": "cdp-doctor-primary", "receiver_account_id": "primary"},
        )
        backup = queue.enqueue(
            "backup should continue",
            source="openclaw-weixin",
            external_user="backup-cdp@im.wechat",
            metadata={"msg_id": "cdp-doctor-backup", "receiver_account_id": "backup1"},
        )
        primary_id = str(primary["id"])
        backup_id = str(backup["id"])

        from mobile_maintenance import doctor_report, repair_report

        doctor = doctor_report(queue, config)
        dry_repair = repair_report(queue, config, apply=False, include_reply_send=False)

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        dispatches: list[dict[str, Any]] = []
        status_acks: list[dict[str, Any]] = []

        def fake_poll_codex_result_cdp(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": False,
                "generationActive": False,
                "transient": True,
                "startup": {"ok": False, "reason": "codex_cdp_transport_not_ready"},
                "reason": "codex cdp is starting or unavailable",
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
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
                "listed": True,
                "listed_status": {"type": "idle"},
            }

        def fake_dispatch_to_codex(
            tasks: list[dict[str, Any]],
            thread_id: str,
            dispatch_config: dict[str, Any],
            _continuation: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            mode = str(dispatch_config.get("trigger", {}).get("delivery_mode") or "")
            task_ids = [str(task.get("id") or "") for task in tasks if str(task.get("id") or "")]
            dispatches.append({"mode": mode, "thread_id": thread_id, "task_ids": task_ids})
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-" + thread_id,
                "client_user_message_id": "batch-" + thread_id,
                "expected_task_ids": task_ids,
            }

        def fake_send_status_ack(
            _queue: MobileQueue,
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            event_type: str,
        ) -> dict[str, Any]:
            status_acks.append({"task_id": str(task.get("id") or ""), "event_type": event_type, "text": text})
            return {"ok": True, "mode": "test"}

        try:
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                worker_result = worker_once(queue, config, limit=5)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["send_status_ack"] = original_status_ack

        primary_after = queue.get_task(primary_id) or {}
        backup_after = queue.get_task(backup_id) or {}
        issue_codes = [
            str(item.get("code") or "")
            for item in ((doctor.get("diagnosis") or {}).get("issues") or [])
            if isinstance(item, dict)
        ]
        cdp_route = ((doctor.get("snapshot") or {}).get("cdp_route") or {})
        dry_actions = ((dry_repair.get("repair") or {}).get("actions") or [])
        ok = bool(
            doctor.get("ok") is False
            and "codex_cdp_unavailable" in issue_codes
            and cdp_route.get("layer") in {"transport_down", "send_script_missing", "startup_script_missing"}
            and cdp_route.get("primary_pending_count") == 1
            and dry_repair.get("apply") is False
            and all(action.get("code") != "schedule_due_reply_pending" or action.get("result", {}).get("skipped") for action in dry_actions)
            and worker_result.get("action") == "dispatched_waiting_result"
            and str(worker_result.get("thread_id") or "") == "thread-backup-app"
            and str(primary_after.get("status") or "") == "pending"
            and str(backup_after.get("status") or "") == "sent_to_codex"
            and dispatches == [{"mode": "codex-app-server", "thread_id": "thread-backup-app", "task_ids": [backup_id]}]
        )
        return {
            "ok": ok,
            "temp_only": True,
            "doctor_issue_codes": issue_codes,
            "cdp_route": cdp_route,
            "dry_repair": dry_repair.get("repair"),
            "worker_result": worker_result,
            "dispatches": dispatches,
            "status_acks": status_acks,
            "statuses": {
                primary_id: primary_after.get("status"),
                backup_id: backup_after.get("status"),
            },
            "assertion": "CDP unavailable is diagnosed without unsafe repair, probe failure stays primary-scoped, and backup app-server dispatch continues",
        }


def final_reply_visibility_check() -> dict[str, Any]:
    accepted_uncertain = {
        "ok": True,
        "attempts": [
            {
                "mode": "original",
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "weixin_ret": None,
                "stdout": {"ok": True, "deliveryAccepted": True, "messageId": "gateway-only"},
            }
        ],
        "final": {
            "ok": True,
            "delivery_accepted": True,
            "phone_visible_confirmed": False,
            "weixin_ret": None,
            "stdout": {"ok": True, "deliveryAccepted": True, "messageId": "gateway-only"},
        },
    }
    direct_uncertain = {
        "ok": True,
        "attempts": [
            {
                "mode": "original",
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "weixin_ret": None,
                "stdout": {"ok": True, "deliveryAccepted": True},
            }
        ],
        "final": {
            "ok": True,
            "delivery_accepted": True,
            "phone_visible_confirmed": False,
            "weixin_ret": None,
            "stdout": {"ok": True, "deliveryAccepted": True},
        },
    }
    confirmed_visible = {
        "ok": True,
        "attempts": [
            {
                "mode": "original",
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": True,
                "weixin_ret": None,
                "stdout": {"ok": True, "deliveryAccepted": True, "phoneVisibleConfirmed": True},
            }
        ],
        "final": {},
    }
    return {
        "ok": (
            not final_reply_phone_visible(accepted_uncertain)
            and not final_reply_phone_visible(direct_uncertain)
            and final_reply_phone_visible(confirmed_visible)
        ),
        "temp_only": True,
        "accepted_uncertain_visible": final_reply_phone_visible(accepted_uncertain),
        "direct_uncertain_visible": final_reply_phone_visible(direct_uncertain),
        "confirmed_visible": final_reply_phone_visible(confirmed_visible),
        "assertion": "sender acceptance without explicit phone visibility is not phone-visible; push_final_reply records it once without automatic resend",
        }


def visible_cdp_unconfirmed_multi_supplement_followup_check() -> dict[str, Any]:
    """Temp-only check that unverified CDP delivery keeps mixed supplement runtime intact."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-multi-supp-") as temp_root:
        temp = Path(temp_root)
        user = "visible-cdp-multi@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "accounts": {"users": {user: {"account_id": "primary"}}},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
                "account_onboarding_worker_sync_enabled": False,
            },
            "trigger": {
                "codex_thread_id": "visible-thread",
                "delivery_timeout_seconds": 1,
                "cooldown_seconds": 0,
                "worker_dispatch_attempts_per_cycle": 3,
                "active_recovery_max_sent_checks_per_cycle": 0,
                "visible_cdp_unverified_submission_attention_after_attempts": 5,
            },
            "threads": {
                "default_id": "primary-thread",
                "items": [{"id": "primary-thread", "name": "Primary Visible Thread", "thread_id": "configured-thread"}],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "primary-thread")
        owner = queue.enqueue("owner prompt", source="openclaw-weixin", external_user=user, external_conversation=user, metadata={"msg_id": "multi-1", "receiver_account_id": "primary"})
        supp_a = queue.enqueue("supplement A", source="openclaw-weixin", external_user=user, external_conversation=user, metadata={"msg_id": "multi-2", "receiver_account_id": "primary"})
        supp_b = queue.enqueue("supplement B", source="openclaw-weixin", external_user=user, external_conversation=user, metadata={"msg_id": "multi-3", "receiver_account_id": "primary"})
        owner_id = str(owner["id"])
        supp_a_id = str(supp_a["id"])
        supp_b_id = str(supp_b["id"])
        dispatched: list[dict[str, Any]] = []

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        mode = "no_evidence"

        def fake_poll_codex_result_cdp(_config: dict[str, Any], _baseline_key: str = "", *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "generationActive": False, "startup": {"ok": True}}

        def fake_dispatch_to_codex(tasks: list[dict[str, Any]], thread_id: str, dispatch_config: dict[str, Any], _continuation: dict[str, Any] | None = None) -> dict[str, Any]:
            owner_ids = [str(item) for item in dispatch_config.get("_delivery_group_result_owner_task_ids") or [] if str(item)]
            dispatched.append({"thread_id": thread_id, "task_ids": [str(task.get("id") or "") for task in tasks], "owner_ids": owner_ids})
            return {
                "ok": True,
                "delivery_accepted": True,
                "submission_confirmed": False,
                "submission_unconfirmed": True,
                "reason": "cdp_visible_input_unconfirmed_observing",
                "mode": "codex-cdp",
                "thread_id": thread_id,
                "turn_id": "turn-multi",
                "baseline_key": "baseline-multi",
                "client_user_message_id": "batch-multi",
                "expected_task_ids": owner_ids,
                "mobile_protocols": {task_id: mobile_protocol(task_id, "batch-multi") for task_id in owner_ids},
                "desktop_visible": {"confirmed": mode == "marker_evidence", "body_has_exact_prompt": False},
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                first_result = worker_once(queue, config, limit=3)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        ids = [owner_id, supp_a_id, supp_b_id]
        statuses = {tid: (queue.get_task(tid) or {}).get("status") for tid in ids}
        context = {tid: task_is_supplement_context(queue, tid) for tid in ids}
        runtime_items = bridge_supplement_payload_for_task(queue, supp_a_id)[1].get("items") or []
        item_ids = [str(item.get("message_id") or "") for item in runtime_items if isinstance(item, dict)]
        with queue.session() as db:
            event_counts = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    "SELECT event_type, COUNT(*) AS n FROM mobile_events WHERE task_id IN (?,?,?) GROUP BY event_type",
                    tuple(ids),
                ).fetchall()
            }
        ok = bool(
            first_result.get("action") == "dispatched_waiting_result"
            and statuses.get(owner_id) == "sent_to_codex"
            and all(statuses.get(tid) == "pending" for tid in [supp_a_id, supp_b_id])
            and all(context.get(tid) for tid in [supp_a_id, supp_b_id])
            and item_ids == [supp_a_id, supp_b_id]
            and event_counts.get("pending_backlog_supplement_pending_published", 0) == 2
            and event_counts.get("delivery_group_member_released", 0) == 0
            and len(dispatched) == 1
            and dispatched[-1].get("owner_ids") == [owner_id]
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first_result": first_result,
            "statuses": statuses,
            "supplement_context": context,
            "runtime_item_ids": item_ids,
            "dispatched": dispatched,
            "event_counts": event_counts,
            "assertion": "unverified accepted CDP delivery keeps pending supplements attached as supplement context without dropping remaining items or redispatching them as owners",
        }


def pending_visible_cdp_multi_supplement_consumption_check() -> dict[str, Any]:
    """Facade for moved supplement regression check."""
    return run_supplement_regression_check("pending_visible_cdp_multi_supplement_consumption_check", globals())


def visible_cdp_repeated_unconfirmed_attention_check() -> dict[str, Any]:
    """Temp-only check that repeated unverified visible-CDP submissions enter attention state without route switching."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-cdp-attention-") as temp_root:
        temp = Path(temp_root)
        user = "visible-cdp-attention@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {
                "account_id": "primary",
                "phone_status_ack_events": [],
                "account_onboarding_worker_sync_enabled": False,
            },
            "trigger": {
                "codex_thread_id": "visible-thread",
                "cooldown_seconds": 0,
                "worker_dispatch_attempts_per_cycle": 3,
                "active_recovery_max_sent_checks_per_cycle": 0,
                "visible_cdp_unverified_submission_attention_after_attempts": 2,
                "visible_cdp_unverified_submission_attention_retry_seconds": 60,
                "delivery_retry_seconds": 0,
            },
            "threads": {
                "default_id": "primary-thread",
                "items": [{"id": "primary-thread", "name": "Primary Visible Thread", "thread_id": "configured-thread"}],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "primary-thread")
        task = queue.enqueue("owner prompt", source="openclaw-weixin", external_user=user, external_conversation=user, metadata={"msg_id": "attention-1", "receiver_account_id": "primary"})
        task_id = str(task["id"])
        dispatched: list[dict[str, Any]] = []

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]

        def fake_poll_codex_result_cdp(_config: dict[str, Any], _baseline_key: str = "", *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "generationActive": False, "startup": {"ok": True}}

        def fake_dispatch_to_codex(tasks: list[dict[str, Any]], thread_id: str, dispatch_config: dict[str, Any], _continuation: dict[str, Any] | None = None) -> dict[str, Any]:
            owner_ids = [str(item) for item in dispatch_config.get("_delivery_group_result_owner_task_ids") or [] if str(item)]
            dispatched.append({"thread_id": thread_id, "task_ids": [str(task.get("id") or "") for task in tasks], "owner_ids": owner_ids})
            return {
                "ok": True,
                "delivery_accepted": True,
                "submission_unconfirmed": True,
                "reason": "cdp_visible_input_unconfirmed_observing",
                "mode": "codex-cdp",
                "thread_id": thread_id,
                "turn_id": "turn-attention",
                "baseline_key": "baseline-attention",
                "client_user_message_id": "batch-attention",
                "expected_task_ids": owner_ids,
                "mobile_protocols": {owner_id: mobile_protocol(owner_id, "batch-attention") for owner_id in owner_ids},
                "desktop_visible": {"confirmed": False, "body_has_exact_prompt": False},
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        try:
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                first = worker_once(queue, config, limit=3)
                clear_delivery_retry(queue, [task_id])
                second = worker_once(queue, config, limit=3)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        recovery = get_thread_recovery(queue, task_id)
        retry = get_delivery_retry(queue, task_id)
        task_after = queue.get_task(task_id) or {}
        attention_payload = latest_task_event_payload(queue, task_id, "cdp_visible_submission_needs_attention")
        ok = bool(
            first.get("action") == "dispatched_waiting_result"
            and second.get("action") == "idle"
            and recovery.get("active") is False
            and retry.get("active") is False
            and task_after.get("status") == "sent_to_codex"
            and attention_payload == {}
            and len(dispatched) == 1
            and dispatched[0].get("thread_id") == "visible-thread"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first": first,
            "second": second,
            "thread_recovery": recovery,
            "delivery_retry": retry,
            "task_status": task_after.get("status"),
            "attention_payload": attention_payload,
            "dispatched": dispatched,
            "assertion": "accepted visible-CDP submissions without prompt or marker evidence remain owned-result waits; visible evidence is diagnostic and must not cause duplicate dispatch or route switching",
        }


def final_reply_visibility_unconfirmed_check() -> dict[str, Any]:
    """Temp-only check that accepted final replies are recorded once without relying on visibility."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-final-visibility-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "openclaw": {
                "account_id": "backup1",
                "reply_pending_context_retry_limit_per_cycle": 5,
                "reply_pending_context_retry_seconds": 10,
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }
        now = datetime.now(timezone.utc).isoformat()
        task_id = "visibleunknown-final"
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
                    "user@im.wechat",
                    "",
                    "/ask",
                    "probe",
                    "L1",
                    "done",
                    "same final reply",
                    "",
                    "backup1",
                    json.dumps({"context_token": "ctx"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        original_reply = globals()["reply_to_weixin_with_fallbacks"]

        def fake_reply_to_weixin_with_fallbacks(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "attempts": [
                    {
                        "ok": True,
                        "delivery_accepted": True,
                        "phone_visible_confirmed": False,
                        "weixin_ret": None,
                        "stdout": {"ok": True, "deliveryAccepted": True, "messageId": "accepted-only"},
                    }
                ],
                "final": {
                    "ok": True,
                    "delivery_accepted": True,
                    "phone_visible_confirmed": False,
                    "weixin_ret": None,
                    "stdout": {"ok": True, "deliveryAccepted": True, "messageId": "accepted-only"},
                },
            }

        try:
            globals()["reply_to_weixin_with_fallbacks"] = fake_reply_to_weixin_with_fallbacks
            result = push_final_reply(queue, queue.get_task(task_id) or {}, "same final reply", config)
            queue.mark_pushed(task_id, bool(result.get("ok")), json.dumps(result, ensure_ascii=False))
        finally:
            globals()["reply_to_weixin_with_fallbacks"] = original_reply

        task = queue.get_task(task_id) or {}
        pending_retry = process_pending_reply_context_retries(queue, config, limit=5)
        schedule_retry = schedule_waiting_context_replies(
            queue,
            config,
            "user@im.wechat",
            "backup1",
            "fresh-context",
            "trigger1",
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
                    (task_id,),
                ).fetchall()
            }
        ok = bool(
            result.get("ok")
            and result.get("phone_visible_confirmed") is False
            and task.get("push_status") == "pushed_to_wecom"
            and task.get("pushed_at")
            and not task_event_exists(queue, task_id, "final_reply_waiting_weixin_context")
            and events.get("final_reply_weixin_accepted") == 1
            and events.get("final_reply_visibility_unconfirmed", 0) == 0
            and pending_retry.get("scheduled") == 0
            and schedule_retry.get("scheduled") == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "task": {
                "status": task.get("status"),
                "push_status": task.get("push_status"),
                "pushed_at": task.get("pushed_at"),
            },
            "events": events,
            "pending_retry": pending_retry,
            "schedule_retry": schedule_retry,
            "assertion": "accepted final replies are recorded once and not auto-resent without depending on visibility confirmation",
        }


def reply_send_idempotency_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-reply-idempotency-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "reply-idempotency-task"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    message_fingerprint, risk_level, status, result, receiver_account_id,
                    metadata_json, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "idempotency@im.wechat",
                    "",
                    "/ask",
                    "prompt",
                    "reply-idempotency-fingerprint",
                    "L1",
                    "done",
                    "final text",
                    "backup2",
                    "{}",
                    now,
                    now,
                ),
            )
        config = {"queue": {"db_path": str(temp / "queue.db")}, "safety": {"shadow_mode": False}}
        first_ack = reserve_status_ack_send(queue, task_id, "status_ack_waiting", "仍在处理，已等待 60 秒…")
        second_ack = reserve_status_ack_send(queue, task_id, "status_ack_waiting", "已进入 Codex 处理，已等待 60 秒…")
        original_spawn = globals()["spawn_cli_background"]
        spawned: list[dict[str, Any]] = []

        def fake_spawn_cli_background(
            _queue: MobileQueue,
            task_arg: str,
            _config: dict[str, Any],
            command: list[str],
            _label: str,
            event_prefix: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            spawned.append({"task_id": task_arg, "command": list(command), "event_prefix": event_prefix, "payload": dict(payload)})
            _queue.add_event("wecom", f"{event_prefix}_spawned", {"ok": True, "pid": 1234, **payload}, task_arg)
            return {"ok": True, "async": True, "spawned": True, "pid": 1234}

        try:
            globals()["spawn_cli_background"] = fake_spawn_cli_background
            first_final = push_final_reply_async(queue, queue.get_task(task_id) or {}, "final text", config)
            second_final = push_final_reply_async(queue, queue.get_task(task_id) or {}, "final text", config)
        finally:
            globals()["spawn_cli_background"] = original_spawn
        queue.add_event("wecom", "final_reply_weixin_accepted", {"ok": True}, task_id)
        third_final = push_final_reply_async(queue, queue.get_task(task_id) or {}, "final text", config)
        owned_poll = {
            "mode": "codex-app-server",
            "turn_id": "turn-idempotency",
            "matched_turn_id": "turn-idempotency",
            "ownership": {
                "matched_task_id": task_id,
                "matched_result_code": "result-code-idempotency",
            },
        }
        first_owned_consume = reserve_owned_result_consume(queue, task_id, owned_poll)
        second_owned_consume = reserve_owned_result_consume(queue, task_id, {**owned_poll, "mode": "codex-thread-history"})
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
        checks = {
            "waiting_ack_text_variants_share_lease": {
                "ok": bool(first_ack.get("reserved") and second_ack.get("duplicate")),
                "first_ack": first_ack,
                "second_ack": second_ack,
            },
            "final_reply_async_single_spawn": {
                "ok": bool(first_final.get("spawned") and second_final.get("suppressed") and len(spawned) == 1),
                "first_final": first_final,
                "second_final": second_final,
                "spawned": spawned,
                "events": events,
            },
            "accepted_final_reply_suppresses_later_spawn": {
                "ok": bool(third_final.get("suppressed") and len(spawned) == 1),
                "third_final": third_final,
            },
            "owned_result_consume_single_winner": {
                "ok": bool(first_owned_consume.get("reserved") and second_owned_consume.get("duplicate")),
                "first_owned_consume": first_owned_consume,
                "second_owned_consume": second_owned_consume,
            },
        }
        failed = {name: item for name, item in checks.items() if not item.get("ok")}
        return {
            "schema": "reply-send-idempotency-check/v1",
            "ok": not failed,
            "checks": checks,
            "failed": failed,
            "temp_only": True,
            "assertion": "status acks, owned-result consumption, and final replies use task-level leases so concurrent recovery cycles cannot duplicate completion or Weixin sends",
        }


def final_reply_media_text_split_check() -> dict[str, Any]:
    """Temp-only check that text+media final replies are sent and recorded as separate parts."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-final-media-text-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        media = temp / "sample.wav"
        media.write_bytes(b"RIFF----WAVE")
        config = {
            "openclaw": {
                "account_id": "backup1",
                "final_reply_retry_delays_seconds": [],
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }
        now = datetime.now(timezone.utc).isoformat()
        task_id = "media-text-final"
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
                    "user@im.wechat",
                    "",
                    "/ask",
                    "send file",
                    "L1",
                    "done",
                    "done",
                    "",
                    "backup1",
                    json.dumps({"context_token": "ctx"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        calls: list[dict[str, Any]] = []
        original_reply = globals()["reply_to_weixin"]

        def fake_reply_to_weixin(
            _task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            send: bool,
            media: str | None = None,
        ) -> dict[str, Any]:
            calls.append({"text": text, "send": send, "media": str(media or "")})
            return {
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "weixin_ret": None,
                "weixin_errcode": None,
                "weixin_errmsg": "",
                "business_error": "",
                "dry_run": not send,
                "media": str(media or ""),
                "returncode": 0,
                "stdout": {
                    "ok": True,
                    "deliveryAccepted": True,
                    "phoneVisibleConfirmed": False,
                    "textChars": len(text),
                    "mediaPresent": bool(media),
                    "transport": "direct-ilink-media" if media else "direct-ilink",
                    "messageId": "media-msg" if media else "text-msg",
                },
                "stderr": "",
            }

        try:
            globals()["reply_to_weixin"] = fake_reply_to_weixin
            result = push_final_reply(queue, queue.get_task(task_id) or {}, "文字说明", config, media=str(media))
            retry_result = push_final_reply(queue, queue.get_task(task_id) or {}, "文字说明", config, media=str(media))
        finally:
            globals()["reply_to_weixin"] = original_reply

        task = queue.get_task(task_id) or {}
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
        text_calls = [call for call in calls if not call.get("media")]
        media_calls = [call for call in calls if call.get("media")]
        ok = bool(
            result.get("ok")
            and result.get("split_delivery")
            and events.get("final_reply_text_accepted") == 1
            and events.get("final_reply_media_accepted") == 1
            and events.get("final_reply_weixin_accepted") == 1
            and len(text_calls) == 1
            and len(media_calls) == 1
            and media_calls[0].get("text") == ""
            and retry_result.get("ok")
            and retry_result.get("already_complete")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "retry_result": retry_result,
            "calls": calls,
            "events": events,
            "task": {
                "status": task.get("status"),
                "push_status": task.get("push_status"),
                "pushed_at": task.get("pushed_at"),
            },
            "assertion": "final replies with both text and media send separate text/media messages and do not resend accepted parts on retry",
        }


def final_reply_media_ret2_governance_check() -> dict[str, Any]:
    """Temp-only check for ASCII media spool and ret=-2 attachment governance."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-media-ret2-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        media = temp / "热工理论刷题工具_离线版_20260703.zip"
        media.write_bytes(b"PK\x03\x04sample zip payload")
        config = {
            "openclaw": {
                "account_id": "backup2",
                "final_reply_retry_delays_seconds": [],
            },
            "queue": {"db_path": str(temp / "queue.db")},
        }
        now = datetime.now(timezone.utc).isoformat()
        task_id = "media-ret2-final"
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
                    "user@im.wechat",
                    "",
                    "/ask",
                    "send zip",
                    "L1",
                    "done",
                    "done",
                    "",
                    "backup2",
                    json.dumps({"context_token": "ctx"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        calls: list[dict[str, Any]] = []
        original_reply = globals()["reply_to_weixin"]

        def fake_reply_to_weixin(
            _task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            send: bool,
            media: str | None = None,
        ) -> dict[str, Any]:
            calls.append({"text": text, "send": send, "media": str(media or "")})
            if media:
                return {
                    "ok": False,
                    "delivery_accepted": False,
                    "phone_visible_confirmed": False,
                    "weixin_ret": -2,
                    "weixin_errcode": None,
                    "weixin_errmsg": "",
                    "business_error": "weixin_ret_-2",
                    "dry_run": not send,
                    "media": str(media or ""),
                    "returncode": 1,
                    "stdout": {
                        "ok": False,
                        "deliveryAccepted": False,
                        "phoneVisibleConfirmed": False,
                        "mediaPresent": True,
                        "transport": "gateway-media",
                        "weixinRet": -2,
                        "gatewaySubmitted": True,
                        "response": {
                            "deliveryAccepted": False,
                            "gatewaySubmitted": True,
                            "weixinRet": -2,
                        },
                    },
                    "stderr": "",
                }
            return {
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "weixin_ret": None,
                "weixin_errcode": None,
                "weixin_errmsg": "",
                "business_error": "",
                "dry_run": not send,
                "media": "",
                "returncode": 0,
                "stdout": {"ok": True, "deliveryAccepted": True, "messageId": "text-msg"},
                "stderr": "",
            }

        try:
            globals()["reply_to_weixin"] = fake_reply_to_weixin
            result = push_final_reply(queue, queue.get_task(task_id) or {}, "附件说明", config, media=str(media))
        finally:
            globals()["reply_to_weixin"] = original_reply

        task = queue.get_task(task_id) or {}
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
        media_calls = [call for call in calls if call.get("media")]
        prepared_media = media_calls[0].get("media") if media_calls else ""
        prepared_name = Path(prepared_media).name if prepared_media else ""
        ok = bool(
            not result.get("ok")
            and result.get("recoverable")
            and result.get("source_reason") == "media_sendmessage_ret_-2"
            and task.get("push_status") == "reply_pending"
            and events.get("final_reply_text_accepted") == 1
            and events.get("final_reply_media_unconfirmed") == 1
            and prepared_media
            and Path(prepared_media).exists()
            and is_ascii_safe_filename(prepared_name)
            and prepared_media != str(media)
            and (
                isinstance(result.get("detail"), dict)
                and classify_media_send_failure(result["detail"].get("reply", {})).get("category") == "media_sendmessage_ret_-2"
            )
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "calls": calls,
            "prepared_media": prepared_media,
            "prepared_name": prepared_name,
            "events": events,
            "task": {
                "status": task.get("status"),
                "push_status": task.get("push_status"),
                "pushed_at": task.get("pushed_at"),
            },
            "assertion": "non-ASCII ZIP names are copied to ASCII outbound media spool, ret=-2 remains reply_pending/media_unconfirmed, and gatewaySubmitted is not treated as phone delivery",
        }


def final_reply_active_owner_guard_check() -> dict[str, Any]:
    """Temp-only check that manual final-reply cannot hijack an active owner."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-final-owner-guard-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            ("active-owner", "sent_to_codex", "", ""),
            ("done-owner", "done", "completed result", ""),
        ]
        with queue.session() as db:
            for task_id, status, result, push_status in rows:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, push_status, receiver_account_id,
                        metadata_json, created_at, updated_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "user@im.wechat",
                        "",
                        "/ask",
                        f"test {task_id}",
                        sha256_text(f"test {task_id}"),
                        sha256_text(f"fingerprint {task_id}"),
                        "L1",
                        status,
                        result,
                        push_status,
                        "primary",
                        "{}",
                        now,
                        now,
                    ),
                )
        active = queue.get_task("active-owner") or {}
        done = queue.get_task("done-owner") or {}
        active_guard = guard_final_reply_owner_ready(queue, active)
        done_guard = guard_final_reply_owner_ready(queue, done)
        with queue.session() as db:
            guarded_events = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id='active-owner' AND event_type='final_reply_active_owner_guarded'
                """
            ).fetchone()["n"]
        ok = bool(
            active_guard
            and active_guard.get("reason") == "final_reply_owner_not_complete"
            and done_guard is None
            and guarded_events == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "active_guard": active_guard,
            "done_guard": done_guard,
            "guarded_events": guarded_events,
            "assertion": "final-reply rejects active owners without result and still permits completed owner replies",
        }


def failed_result_visibility_unconfirmed_recovery_check() -> dict[str, Any]:
    """Temp-only check recovered failed-result replies obey visibility-unconfirmed no-resend policy."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-failed-result-visible-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        task_id = "failed-visible-result"
        now = datetime.now(timezone.utc).isoformat()
        config = {
            "openclaw": {
                "account_id": "primary",
                "reply_pending_context_retry_limit_per_cycle": 5,
                "reply_pending_context_retry_seconds": 10,
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
                    "user@im.wechat",
                    "user@im.wechat",
                    "/ask",
                    "probe",
                    "L1",
                    "failed",
                    "durable recovered answer",
                    "",
                    "primary",
                    json.dumps({"context_token": "ctx"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        queue.add_event("local", "failure_close_owned_result_recovered", {"seed": "durable result"}, task_id)
        recovery = recover_failed_tasks_with_result_for_reply(queue, config, apply=True, task_id=task_id, limit=5)

        original_reply = globals()["reply_to_weixin_with_fallbacks"]

        def fake_reply_to_weixin_with_fallbacks(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "attempts": [
                    {
                        "ok": True,
                        "delivery_accepted": True,
                        "phone_visible_confirmed": False,
                        "stdout": {"ok": True, "deliveryAccepted": True, "phoneVisibleConfirmed": False},
                    }
                ],
                "final": {
                    "ok": True,
                    "delivery_accepted": True,
                    "phone_visible_confirmed": False,
                    "stdout": {"ok": True, "deliveryAccepted": True, "phoneVisibleConfirmed": False},
                },
            }

        try:
            globals()["reply_to_weixin_with_fallbacks"] = fake_reply_to_weixin_with_fallbacks
            task_for_push = queue.get_task(task_id) or {}
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET push_status='reply_sending', updated_at=?
                    WHERE id=? AND push_status IN ('reply_pending','reply_retrying','push_failed')
                    """,
                    (datetime.now(timezone.utc).isoformat(), task_id),
                )
            push = push_final_reply(queue, task_for_push, str(task_for_push.get("result") or ""), config)
            if push.get("reason") == "waiting_weixin_context":
                queue.mark_reply_pending(task_id, json.dumps(push, ensure_ascii=False))
            else:
                queue.mark_pushed(task_id, bool(push.get("ok")), json.dumps(push, ensure_ascii=False))
        finally:
            globals()["reply_to_weixin_with_fallbacks"] = original_reply

        task_after = queue.get_task(task_id) or {}
        retry = process_pending_reply_context_retries(queue, config, limit=5)
        with queue.session() as db:
            event_counts = {
                row["event_type"]: int(row["n"])
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
            recovery.get("recovered_count") == 1
            and push.get("ok") is True
            and push.get("phone_visible_confirmed") is False
            and task_after.get("status") == "pushed_to_wecom"
            and task_after.get("push_status") == "pushed_to_wecom"
            and event_counts.get("push_result", 0) == 1
            and retry.get("scheduled") == 0
            and retry.get("waiting_context") == 0
            and retry.get("missing_context") == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "push": push,
            "task": {
                "status": task_after.get("status"),
                "push_status": task_after.get("push_status"),
                "error": task_after.get("error"),
            },
            "event_counts": event_counts,
            "retry": retry,
            "assertion": "failed tasks recovered through durable result use the normal final-reply visibility policy and are not auto-resent when accepted but unconfirmed",
        }


def push_failed_ret2_fresh_context_recovery_check() -> dict[str, Any]:
    """Temp-only check that ret=-2 push_failed backlog is retried only by a fresh inbound context."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-pushfailed-ret2-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        account_id = "backup1"
        external_user = "user@im.wechat"
        task_id = "pushfailed-ret2"
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
                    "push_failed",
                    "final reply",
                    "push_failed",
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

        def fake_push_final_reply_async(_queue: MobileQueue, task: dict[str, Any], text: str, _config: dict[str, Any], media: str | None = None) -> dict[str, Any]:
            spawned.append({"task_id": str(task.get("id") or ""), "text": text, "media": str(media or "")})
            return {"ok": True, "async": True, "spawned": True, "pid": 1}

        try:
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            automatic = process_pending_reply_context_retries(queue, config, limit=5)
            after_automatic = queue.get_task(task_id) or {}
            inbound = schedule_waiting_context_replies(queue, config, external_user, account_id, "fresh-context", "new-inbound-task")
            after_inbound = queue.get_task(task_id) or {}
        finally:
            globals()["push_final_reply_async"] = original_push

        ok = bool(
            automatic.get("scheduled") == 0
            and after_automatic.get("push_status") == "push_failed"
            and inbound.get("scheduled") == 1
            and inbound.get("task_ids") == [task_id]
            and after_inbound.get("push_status") == "reply_retrying"
            and queue.runtime_get(task_context_token_key(task_id)) == "fresh-context"
            and len(spawned) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "automatic_retry": automatic,
            "inbound_retry": inbound,
            "spawned": spawned,
            "push_status_after_automatic": after_automatic.get("push_status"),
            "push_status_after_inbound": after_inbound.get("push_status"),
            "token_after": queue.runtime_get(task_context_token_key(task_id)),
            "assertion": "ret=-2 push_failed backlog is not timer-retried but is recovered when a same-account fresh inbound context arrives",
        }


def weixin_errcode_session_timeout_check() -> dict[str, Any]:
    """Temp-only check that errcode=-14 is not treated as successful delivery."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-errcode-timeout-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        account_id = "backup1"
        external_user = "user@im.wechat"
        task_id = "errcode-session-timeout"
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
                    "",
                    account_id,
                    json.dumps({"context_token": "old-context"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

        original_reply = globals()["reply_to_weixin_with_fallbacks"]

        def fake_reply_to_weixin_with_fallbacks(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            attempt = {
                "ok": False,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "weixin_ret": None,
                "weixin_errcode": -14,
                "weixin_errmsg": "session timeout",
                "business_error": "weixin_errcode_-14",
                "stdout": {
                    "ok": True,
                    "deliveryAccepted": True,
                    "phoneVisibleConfirmed": False,
                    "response": {"errcode": -14, "errmsg": "session timeout"},
                },
            }
            return {"ok": False, "attempts": [attempt], "final": attempt}

        try:
            globals()["reply_to_weixin_with_fallbacks"] = fake_reply_to_weixin_with_fallbacks
            result = push_final_reply(queue, queue.get_task(task_id) or {}, "final reply", config)
        finally:
            globals()["reply_to_weixin_with_fallbacks"] = original_reply

        task = queue.get_task(task_id) or {}
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
            result.get("ok") is False
            and result.get("reason") == "waiting_weixin_context"
            and result.get("source_reason") == "sendmessage_errcode_-14"
            and task.get("status") == "done"
            and task.get("push_status") == "reply_pending"
            and not task.get("pushed_at")
            and events.get("final_reply_waiting_weixin_context") == 1
            and events.get("final_reply_visibility_unconfirmed", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "task": {
                "status": task.get("status"),
                "push_status": task.get("push_status"),
                "pushed_at": task.get("pushed_at"),
            },
            "events": events,
            "assertion": "deliveryAccepted with response.errcode=-14 is waiting_weixin_context, not pushed_to_wecom",
        }

_CHECKS = {
    "cdp_visible_delivery_check": cdp_visible_delivery_check,
    "visible_cdp_unconfirmed_observation_check": visible_cdp_unconfirmed_observation_check,
    "pending_visible_cdp_result_recovery_check": pending_visible_cdp_result_recovery_check,
    "cdp_route_doctor_check": cdp_route_doctor_check,
    "final_reply_visibility_check": final_reply_visibility_check,
    "visible_cdp_unconfirmed_multi_supplement_followup_check": visible_cdp_unconfirmed_multi_supplement_followup_check,
    "pending_visible_cdp_multi_supplement_consumption_check": pending_visible_cdp_multi_supplement_consumption_check,
    "visible_cdp_repeated_unconfirmed_attention_check": visible_cdp_repeated_unconfirmed_attention_check,
    "final_reply_visibility_unconfirmed_check": final_reply_visibility_unconfirmed_check,
    "reply_send_idempotency_check": reply_send_idempotency_check,
    "final_reply_media_text_split_check": final_reply_media_text_split_check,
    "final_reply_media_ret2_governance_check": final_reply_media_ret2_governance_check,
    "final_reply_active_owner_guard_check": final_reply_active_owner_guard_check,
    "failed_result_visibility_unconfirmed_recovery_check": failed_result_visibility_unconfirmed_recovery_check,
    "push_failed_ret2_fresh_context_recovery_check": push_failed_ret2_fresh_context_recovery_check,
    "weixin_errcode_session_timeout_check": weixin_errcode_session_timeout_check,
}
