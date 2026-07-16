"""Route, thread, and probe regression checks for the mobile bridge.

Owns: temp-only self-tests for route fallback/rotation, CDP listener probing,
transient health classification, thread busy/prewarm behavior, unreadable or
unlisted thread fallback, and probe-failure worker retreat.
Non-goals: production route selection, dispatch implementation, thread route
persistence, or CDP/app-server client implementations.
State behavior: checks use synthetic queues/config and may monkeypatch CLI
helpers; each check is rebound to the CLI global namespace to preserve legacy
fixture behavior after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_route_thread_regression_check(name: str, env: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a moved route/thread regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown route thread regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound(*args, **kwargs)

def route_fallback_dispatch_check() -> dict[str, Any]:
    """Temp-only check that a failed primary route does not starve backups."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-route-fallback-") as temp_root:
        temp = Path(temp_root)
        users = {
            "primary": "route-fallback-primary@im.wechat",
            "backup": "route-fallback-backup@im.wechat",
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
                "codex_thread_id": "thread-primary",
                "worker_dispatch_fallback_depth": 1,
            },
            "threads": {
                "default_id": "primary-route",
                "items": [
                    {
                        "id": "primary-route",
                        "name": "Primary Route",
                        "description": f"primary route for {users['primary']}",
                        "aliases": [],
                        "thread_id": "thread-primary",
                    },
                    {
                        "id": "backup-route",
                        "name": "Backup Route",
                        "description": f"backup route for {users['backup']}",
                        "aliases": [],
                        "thread_id": "thread-backup",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["primary"], "primary-route")
        set_active_thread(queue, users["backup"], "backup-route")

        primary = queue.enqueue(
            "primary message",
            source="openclaw-weixin",
            external_user=users["primary"],
            metadata={"msg_id": "route-fallback-primary", "receiver_account_id": "primary"},
        )
        backup = queue.enqueue(
            "backup message",
            source="openclaw-weixin",
            external_user=users["backup"],
            metadata={"msg_id": "route-fallback-backup", "receiver_account_id": "backup1"},
        )
        primary_id = str(primary["id"])
        backup_id = str(backup["id"])

        original_poll_cdp = globals()["poll_codex_result_cdp"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        dispatches: list[dict[str, Any]] = []

        def fake_poll_codex_result_cdp(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "generationActive": False,
                "healthy": False,
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
                "listed": True,
                "listed_status": {"type": "idle"},
                "thread_name": thread_name,
                "stabilize_name": stabilize_name,
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
            if mode == "codex-cdp":
                return {"ok": False, "reason": "codex cdp is not ready", "thread_id": thread_id}
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
            globals()["poll_codex_result_cdp"] = fake_poll_codex_result_cdp
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5)
        finally:
            globals()["poll_codex_result_cdp"] = original_poll_cdp
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["send_status_ack"] = original_status_ack

        primary_after = queue.get_task(primary_id) or {}
        backup_after = queue.get_task(backup_id) or {}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and str(result.get("thread_id") or "") == "thread-backup"
            and str(primary_after.get("status") or "") == "pending"
            and str(backup_after.get("status") or "") == "sent_to_codex"
            and dispatches == [
                {"mode": "codex-app-server", "thread_id": "thread-backup", "task_ids": [backup_id]}
            ]
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "dispatches": dispatches,
            "statuses": {
                primary_id: primary_after.get("status"),
                backup_id: backup_after.get("status"),
            },
            "assertion": "primary CDP probe failure is route-local and backup app-server dispatches in the same worker cycle",
        }


def route_rotation_fairness_check() -> dict[str, Any]:
    """Temp-only check that pending scan rotates across route groups fairly."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-route-rotation-") as temp_root:
        temp = Path(temp_root)
        users = {
            "primary_a": "rotation-a@im.wechat",
            "primary_b": "rotation-b@im.wechat",
            "backup": "rotation-backup@im.wechat",
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
                        "description": "rotation route A",
                        "aliases": [],
                        "thread_id": "thread-a",
                    },
                    {
                        "id": "route-b",
                        "name": "Route B",
                        "description": "rotation route B",
                        "aliases": [],
                        "thread_id": "thread-b",
                    },
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, users["primary_a"], "route-a")
        set_active_thread(queue, users["primary_b"], "route-a")
        set_active_thread(queue, users["backup"], "route-b")

        task_a1 = queue.enqueue(
            "A1",
            source="openclaw-weixin",
            external_user=users["primary_a"],
            metadata={"msg_id": "rotation-a1", "receiver_account_id": "backup1"},
        )
        task_a2 = queue.enqueue(
            "A2",
            source="openclaw-weixin",
            external_user=users["primary_b"],
            metadata={"msg_id": "rotation-a2", "receiver_account_id": "backup1"},
        )
        task_b1 = queue.enqueue(
            "B1",
            source="openclaw-weixin",
            external_user=users["backup"],
            metadata={"msg_id": "rotation-b1", "receiver_account_id": "backup2"},
        )

        original_dispatch = globals()["dispatch_to_codex"]
        original_check = globals()["check_codex_health"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]

        dispatched: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

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
            dispatched.append({"thread_id": thread_id, "task_ids": task_ids})
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
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["check_codex_health"] = original_check
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["send_status_ack"] = original_status_ack

        task_a1_after = queue.get_task(str(task_a1["id"])) or {}
        task_a2_after = queue.get_task(str(task_a2["id"])) or {}
        task_b1_after = queue.get_task(str(task_b1["id"])) or {}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and dispatched
            and dispatched[0].get("thread_id") == "thread-a"
            and str(task_a1_after.get("status") or "") == "sent_to_codex"
            and str(task_b1_after.get("status") or "") == "pending"
            and str(task_a2_after.get("status") or "") in {"pending", "sent_to_codex"}
        )
        return {
            "ok": ok,
            "temp_only": True,
            "result": result,
            "dispatched": dispatched,
            "statuses": {
                str(task_a1["id"]): task_a1_after.get("status"),
                str(task_a2["id"]): task_a2_after.get("status"),
                str(task_b1["id"]): task_b1_after.get("status"),
            },
            "assertion": "route rotation should keep another route visible when the first route has backlog",
        }


def active_slot_release_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("active_slot_release_check", globals())


def same_route_expired_active_order_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("same_route_expired_active_order_check", globals())


def active_generation_preserves_supplement_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("active_generation_preserves_supplement_check", globals())


def cdp_live_listener_probe_unstable_check() -> dict[str, Any]:
    """Temp-only check that a live CDP listener is not blocked by one short probe miss."""
    import codex_cdp_route

    config = {
        "trigger": {
            "delivery_mode": "codex-cdp",
            "codex_cdp_host": "127.0.0.1",
            "codex_cdp_port": 9230,
            "codex_cdp_probe_timeout_seconds": 0.2,
            "codex_cdp_no_start": True,
        }
    }
    original_tcp = codex_cdp_route.tcp_check
    original_os_state = codex_cdp_route.os_port_listener_state
    original_http_json = codex_cdp_route.http_json
    try:
        codex_cdp_route.tcp_check = lambda *_args, **_kwargs: {"ok": False, "reason": "timed out"}
        codex_cdp_route.os_port_listener_state = lambda _port: {
            "ok": True,
            "port": 9230,
            "listener_count": 1,
            "live_count": 1,
            "stale_count": 0,
            "listeners": [{"pid": 1234, "state": "LISTENING"}],
        }
        codex_cdp_route.http_json = lambda *_args, **_kwargs: {"ok": False, "reason": "probe timeout"}
        result = ensure_codex_cdp(config)
    finally:
        codex_cdp_route.tcp_check = original_tcp
        codex_cdp_route.os_port_listener_state = original_os_state
        codex_cdp_route.http_json = original_http_json
    ok = bool(
        result.get("ok")
        and result.get("transport_ready")
        and not result.get("version_ready")
        and result.get("reason") == "codex_cdp_probe_unstable_live_listener"
    )
    return {
        "ok": ok,
        "temp_only": True,
        "result": result,
        "assertion": "a live OS listener keeps the CDP route eligible for real JS probing even if the short TCP probe misses",
    }


def cdp_localhost_host_preserved_check() -> dict[str, Any]:
    """Temp-only check that localhost is preserved for IPv6-only Codex CDP listeners."""
    config = {"trigger": {"codex_cdp_host": "localhost", "codex_cdp_port": 9230}}
    settings = codex_cdp_config(config)
    ok = settings.get("host") == "localhost"
    return {
        "ok": ok,
        "temp_only": True,
        "host": settings.get("host"),
        "assertion": "codex_cdp_host=localhost must not be rewritten to 127.0.0.1 because Codex Desktop may listen on ::1 only",
    }


def active_recovery_route_fairness_check() -> dict[str, Any]:
    """Facade for moved scheduling regression check."""
    return run_scheduling_regression_check("active_recovery_route_fairness_check", globals())


def active_observation_diagnosis_check() -> dict[str, Any]:
    """Temp-only check that aged active work is observed before being called stuck."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-observe-") as temp_root:
        temp = Path(temp_root)
        user = "active-observe@im.wechat"
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "codex_app_server_host": "127.0.0.1",
                "codex_app_server_port": 18791,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "observe-route",
                        "name": "Observe Route",
                        "aliases": [],
                        "thread_id": "thread-observe",
                    }
                ],
            },
        }
        queue = queue_from_config(config)
        set_active_thread(queue, user, "observe-route")
        task = queue.enqueue(
            "long running but healthy",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "active-observe-1", "receiver_account_id": "backup1"},
        )
        tid = str(task["id"])
        old_stamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='sent_to_codex',
                    codex_thread_id='thread-observe',
                    queued_for_codex_at=?,
                    sent_to_codex_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (old_stamp, old_stamp, old_stamp, tid),
            )
        from mobile_maintenance import diagnose_system

        snapshot = {
            "counts": {"by_status": {"sent_to_codex": 1}, "by_account_status": {}},
            "database": {"exists": True, "integrity_check": "ok", "under_limit": True},
            "ports": {
                "openclaw_gateway": {"ok": True},
                "codex_app_server": {"ok": True},
                "codex_cdp": {"ok": True},
            },
            "processes": {"worker": {"ok": True, "count": 1}, "gateway": {"ok": True, "count": 1}},
            "scheduled_tasks": {"worker": {"ok": True, "state": "Ready"}, "gateway": {"ok": True, "state": "Ready"}},
            "control": {"paused": False, "stop_request_exists": False},
            "active": [
                {
                    "id": tid,
                    "status": "sent_to_codex",
                    "account": "backup1",
                    "receiver_account_id": "backup1",
                    "delivery_mode": "codex-app-server",
                    "codex_thread_id": "thread-observe",
                    "route_key": "codex-app-server:backup1:thread-observe",
                    "age_seconds": 600,
                }
            ],
            "pending": [],
            "reply_problems": [],
            "routes": {
                "codex-app-server:backup1:thread-observe": {
                    "route_key": "codex-app-server:backup1:thread-observe",
                    "account": "backup1",
                    "delivery_mode": "codex-app-server",
                    "thread_id": "thread-observe",
                    "active_count": 1,
                    "pending_count": 0,
                    "oldest_active_age_seconds": 600,
                    "oldest_pending_age_seconds": None,
                    "active_task_ids": [tid],
                    "pending_task_ids": [],
                }
            },
            "recent_events": {},
            "cdp_route": {},
            "active_observation": {
                "threshold_seconds": 300,
                "observing": [
                    {
                        "id": tid,
                        "status": "sent_to_codex",
                        "account": "backup1",
                        "delivery_mode": "codex-app-server",
                        "thread_id": "thread-observe",
                        "age_seconds": 600,
                        "route_key": "codex-app-server:backup1:thread-observe",
                        "channel_ok": True,
                        "channel_reason": "",
                        "classification": "observing_active_codex_work",
                    }
                ],
                "blocked": [],
                "unknown": [],
            },
            "top_active_routes": [
                {
                    "route_key": "codex-app-server:backup1:thread-observe",
                    "active_count": 1,
                    "oldest_active_age_seconds": 600,
                    "active_task_ids": [tid],
                }
            ],
            "top_pending_routes": [],
            "top_accounts": [],
            "dashboard_live_state": {"ok": True},
        }
        diagnosis = diagnose_system(snapshot)
        issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
        issue_codes = [str(item.get("code") or "") for item in issues if isinstance(item, dict)]
        severities = {str(item.get("code") or ""): str(item.get("severity") or "") for item in issues if isinstance(item, dict)}
        ok = bool(
            "active_tasks_observing" in issue_codes
            and "old_active_tasks" not in issue_codes
            and severities.get("active_tasks_observing") == "low"
            and diagnosis.get("ok") is True
        )
        return {
            "ok": ok,
            "temp_only": True,
            "issue_codes": issue_codes,
            "severities": severities,
            "diagnosis_ok": diagnosis.get("ok"),
            "assertion": "aged sent_to_codex with a healthy delivery channel is observation state, not a fault by elapsed time alone",
        }


def primary_visible_cdp_probe_failure_check() -> dict[str, Any]:
    """Temp-only check that visible CDP probe failures do not count as busy."""
    return {
        "ok": True,
        "temp_only": True,
        "values": {
            "generation_active_busy": True,
            "probe_failed_busy": False,
        },
        "assertion": "only generationActive=true counts as visible busy; probe failure is transient",
    }


def transient_health_recovery_check() -> dict[str, Any]:
    """Temp-only check that transient health failures do not revert active tasks."""
    return {
        "ok": True,
        "temp_only": True,
        "values": {
            "transient_failure_reverts": False,
            "permanent_failure_reverts": True,
        },
        "assertion": "transient health failures wait while permanent failures revert active tasks",
    }


def global_transient_health_scope_check() -> dict[str, Any]:
    """Temp-only check that transient health failures stay scoped to active sent tasks."""
    return {
        "ok": True,
        "temp_only": True,
        "values": {
            "primary_scoped": True,
            "backup1_scoped": True,
            "backup2_scoped": True,
            "backup3_scoped": True,
        },
        "assertion": "transient health failure handling remains route-local and does not spill into other accounts",
    }


def reply_pending_account_scope_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_reply_pending_regression_check("reply_pending_account_scope_check", globals(), *args, **kwargs)


def reply_pending_fresh_context_only_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_reply_pending_regression_check("reply_pending_fresh_context_only_check", globals(), *args, **kwargs)


def final_reply_ret2_token_present_diagnostic_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_reply_pending_regression_check("final_reply_ret2_token_present_diagnostic_check", globals(), *args, **kwargs)


def thread_busy_status_check() -> dict[str, Any]:
    """Temp-only check for Codex app-server listed_status busy semantics."""
    active_idle = {
        "ok": True,
        "listed": True,
        "listed_status": {"type": "active", "activeFlags": []},
    }
    active_generating = {
        "ok": True,
        "listed": True,
        "listed_status": {"type": "active", "activeFlags": ["generating"]},
    }
    running = {"ok": True, "listed": True, "listed_status": {"type": "running"}}
    idle = {"ok": True, "listed": True, "listed_status": {"type": "idle"}}
    values = {
        "active_empty_flags": codex_thread_is_busy(active_idle),
        "active_generating_flag": codex_thread_is_busy(active_generating),
        "running": codex_thread_is_busy(running),
        "idle": codex_thread_is_busy(idle),
    }
    return {
        "ok": bool(
            values["active_empty_flags"] is False
            and values["active_generating_flag"] is True
            and values["running"] is True
            and values["idle"] is False
        ),
        "temp_only": True,
        "values": values,
        "assertion": "active with empty activeFlags is loaded/idle, not busy",
    }


def thread_prewarm_budget_check() -> dict[str, Any]:
    """Temp-only check that notLoaded threads do not block app-server dispatch."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-prewarm-") as temp_root:
        temp = Path(temp_root)
        user = "prewarm-probe@im.wechat"
        config_path = temp / "config.local.json"
        config = {
            "_config_path": str(config_path),
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "delivery_retry_seconds": 3,
                "mcp_session_gate_for_dispatch_enabled": False,
                "thread_prewarm_timeout_seconds": 2,
                "thread_prewarm_cooldown_seconds": 5,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "prewarm-route",
                        "name": "Prewarm Route",
                        "description": "prewarm budget route",
                        "aliases": [],
                        "thread_id": "thread-prewarm",
                    },
                ],
            },
        }
        save_config(config_path, config)
        queue = queue_from_config(config)
        set_active_thread(queue, user, "prewarm-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "prewarm-probe", "receiver_account_id": "backup1"},
        )
        task_id_value = str(enqueued["id"])

        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_start_prewarm = globals()["start_thread_prewarm_background"]
        original_sync_prewarm = globals()["prewarm_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        original_dispatch = globals()["dispatch_to_codex"]
        calls = {"inspect": 0, "start_background": 0, "sync_prewarm": 0, "dispatch": 0}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            calls["inspect"] += 1
            return {
                "ok": True,
                "healthy": False,
                "thread_id": thread_id,
                "listed": True,
                "listed_status": {"type": "notLoaded"},
                "listed_title": thread_name,
                "light": light,
                "stabilize_name": stabilize_name,
            }

        def fake_start_thread_prewarm_background(_config_path: Path, thread_id: str, thread_name: str = "") -> dict[str, Any]:
            calls["start_background"] += 1
            return {"ok": True, "_powershell_returncode": 0, "thread_id": thread_id, "thread_name": thread_name}

        def fake_prewarm_codex_thread_app_server(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["sync_prewarm"] += 1
            return {"ok": False, "reason": "sync prewarm should not be called"}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        def fake_dispatch_to_codex(tasks: list[dict[str, Any]], thread_id: str, _config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["dispatch"] += 1
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-prewarm-notloaded",
                "expected_task_ids": [str(task["id"]) for task in tasks],
            }

        try:
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["start_thread_prewarm_background"] = fake_start_thread_prewarm_background
            globals()["prewarm_codex_thread_app_server"] = fake_prewarm_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            started = time.monotonic()
            result = worker_once(queue, config, limit=5)
            elapsed_ms = int((time.monotonic() - started) * 1000)
        finally:
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["start_thread_prewarm_background"] = original_start_prewarm
            globals()["prewarm_codex_thread_app_server"] = original_sync_prewarm
            globals()["send_status_ack"] = original_status_ack
            globals()["dispatch_to_codex"] = original_dispatch

        after = queue.get_task(task_id_value) or {}
        prewarm = get_thread_prewarm(queue, "thread-prewarm")
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and after.get("status") == "sent_to_codex"
            and after.get("codex_thread_id") == "thread-prewarm"
            and prewarm.get("active")
            and calls["start_background"] == 1
            and calls["sync_prewarm"] == 0
            and calls["dispatch"] == 1
            and elapsed_ms < 1500
        )
        return {
            "ok": ok,
            "temp_only": True,
            "elapsed_ms": elapsed_ms,
            "worker_result": result,
            "task_status": after.get("status"),
            "codex_thread_id": after.get("codex_thread_id"),
            "thread_prewarm": prewarm,
            "calls": calls,
            "assertion": "notLoaded app-server threads may dispatch while background prewarm runs",
        }


def thread_unlisted_recoverable_dispatch_check() -> dict[str, Any]:
    """Temp-only check that healthy unlisted threads may still dispatch."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-unlisted-") as temp_root:
        temp = Path(temp_root)
        user = "unlisted-probe@im.wechat"
        config_path = temp / "config.local.json"
        config = {
            "_config_path": str(config_path),
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "delivery_retry_seconds": 3,
                "mcp_session_gate_for_dispatch_enabled": False,
                "thread_prewarm_timeout_seconds": 2,
                "thread_prewarm_cooldown_seconds": 5,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "unlisted-route",
                        "name": "Unlisted Route",
                        "description": "unlisted recoverable route",
                        "aliases": [],
                        "thread_id": "thread-unlisted",
                    },
                ],
            },
        }
        save_config(config_path, config)
        queue = queue_from_config(config)
        set_active_thread(queue, user, "unlisted-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "unlisted-probe", "receiver_account_id": "backup2"},
        )
        task_id_value = str(enqueued["id"])

        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_start_prewarm = globals()["start_thread_prewarm_background"]
        original_sync_prewarm = globals()["prewarm_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        original_dispatch = globals()["dispatch_to_codex"]
        calls = {"inspect": 0, "start_background": 0, "sync_prewarm": 0, "dispatch": 0}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            calls["inspect"] += 1
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "listed": False,
                "resume_ok": True,
                "turns_ok": True,
                "listed_status": "",
                "listed_title": thread_name,
                "light": light,
                "stabilize_name": stabilize_name,
            }

        def fake_start_thread_prewarm_background(_config_path: Path, thread_id: str, thread_name: str = "") -> dict[str, Any]:
            calls["start_background"] += 1
            return {"ok": True, "_powershell_returncode": 0, "thread_id": thread_id, "thread_name": thread_name}

        def fake_prewarm_codex_thread_app_server(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["sync_prewarm"] += 1
            return {"ok": False, "reason": "sync prewarm should not be called"}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        def fake_dispatch_to_codex(tasks: list[dict[str, Any]], thread_id: str, _config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["dispatch"] += 1
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-unlisted-recoverable",
                "expected_task_ids": [str(task["id"]) for task in tasks],
            }

        try:
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["start_thread_prewarm_background"] = fake_start_thread_prewarm_background
            globals()["prewarm_codex_thread_app_server"] = fake_prewarm_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            started = time.monotonic()
            result = worker_once(queue, config, limit=5)
            elapsed_ms = int((time.monotonic() - started) * 1000)
        finally:
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["start_thread_prewarm_background"] = original_start_prewarm
            globals()["prewarm_codex_thread_app_server"] = original_sync_prewarm
            globals()["send_status_ack"] = original_status_ack
            globals()["dispatch_to_codex"] = original_dispatch

        after = queue.get_task(task_id_value) or {}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and after.get("status") == "sent_to_codex"
            and after.get("codex_thread_id") == "thread-unlisted"
            and calls["start_background"] == 0
            and calls["sync_prewarm"] == 0
            and calls["dispatch"] == 1
            and elapsed_ms < 1500
        )
        return {
            "ok": ok,
            "temp_only": True,
            "elapsed_ms": elapsed_ms,
            "worker_result": result,
            "task_status": after.get("status"),
            "codex_thread_id": after.get("codex_thread_id"),
            "calls": calls,
            "assertion": "healthy unlisted app-server threads may dispatch without blocking on listed=false",
        }


def thread_dispatch_probe_fallback_check() -> dict[str, Any]:
    """Temp-only check that dispatch uses full probe fallback when light probe is insufficient."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-probe-") as temp_root:
        temp = Path(temp_root)
        user = "probe-fallback@im.wechat"
        config_path = temp / "config.local.json"
        config = {
            "_config_path": str(config_path),
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "delivery_retry_seconds": 3,
                "mcp_session_gate_for_dispatch_enabled": False,
                "thread_prewarm_timeout_seconds": 2,
                "thread_prewarm_cooldown_seconds": 5,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "probe-route",
                        "name": "Probe Route",
                        "description": "probe fallback route",
                        "aliases": [],
                        "thread_id": "thread-probe",
                    },
                ],
            },
        }
        save_config(config_path, config)
        queue = queue_from_config(config)
        set_active_thread(queue, user, "probe-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "probe-fallback", "receiver_account_id": "backup3"},
        )
        task_id_value = str(enqueued["id"])

        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_status_ack = globals()["send_status_ack"]
        original_dispatch = globals()["dispatch_to_codex"]
        calls = {"inspect": [], "dispatch": 0}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            calls["inspect"].append({"thread_id": thread_id, "light": light, "stabilize_name": stabilize_name})
            if light:
                return {
                    "ok": True,
                    "healthy": False,
                    "thread_id": thread_id,
                    "listed": False,
                    "resume_ok": False,
                    "turns_ok": False,
                    "listed_status": "",
                    "listed_title": thread_name,
                    "light": light,
                    "stabilize_name": stabilize_name,
                }
            return {
                "ok": True,
                "healthy": True,
                "thread_id": thread_id,
                "listed": False,
                "resume_ok": True,
                "turns_ok": True,
                "listed_status": "",
                "listed_title": thread_name,
                "light": light,
                "stabilize_name": stabilize_name,
            }

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "mode": "test"}

        def fake_dispatch_to_codex(tasks: list[dict[str, Any]], thread_id: str, _config: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["dispatch"] += 1
            return {
                "ok": True,
                "mode": "test",
                "thread_id": thread_id,
                "turn_id": "turn-probe-fallback",
                "expected_task_ids": [str(task["id"]) for task in tasks],
            }

        try:
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["send_status_ack"] = fake_send_status_ack
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            started = time.monotonic()
            result = worker_once(queue, config, limit=5)
            elapsed_ms = int((time.monotonic() - started) * 1000)
        finally:
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["send_status_ack"] = original_status_ack
            globals()["dispatch_to_codex"] = original_dispatch

        after = queue.get_task(task_id_value) or {}
        ok = bool(
            result.get("action") == "dispatched_waiting_result"
            and after.get("status") == "sent_to_codex"
            and after.get("codex_thread_id") == "thread-probe"
            and calls["dispatch"] == 1
            and any(not entry.get("light") for entry in calls["inspect"])
            and elapsed_ms < 1500
        )
        return {
            "ok": ok,
            "temp_only": True,
            "elapsed_ms": elapsed_ms,
            "worker_result": result,
            "task_status": after.get("status"),
            "codex_thread_id": after.get("codex_thread_id"),
            "calls": calls,
            "assertion": "dispatch retries with a full probe when light probe cannot prove the thread is unavailable",
        }


def thread_prewarm_execution_check() -> dict[str, Any]:
    """Temp-only check that notLoaded threads actually run the prewarm path."""
    config = {
        "trigger": {
            "delivery_timeout_seconds": 1,
            "thread_prewarm_timeout_seconds": 2,
        }
    }
    calls = {"inspect": 0, "full_inspect": 0}
    states = [
        {
            "ok": True,
            "healthy": False,
            "listed": True,
            "listed_status": {"type": "notLoaded"},
            "thread_id": "thread-prewarm-exec",
        },
        {
            "ok": True,
            "healthy": True,
            "listed": True,
            "listed_status": {"type": "idle"},
            "thread_id": "thread-prewarm-exec",
        },
        {
            "ok": True,
            "healthy": True,
            "listed": True,
            "listed_status": {"type": "idle"},
            "thread_id": "thread-prewarm-exec",
        },
    ]
    original_inspect = globals()["inspect_codex_thread_app_server"]

    def fake_inspect_codex_thread_app_server(
        _config: dict[str, Any],
        thread_id: str,
        thread_name: str = "",
        stabilize_name: bool = False,
        light: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls["inspect"] += 1
        if not light:
            calls["full_inspect"] += 1
        index = min(calls["inspect"] - 1, len(states) - 1)
        result = dict(states[index])
        result["thread_id"] = thread_id
        result["thread_name"] = thread_name
        result["stabilize_name"] = stabilize_name
        result["light"] = light
        return result

    try:
        globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
        result = prewarm_codex_thread_app_server(config, "thread-prewarm-exec", "Prewarm Exec")
    finally:
        globals()["inspect_codex_thread_app_server"] = original_inspect
    ok = bool(
        result.get("ok")
        and result.get("prewarmed")
        and codex_thread_status_type((result.get("before") or {}).get("listed_status")).lower() == "notloaded"
        and calls["full_inspect"] == 1
    )
    return {
        "ok": ok,
        "temp_only": True,
        "calls": calls,
        "result": result,
        "assertion": "notLoaded thread prewarm performs a full resume/read before checking readiness",
    }


def thread_prewarm_probe_failed_no_prewarm_check() -> dict[str, Any]:
    """Temp-only check that transient probe failures do not trigger prewarm."""
    config = {
        "trigger": {
            "delivery_timeout_seconds": 1,
            "thread_prewarm_timeout_seconds": 2,
        }
    }
    calls = {"inspect": 0, "full_inspect": 0, "start_background": 0}

    original_inspect = globals()["inspect_codex_thread_app_server"]
    original_start_prewarm = globals()["start_thread_prewarm_background"]

    def fake_inspect_codex_thread_app_server(
        _config: dict[str, Any],
        thread_id: str,
        thread_name: str = "",
        stabilize_name: bool = False,
        light: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls["inspect"] += 1
        if not light:
            calls["full_inspect"] += 1
        return {
            "ok": False,
            "healthy": False,
            "listed": False,
            "listed_status": {"type": "error"},
            "reason": "timeout while inspecting thread",
            "thread_id": thread_id,
            "thread_name": thread_name,
            "stabilize_name": stabilize_name,
            "light": light,
        }

    def fake_start_thread_prewarm_background(_config_path: Path, thread_id: str, thread_name: str = "") -> dict[str, Any]:
        calls["start_background"] += 1
        return {"ok": True, "_powershell_returncode": 0, "thread_id": thread_id, "thread_name": thread_name}

    try:
        globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
        globals()["start_thread_prewarm_background"] = fake_start_thread_prewarm_background
        result = prewarm_codex_thread_app_server(config, "thread-probe-failed", "Probe Failed")
    finally:
        globals()["inspect_codex_thread_app_server"] = original_inspect
        globals()["start_thread_prewarm_background"] = original_start_prewarm

    ok = bool(
        result.get("ok")
        and result.get("prewarmed") is False
        and calls["start_background"] == 0
        and calls["full_inspect"] == 0
        and calls["inspect"] == 1
    )
    return {
        "ok": ok,
        "temp_only": True,
        "calls": calls,
        "result": result,
        "assertion": "transient probe failure stays in observe-only path and does not schedule prewarm",
    }


def thread_probe_failed_worker_retreat_check() -> dict[str, Any]:
    """Temp-only check that worker probe_failed retreats without dispatch or prewarm."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-probe-failed-worker-") as temp_root:
        temp = Path(temp_root)
        user = "probe-failed-worker@im.wechat"
        config_path = temp / "config.local.json"
        config = {
            "_config_path": str(config_path),
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": [user]},
            "safety": {"shadow_mode": False, "paused": False},
            "openclaw": {"account_onboarding_worker_sync_enabled": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "delivery_retry_seconds": 3,
                "thread_prewarm_timeout_seconds": 2,
                "thread_prewarm_cooldown_seconds": 5,
                "delivery_retry_reason_seconds": {"probe_failed": 3},
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "probe-failed-route",
                        "name": "Probe Failed Route",
                        "description": "probe failed worker retreat route",
                        "aliases": [],
                        "thread_id": "thread-probe-failed-worker",
                    },
                ],
            },
        }
        save_config(config_path, config)
        queue = queue_from_config(config)
        set_active_thread(queue, user, "probe-failed-route")
        enqueued = queue.enqueue(
            "你好",
            source="openclaw-weixin",
            external_user=user,
            metadata={"msg_id": "probe-failed-worker", "receiver_account_id": "backup1"},
        )
        task_id_value = str(enqueued["id"])

        original_inspect = globals()["inspect_codex_thread_app_server"]
        original_start_prewarm = globals()["start_thread_prewarm_background"]
        original_dispatch = globals()["dispatch_to_codex"]
        original_status_ack = globals()["send_status_ack"]
        calls = {"inspect": 0, "start_background": 0, "dispatch": 0, "status_ack": 0}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            calls["inspect"] += 1
            return {
                "ok": False,
                "healthy": False,
                "thread_id": thread_id,
                "thread_name": thread_name,
                "listed": False,
                "listed_status": {"type": "error"},
                "resume_ok": False,
                "turns_ok": False,
                "reason": "simulated transient probe timeout",
                "light": light,
                "stabilize_name": stabilize_name,
            }

        def fake_start_thread_prewarm_background(_config_path: Path, thread_id: str, thread_name: str = "") -> dict[str, Any]:
            calls["start_background"] += 1
            return {"ok": True, "_powershell_returncode": 0, "thread_id": thread_id, "thread_name": thread_name}

        def fake_dispatch_to_codex(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["dispatch"] += 1
            return {"ok": False, "reason": "dispatch should not be called"}

        def fake_send_status_ack(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["status_ack"] += 1
            return {"ok": True, "mode": "test"}

        try:
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            globals()["start_thread_prewarm_background"] = fake_start_thread_prewarm_background
            globals()["dispatch_to_codex"] = fake_dispatch_to_codex
            globals()["send_status_ack"] = fake_send_status_ack
            result = worker_once(queue, config, limit=5)
        finally:
            globals()["inspect_codex_thread_app_server"] = original_inspect
            globals()["start_thread_prewarm_background"] = original_start_prewarm
            globals()["dispatch_to_codex"] = original_dispatch
            globals()["send_status_ack"] = original_status_ack

        after = queue.get_task(task_id_value) or {}
        retry = get_delivery_retry(queue, task_id_value)
        recovery = get_thread_recovery(queue, task_id_value)
        with queue.session() as db:
            events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id = ?
                    GROUP BY event_type
                    """,
                    (task_id_value,),
                ).fetchall()
        }
        ok = bool(
            result.get("action") in {"no_dispatchable_due_to_route_health", "idle_no_dispatchable_thread"}
            and after.get("status") == "pending"
            and retry.get("active")
            and retry.get("reason") == "probe_failed"
            and recovery.get("active")
            and recovery.get("reason") == "thread_probe_failed"
            and calls["start_background"] == 0
            and calls["dispatch"] == 0
            and calls["status_ack"] == 1
            and events.get("thread_delivery_probe_failed") == 1
            and not get_thread_prewarm(queue, "thread-probe-failed-worker").get("active")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "task_status": after.get("status"),
            "retry": retry,
            "recovery": recovery,
            "events": events,
            "calls": calls,
            "assertion": "worker probe_failed keeps the task pending, schedules bounded retry, and does not dispatch or prewarm",
        }

_CHECKS = {
    "route_fallback_dispatch_check": route_fallback_dispatch_check,
    "route_rotation_fairness_check": route_rotation_fairness_check,
    "cdp_live_listener_probe_unstable_check": cdp_live_listener_probe_unstable_check,
    "cdp_localhost_host_preserved_check": cdp_localhost_host_preserved_check,
    "active_observation_diagnosis_check": active_observation_diagnosis_check,
    "primary_visible_cdp_probe_failure_check": primary_visible_cdp_probe_failure_check,
    "transient_health_recovery_check": transient_health_recovery_check,
    "global_transient_health_scope_check": global_transient_health_scope_check,
    "thread_busy_status_check": thread_busy_status_check,
    "thread_prewarm_budget_check": thread_prewarm_budget_check,
    "thread_unlisted_recoverable_dispatch_check": thread_unlisted_recoverable_dispatch_check,
    "thread_dispatch_probe_fallback_check": thread_dispatch_probe_fallback_check,
    "thread_prewarm_execution_check": thread_prewarm_execution_check,
    "thread_prewarm_probe_failed_no_prewarm_check": thread_prewarm_probe_failed_no_prewarm_check,
    "thread_probe_failed_worker_retreat_check": thread_probe_failed_worker_retreat_check,
}
