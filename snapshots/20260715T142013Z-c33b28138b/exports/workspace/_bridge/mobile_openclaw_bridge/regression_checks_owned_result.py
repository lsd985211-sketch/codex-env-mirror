"""Owned-result and recovery regression checks for the mobile bridge.

Owns: large self-test routines covering owned-result recovery, redelivery gates,
protocol-violation recovery, and app-server continuation safeguards.
Non-goals: production queue dispatch, permission decisions, final reply sending,
or CLI argument parsing.
State behavior: tests may create temp queues and synthetic events; runtime
callbacks and classes are injected by the CLI facade before each check.
Normal caller: `mobile_openclaw_cli` facade functions that preserve legacy CLI
command names while keeping the main CLI file smaller.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any

_PROTECTED_NAMES = {
    "Any",
    "_CHECKS",
    "_PROTECTED_NAMES",
    "_inject_dependencies",
    "run_owned_result_regression_check",
}


def _inject_dependencies(env: dict[str, Any]) -> None:
    """Populate legacy global dependencies without overwriting moved checks."""
    for name, value in env.items():
        if name in _PROTECTED_NAMES or name in _CHECKS:
            continue
        globals()[name] = value


def run_owned_result_regression_check(name: str, env: dict[str, Any]) -> dict[str, Any]:
    """Run a moved regression check in the CLI global namespace.

    These legacy checks intentionally monkeypatch CLI globals to simulate Codex
    app-server, CDP, and reply-delivery behavior. Rebinding the function object
    to the facade's globals preserves that old test isolation contract after
    moving the source out of the large CLI file.
    """
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown owned-result regression check: {name}") from exc
    rebound_check = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound_check()

def waiting_followup_owned_result_redelivery_gate_check() -> dict[str, Any]:
    """Temp-only check that a waiting primary completes from an already-owned result before redelivery."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-waiting-owned-gate-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["waiting-owned-gate@im.wechat"]},
            "accounts": {"users": {"waiting-owned-gate@im.wechat": {"account_id": "primary"}}},
            "openclaw_accounts": {"primary": {"userId": "waiting-owned-gate@im.wechat", "token": "present"}},
            "permissions": {
                "users": {"waiting-owned-gate@im.wechat": {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 5,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
            "openclaw": {"account_id": "primary"},
            "threads": {
                "default_id": "thread-waiting-owned-gate",
                "items": [
                    {
                        "id": "thread-waiting-owned-gate",
                        "name": "Waiting Owned Gate Thread",
                        "thread_id": "thread-waiting-owned-gate",
                    }
                ],
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "waiting-gate-owner"
        followup_id = "waiting-gate-followup"
        with queue.session() as db:
            for tid, text, status, created_at in [
                (owner_id, "parked owner prompt", "sent_to_codex", now),
                (followup_id, "same-thread follow-up", "pending", now),
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
                        "waiting-owned-gate@im.wechat",
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
                        "thread-waiting-owned-gate",
                        "{}",
                        created_at,
                        created_at,
                        created_at if status == "sent_to_codex" else None,
                        created_at if status == "sent_to_codex" else None,
                    ),
                )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-waiting-owned-gate",
                "turn_id": "turn-waiting-owned-gate-latest",
                "client_message_id": "batch-waiting-owned-gate-latest",
                "expected_task_ids": [owner_id],
                "mobile_protocols": {
                    owner_id: {
                        "task_id": owner_id,
                        "ack_code": "latest-ack-code",
                        "result_code": "latest-result-code",
                    }
                },
                "delivery_mode": "codex-cdp",
            },
            owner_id,
        )
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-waiting-owned-gate",
                "turn_id": "turn-waiting-owned-gate-old",
                "client_message_id": "batch-waiting-owned-gate-old",
                "expected_task_ids": [owner_id],
                "mobile_protocols": {
                    owner_id: {
                        "task_id": owner_id,
                        "ack_code": "old-ack-code",
                        "result_code": "old-result-code",
                    }
                },
                "delivery_mode": "codex-cdp",
            },
            owner_id,
        )
        set_active_thread(queue, "waiting-owned-gate@im.wechat", "thread-waiting-owned-gate")
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(owner_id) or {},
            "generation_active_without_owned_result",
            {"test": "owned result already exists before redelivery"},
        )

        push_calls: list[dict[str, Any]] = []
        poll_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_hist = globals()["poll_historical_owned_codex_result"]
        original_push = globals()["push_final_reply_async"]
        original_inspect = globals()["inspect_codex_thread_for_dispatch"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_inspect_codex_thread_for_dispatch(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "listed": True,
                "listed_status": {"type": "active", "activeFlags": []},
                "resume_ok": True,
                "turns_ok": True,
                "thread_id": thread_id,
                "thread_name": thread_name,
            }

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            turn_id: str,
            _baseline_key: str,
            client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "turn_id": turn_id,
                    "client_message_id": client_message_id,
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            if expected_result_codes == {owner_id: "latest-result-code"}:
                return {
                    "ok": True,
                    "healthy": True,
                    "status": "completed",
                    "newText": "already owned answer",
                    "generationActive": False,
                    "result_complete": True,
                    "terminal_without_text": False,
                    "ownership": {
                        "required": True,
                        "valid": True,
                        "protocol": "mobile_result_boundary_v2",
                        "expected_task_ids": [owner_id],
                        "matched_task_id": owner_id,
                        "matched_result_code": "latest-result-code",
                        "result_complete": True,
                    },
                }
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "",
                "generationActive": False,
                "result_complete": False,
                "terminal_without_text": True,
                "ownership": {
                    "required": True,
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "expected_task_ids": [owner_id],
                    "missing_task_ids": [owner_id],
                    "result_complete": False,
                },
            }

        def fake_poll_historical_owned_codex_result(
            _queue: MobileQueue,
            _poll_config: dict[str, Any],
            task_id_arg: str,
            _thread_id: str,
            current_turn_id: str,
            current_client_message_id: str,
            expected_task_ids: list[str],
            expected_result_codes: dict[str, str],
            expected_ack_codes: dict[str, str],
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "historical": True,
                    "task_id": task_id_arg,
                    "turn_id": current_turn_id,
                    "client_message_id": current_client_message_id,
                    "expected_task_ids": expected_task_ids,
                    "expected_result_codes": expected_result_codes,
                    "expected_ack_codes": expected_ack_codes,
                }
            )
            if expected_result_codes == {owner_id: "old-result-code"}:
                return {
                    "ok": True,
                    "healthy": True,
                    "status": "completed",
                    "newText": "already owned answer",
                    "generationActive": False,
                    "result_complete": True,
                    "terminal_without_text": False,
                    "ownership": {
                        "required": True,
                        "valid": True,
                        "protocol": "mobile_result_boundary_v2",
                        "expected_task_ids": [owner_id],
                        "matched_task_id": owner_id,
                        "matched_result_code": "old-result-code",
                        "result_complete": True,
                    },
                    "historical_attempt_fallback": True,
                    "historical_attempt": {"turn_id": "turn-waiting-owned-gate-old"},
                }
            return {}

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["inspect_codex_thread_for_dispatch"] = fake_inspect_codex_thread_for_dispatch
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["poll_historical_owned_codex_result"] = fake_poll_historical_owned_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            with TemporaryStopRequestPath(temp / "STOP_REQUEST"):
                result = worker_once(queue, config, limit=5, task_id=owner_id)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["inspect_codex_thread_for_dispatch"] = original_inspect
            globals()["poll_codex_result"] = original_poll
            globals()["poll_historical_owned_codex_result"] = original_hist
            globals()["push_final_reply_async"] = original_push

        owner_after = queue.get_task(owner_id) or {}
        followup_after = queue.get_task(followup_id) or {}
        waiting_key_present = bool(queue.runtime_get(waiting_followup_redelivery_key(owner_id)))
        with queue.session() as db:
            owner_events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (owner_id,),
                ).fetchall()
            }
            followup_events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (followup_id,),
                ).fetchall()
            }
        ok = bool(
            result.get("action") in {"waiting_owned_result_completed", "idle_no_dispatchable_thread"}
            and (result.get("action") == "waiting_owned_result_completed" or (result.get("recovery") or {}).get("recovered") == 1)
            and owner_after.get("status") == "done"
            and owner_after.get("result") == "already owned answer"
            and followup_after.get("status") in {"pending", "rejected"}
            and push_calls == [{"task_id": owner_id, "text": "already owned answer", "media": ""}]
            and any(call.get("expected_result_codes") in ({owner_id: "latest-result-code"}, {owner_id: "old-result-code"}) for call in poll_calls)
            and any(call.get("historical") and call.get("expected_result_codes") == {owner_id: "old-result-code"} for call in poll_calls)
            and not waiting_key_present
            and (owner_events.get("pre_redelivery_owned_result_completed", 0) == 1 or owner_events.get("recovery_result_pushed", 0) == 1)
            and owner_events.get("followup_triggered_waiting_redelivery_deferred", 0) == 0
            and followup_events.get("queued_for_codex", 0) == 0
        )
        return {
            "ok": ok,
            "temp_only": True,
            "worker_result": result,
            "owner_status": owner_after.get("status"),
            "owner_result": owner_after.get("result"),
            "followup_status": followup_after.get("status"),
            "push_calls": push_calls,
            "poll_calls": poll_calls,
            "waiting_key_present": waiting_key_present,
            "owner_events": owner_events,
            "followup_events": followup_events,
            "assertion": "a waiting owner with an already-owned result completes before redelivery and cancels the retry path",
        }

def base_ack_only_terminal_redelivery_check() -> dict[str, Any]:
    """Temp-only check that ack-only terminal base failures allow one controlled redelivery."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-base-ack-only-terminal-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["base-ack-only@im.wechat"]},
            "accounts": {"users": {"base-ack-only@im.wechat": {"account_id": "primary"}}},
            "openclaw_accounts": {"primary": {"userId": "base-ack-only@im.wechat", "token": "present"}},
            "permissions": {
                "users": {"base-ack-only@im.wechat": {"role": "admin", "allowed_actions": ["ask"]}},
                "profiles": {"admin": {"allowed_actions": ["ask"]}},
            },
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {"delivery_mode": "codex-cdp"},
            "openclaw": {"account_id": "primary"},
        }
        owner_id = "base-ack-only-owner"
        followup_id = "base-ack-only-followup"
        now = datetime.now(timezone.utc).isoformat()
        with queue.session() as db:
            for tid, text, status in [
                (owner_id, "base ack-only prompt", "sent_to_codex"),
                (followup_id, "follow-up prompt", "pending"),
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
                        "base-ack-only@im.wechat",
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
                        "thread-base-ack-only",
                        "{}",
                        now,
                        now,
                        now if status == "sent_to_codex" else None,
                        now if status == "sent_to_codex" else None,
                    ),
                )
        queue.runtime_set(task_turn_key(owner_id), "turn-base-ack-only")
        queue.runtime_set(task_batch_key(owner_id), "batch-base-ack-only")
        queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(owner_id), "ack-base-only")
        queue.runtime_set(task_result_code_key(owner_id), "result-base-only")
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(owner_id) or {},
            "protocol_violation_no_owned_result",
            {"seed": "base ack-only terminal should not defer forever"},
        )

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_history = globals()["recover_owned_result_from_history_sources"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "status": "",
                "newText": "",
                "ack_seen": True,
                "generationActive": False,
                "result_complete": False,
                "terminal_without_text": False,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "required": True,
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": True,
                    "expected_ack_codes": expected_ack_codes or {},
                    "expected_result_codes": expected_result_codes or {},
                    "expected_task_ids": expected_task_ids or [],
                    "result_complete": False,
                },
            }

        def fake_recover_history(
            _queue: MobileQueue,
            _config: dict[str, Any],
            _poll_config: dict[str, Any],
            _task_id: str,
            _thread_id: str,
            _turn_id: str,
            _client_message_id: str,
            _expected_task_ids: list[str],
            _expected_result_codes: dict[str, str],
            _expected_ack_codes: dict[str, str],
            poll: dict[str, Any],
        ) -> tuple[dict[str, Any], str, bool]:
            return poll, "", False

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["recover_owned_result_from_history_sources"] = fake_recover_history
            gate = try_complete_owned_result_before_redelivery(
                queue,
                config,
                queue.get_task(owner_id) or {},
                "protocol_violation_no_owned_result",
                {"test": "base ack-only terminal gate"},
                trigger_task_id=followup_id,
            )
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["recover_owned_result_from_history_sources"] = original_history

        release = {}
        if gate.get("ok") and not gate.get("completed") and not gate.get("defer_redelivery"):
            clear_waiting_followup_redelivery_state(
                queue,
                owner_id,
                "triggered_by_same_thread_followup",
                {"trigger_task_id": followup_id, "delivery_mode": "codex-cdp"},
            )
            release = release_active_task_to_pending(
                queue,
                config,
                queue.get_task(owner_id) or {},
                "protocol_violation_no_owned_result",
                {"gate": gate, "trigger_task_id": followup_id},
            )

        owner_after_first = queue.get_task(owner_id) or {}
        with queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='sent_to_codex', error='', completed_at=NULL, updated_at=?, sent_to_codex_at=?
                WHERE id=?
                """,
                (now, now, owner_id),
            )
        queue.runtime_set(task_turn_key(owner_id), "turn-base-ack-only-redelivery")
        queue.runtime_set(task_batch_key(owner_id), "batch-base-ack-only-redelivery")
        queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(owner_id), "ack-base-only-redelivery")
        queue.runtime_set(task_result_code_key(owner_id), "result-base-only-redelivery")
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(owner_id) or {},
            "protocol_violation_no_owned_result",
            {"seed": "second ack-only terminal should fail closed"},
        )

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["recover_owned_result_from_history_sources"] = fake_recover_history
            second_gate = try_complete_owned_result_before_redelivery(
                queue,
                config,
                queue.get_task(owner_id) or {},
                "protocol_violation_no_owned_result",
                {"test": "repeated base ack-only terminal gate"},
                trigger_task_id=followup_id,
            )
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["recover_owned_result_from_history_sources"] = original_history

        owner_after_second = queue.get_task(owner_id) or {}
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
                    (owner_id,),
                ).fetchall()
            }
        active_queue = MobileQueue(temp / "active-queue.db")
        active_config = {
            **config,
            "queue": {"db_path": str(temp / "active-queue.db")},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
                "visible_cdp_stale_generation_ack_after_seconds": 30,
            },
        }
        active_owner_id = "base-ack-only-active-owner"
        old_stamp = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
        with active_queue.session() as db:
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
                    active_owner_id,
                    "openclaw-weixin",
                    "base-ack-only@im.wechat",
                    "",
                    "/ask",
                    "active base ack-only prompt",
                    hashlib.sha256(b"active base ack-only prompt").hexdigest(),
                    active_owner_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "primary",
                    "thread-base-ack-only-active",
                    "{}",
                    old_stamp,
                    old_stamp,
                    old_stamp,
                    old_stamp,
                ),
            )
        active_queue.runtime_set(task_turn_key(active_owner_id), "turn-base-ack-only-active")
        active_queue.runtime_set(task_batch_key(active_owner_id), "batch-base-ack-only-active")
        active_queue.runtime_set(task_expected_ids_key(active_owner_id), json.dumps([active_owner_id], ensure_ascii=False))
        active_queue.runtime_set(task_ack_code_key(active_owner_id), "ack-base-active")
        active_queue.runtime_set(task_result_code_key(active_owner_id), "result-base-active")
        mark_waiting_followup_redelivery(
            active_queue,
            active_queue.get_task(active_owner_id) or {},
            "generation_active_without_owned_result",
            {"seed": "waiting followup must not hide later ack-only terminal"},
        )
        active_queue.add_event(
            "local",
            "active_poll_observation",
            {
                "stage": "ack_seen_before_window_drift",
                "ack_seen": True,
                "result_complete": False,
                "policy": "durable task-scoped ack evidence must survive later poll-window drift",
            },
            active_owner_id,
        )
        for _index in range(120):
            active_queue.add_event(
                "local",
                "recovery_waiting_followup_redelivery_skipped",
                {
                    "status": "sent_to_codex",
                    "poll_status": "",
                    "ack_seen": False,
                    "result_complete": False,
                    "policy": "skip noise must not hide earlier durable ack evidence",
                },
                active_owner_id,
            )

        def fake_poll_active_ack_only(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "",
                "ack_seen": False,
                "generationActive": True,
                "result_complete": False,
                "terminal_without_text": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "required": True,
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": False,
                    "expected_ack_codes": expected_ack_codes or {},
                    "expected_result_codes": expected_result_codes or {},
                    "expected_task_ids": expected_task_ids or [],
                    "result_complete": False,
                },
            }

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_active_ack_only
            globals()["recover_owned_result_from_history_sources"] = fake_recover_history
            active_first = recover_active_codex_tasks(active_queue, active_config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["recover_owned_result_from_history_sources"] = original_history

        active_after_first = active_queue.get_task(active_owner_id) or {}
        with active_queue.session() as db:
            db.execute(
                """
                UPDATE mobile_tasks
                SET status='sent_to_codex', error='', completed_at=NULL, updated_at=?, sent_to_codex_at=?
                WHERE id=?
                """,
                (old_stamp, old_stamp, active_owner_id),
            )
        active_queue.runtime_set(task_turn_key(active_owner_id), "turn-base-ack-only-active-second")
        active_queue.runtime_set(task_batch_key(active_owner_id), "batch-base-ack-only-active-second")
        active_queue.runtime_set(task_expected_ids_key(active_owner_id), json.dumps([active_owner_id], ensure_ascii=False))
        active_queue.runtime_set(task_ack_code_key(active_owner_id), "ack-base-active-second")
        active_queue.runtime_set(task_result_code_key(active_owner_id), "result-base-active-second")
        mark_waiting_followup_redelivery(
            active_queue,
            active_queue.get_task(active_owner_id) or {},
            "generation_active_without_owned_result",
            {"seed": "second waiting followup ack-only should fail closed"},
        )
        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_active_ack_only
            globals()["recover_owned_result_from_history_sources"] = fake_recover_history
            active_second = recover_active_codex_tasks(active_queue, active_config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["recover_owned_result_from_history_sources"] = original_history

        active_after_second = active_queue.get_task(active_owner_id) or {}
        with active_queue.session() as db:
            active_events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (active_owner_id,),
                ).fetchall()
            }
        active_ok = bool(
            active_first.get("reverted") == 1
            and active_after_first.get("status") == "pending"
            and active_second.get("reverted") == 1
            and active_after_second.get("status") == "failed"
            and active_events.get("pre_redelivery_base_ack_only_terminal") == 1
            and active_events.get("recovery_base_ack_only_terminal_requeued") == 1
            and active_events.get("protocol_violation_no_owned_result_manual_required") == 1
            and active_events.get("recovery_waiting_followup_redelivery_skipped", 0) == 120
        )
        ok = bool(
            gate.get("ok")
            and not gate.get("completed")
            and not gate.get("defer_redelivery")
            and gate.get("reason") == "base_ack_only_terminal_without_result"
            and gate.get("ack_only_terminal") is True
            and release.get("ok")
            and owner_after_first.get("status") == "pending"
            and second_gate.get("manual_required") is True
            and second_gate.get("reason") == "base_ack_only_terminal_redelivery_already_attempted"
            and owner_after_second.get("status") == "failed"
            and not queue.runtime_get(waiting_followup_redelivery_key(owner_id))
            and events.get("pre_redelivery_base_ack_only_terminal") == 1
            and events.get("protocol_violation_no_owned_result_manual_required") == 1
            and events.get("pre_redelivery_owned_result_deferred", 0) == 0
            and active_ok
        )
        return {
            "ok": ok,
            "temp_only": True,
            "gate": gate,
            "second_gate": second_gate,
            "release": release,
            "owner_status_after_first": owner_after_first.get("status"),
            "owner_status_after_second": owner_after_second.get("status"),
            "waiting_key_present": bool(queue.runtime_get(waiting_followup_redelivery_key(owner_id))),
            "events": events,
            "active_recovery_case": {
                "ok": active_ok,
                "first": active_first,
                "second": active_second,
                "owner_status_after_first": active_after_first.get("status"),
                "owner_status_after_second": active_after_second.get("status"),
                "events": active_events,
            },
            "assertion": "ack-only terminal base task is released once for controlled redelivery, then fails closed on repeat",
        }

def failure_close_owned_result_recovery_check() -> dict[str, Any]:
    """Temp-only check for the narrow failure-close owned-result recovery split."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-failure-close-recovery-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["failure-close@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {"delivery_mode": "codex-app-server"},
            "accounts": {"users": {"failure-close@im.wechat": {"account_id": "backup1"}}},
            "openclaw": {"account_id": "backup1"},
        }
        now = datetime.now(timezone.utc).isoformat()

        def seed_failed_result_task(task_id: str) -> None:
            with queue.session() as db:
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, error, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at,
                        queued_for_codex_at, sent_to_codex_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        "openclaw-weixin",
                        "failure-close@im.wechat",
                        "",
                        "/ask",
                        f"prompt for {task_id}",
                        hashlib.sha256(f"prompt for {task_id}".encode("utf-8")).hexdigest(),
                        task_id,
                        "L1",
                        "sent_to_codex",
                        "",
                        "",
                        "",
                        "backup1",
                        "thread-failure-close",
                        "{}",
                        now,
                        now,
                        now,
                        now,
                    ),
                )
            queue.runtime_set(task_turn_key(task_id), f"turn-{task_id}")
            queue.runtime_set(task_batch_key(task_id), f"batch-{task_id}")
            queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
            queue.runtime_set(task_ack_code_key(task_id), f"ack-{task_id}")
            queue.runtime_set(task_result_code_key(task_id), f"result-{task_id}")
            queue.add_event(
                "local",
                "codex_turn_started",
                {
                    "thread_id": "thread-failure-close",
                    "turn_id": f"turn-{task_id}",
                    "client_message_id": f"batch-{task_id}",
                    "expected_task_ids": [task_id],
                    "protocols": {
                        task_id: {
                            "ack_code": f"ack-{task_id}",
                            "result_code": f"result-{task_id}",
                        }
                    },
                },
                task_id,
            )

        recoverable_id = "failure-close-recoverable"
        nonrecoverable_id = "failure-close-nonrecoverable"
        seed_failed_result_task(recoverable_id)
        seed_failed_result_task(nonrecoverable_id)

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]
        original_historical = globals()["poll_historical_owned_codex_result"]
        original_status_ack_sync = globals()["send_status_ack_sync"]

        push_calls: list[dict[str, Any]] = []
        status_ack_calls: list[dict[str, Any]] = []
        recoverable_poll_calls: list[dict[str, Any]] = []
        nonrecoverable_poll_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        def fake_send_status_ack_sync(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            event_type_arg: str,
        ) -> dict[str, Any]:
            status_ack_calls.append(
                {
                    "task_id": str(task_arg.get("id") or ""),
                    "text": text_arg,
                    "event_type": event_type_arg,
                }
            )
            return {"ok": True, "mode": "test"}

        def fake_historical_poll(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "newText": "", "ownership": {}, "result_complete": False}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            globals()["send_status_ack_sync"] = fake_send_status_ack_sync
            globals()["poll_historical_owned_codex_result"] = fake_historical_poll

            def recoverable_poll(
                _config: dict[str, Any],
                thread_id: str,
                turn_id: str,
                _baseline_key: str,
                _client_message_id: str = "",
                expected_task_ids: list[str] | None = None,
                expected_result_codes: dict[str, str] | None = None,
                expected_ack_codes: dict[str, str] | None = None,
            ) -> dict[str, Any]:
                recoverable_poll_calls.append(
                    {
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "expected_task_ids": expected_task_ids or [],
                        "expected_result_codes": expected_result_codes or {},
                        "expected_ack_codes": expected_ack_codes or {},
                    }
                )
                return {
                    "ok": True,
                    "healthy": True,
                    "status": "completed",
                    "newText": "[[mobile_result_begin:x]]final answer[[mobile_result_end:x]]",
                    "result_complete": True,
                    "ownership": {"valid": True, "result_complete": True},
                }

            globals()["poll_codex_result"] = recoverable_poll
            recoverable_result = fail_app_server_no_owned_result_manual_required(
                queue,
                config,
                queue.get_task(recoverable_id) or {},
                "protocol_violation_no_owned_result",
                {"case": "recoverable"},
            )

            recoverable_after = queue.get_task(recoverable_id) or {}
            with queue.session() as db:
                recoverable_rows = db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (recoverable_id,),
                ).fetchall()
            recoverable_events = {str(row["event_type"] or ""): int(row["n"]) for row in recoverable_rows}

            def nonrecoverable_poll(
                _config: dict[str, Any],
                thread_id: str,
                turn_id: str,
                _baseline_key: str,
                _client_message_id: str = "",
                expected_task_ids: list[str] | None = None,
                expected_result_codes: dict[str, str] | None = None,
                expected_ack_codes: dict[str, str] | None = None,
            ) -> dict[str, Any]:
                nonrecoverable_poll_calls.append(
                    {
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "expected_task_ids": expected_task_ids or [],
                        "expected_result_codes": expected_result_codes or {},
                        "expected_ack_codes": expected_ack_codes or {},
                    }
                )
                return {
                    "ok": True,
                    "healthy": True,
                    "status": "completed",
                    "newText": "",
                    "result_complete": False,
                    "terminal_without_text": True,
                    "protocol": "mobile_result_boundary_v2",
                    "ownership": {"valid": False, "required": True, "result_complete": False},
                }

            globals()["poll_codex_result"] = nonrecoverable_poll
            nonrecoverable_result = fail_app_server_no_owned_result_manual_required(
                queue,
                config,
                queue.get_task(nonrecoverable_id) or {},
                "protocol_violation_no_owned_result",
                {"case": "nonrecoverable"},
            )

            nonrecoverable_after = queue.get_task(nonrecoverable_id) or {}
            with queue.session() as db:
                nonrecoverable_rows = db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (nonrecoverable_id,),
                ).fetchall()
            nonrecoverable_events = {str(row["event_type"] or ""): int(row["n"]) for row in nonrecoverable_rows}
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push
            globals()["send_status_ack_sync"] = original_status_ack_sync
            globals()["poll_historical_owned_codex_result"] = original_historical

        recoverable_ok = bool(
            recoverable_result.get("recovered") is True
            and recoverable_after.get("status") == "done"
            and str(recoverable_after.get("result") or "").strip() == "[[mobile_result_begin:x]]final answer[[mobile_result_end:x]]"
            and len(push_calls) == 1
            and push_calls[0].get("task_id") == recoverable_id
            and recoverable_events.get("failure_close_owned_result_recovered", 0) == 1
            and recoverable_events.get("task_done", 0) >= 1
            and recoverable_poll_calls
        )
        nonrecoverable_ok = bool(
            nonrecoverable_result.get("recovered", False) is False
            and nonrecoverable_after.get("status") == "failed"
            and "manual retry is required" in str(nonrecoverable_after.get("error") or "")
            and len(push_calls) == 1
            and len(status_ack_calls) == 1
            and status_ack_calls[0].get("task_id") == nonrecoverable_id
            and str(status_ack_calls[0].get("event_type") or "") == "status_ack_failure_closed"
            and nonrecoverable_events.get("app_server_protocol_violation_no_owned_result_manual_required", 0) == 1
            and nonrecoverable_poll_calls
        )

        return {
            "ok": recoverable_ok and nonrecoverable_ok,
            "temp_only": True,
            "recoverable_case": {
                "ok": recoverable_ok,
                "result": recoverable_result,
                "status": recoverable_after.get("status"),
                "result_text": recoverable_after.get("result"),
                "push_calls": push_calls,
                "poll_calls": recoverable_poll_calls,
                "events": recoverable_events,
            },
            "nonrecoverable_case": {
                "ok": nonrecoverable_ok,
                "result": nonrecoverable_result,
                "status": nonrecoverable_after.get("status"),
                "error": nonrecoverable_after.get("error"),
                "push_calls_after_recoverable": len(push_calls),
                "poll_calls": nonrecoverable_poll_calls,
                "events": nonrecoverable_events,
            },
            "assertion": "failure-close recovery promotes only durable owned results to done and leaves no-result protocol violations failed without reply replay",
        }

def protocol_violation_no_owned_result_check() -> dict[str, Any]:
    """Temp-only check that terminal mobile-boundary failures are explicit and bounded."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-protocol-violation-") as temp_root:
        temp = Path(temp_root)
        app_queue = MobileQueue(temp / "app-queue.db")
        config = {
            "queue": {"db_path": str(temp / "app-queue.db")},
            "security": {"allowed_users": ["protocol-violation@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 5,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
                "delivery_retry_seconds": 30,
                "protocol_violation_retry_seconds": 30,
                "app_server_no_owned_result_manual_after_attempts": 3,
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "protocol-owner"
        supplement_id = "protocol-supplement"
        with app_queue.session() as db:
            for tid, text, status in [
                (owner_id, "owner prompt", "sent_to_codex"),
                (supplement_id, "later supplement", "pending"),
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
                        "protocol-violation@im.wechat",
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
                        "thread-protocol",
                        "{}",
                        now,
                        now,
                        now if status == "sent_to_codex" else None,
                        now if status == "sent_to_codex" else None,
                    ),
                )
            db.execute(
                """
                UPDATE mobile_tasks
                SET trigger_attempts=?
                WHERE id=?
                """,
                (3, owner_id),
            )
        app_queue.runtime_set(task_turn_key(owner_id), "turn-protocol")
        app_queue.runtime_set(task_batch_key(owner_id), "batch-protocol")
        app_queue.runtime_set(task_expected_ids_key(owner_id), json.dumps([owner_id], ensure_ascii=False))
        app_queue.runtime_set(task_ack_code_key(owner_id), "ack-code")
        app_queue.runtime_set(task_result_code_key(owner_id), "result-code")
        app_queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": "thread-protocol",
                "turn_id": "turn-protocol",
                "client_message_id": "batch-protocol",
                "expected_task_ids": [owner_id],
                "protocols": {
                    owner_id: {
                        "ack_code": "ack-code",
                        "result_code": "result-code",
                    }
                },
            },
            owner_id,
        )
        supplement_payload = {
            "base_message_id": owner_id,
            "active_task_id": owner_id,
            "thread_id": "thread-protocol",
            "delivery_mode": "codex-app-server",
            "items": [task_supplement_snapshot(app_queue.get_task(supplement_id) or {}, "thread-protocol")],
            "published_at": now,
            "supplement_signature": "protocol-supplement",
        }
        app_queue.runtime_set(bridge_supplement_key("thread-protocol"), json.dumps(supplement_payload, ensure_ascii=False))
        app_queue.runtime_set(attachment_supplement_signature_key(owner_id), "protocol-supplement")
        app_queue.add_event(
            "local",
            "pending_backlog_supplement_pending_published",
            {
                "active_task_id": owner_id,
                "thread_id": "thread-protocol",
                "delivery_mode": "codex-app-server",
                "signature": "protocol-supplement",
            },
            supplement_id,
        )

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]
        push_calls: list[dict[str, Any]] = []
        poll_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            thread_id: str,
            turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "",
                "ack_seen": True,
                "result_complete": False,
                "terminal_without_text": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "valid": False,
                    "required": True,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": True,
                    "result_complete": False,
                    "expected_task_ids": [owner_id],
                    "missing_task_ids": [owner_id],
                },
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            recovery = recover_active_codex_tasks(app_queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        owner_after = app_queue.get_task(owner_id) or {}
        supplement_after = app_queue.get_task(supplement_id) or {}
        retry = get_delivery_retry(app_queue, owner_id)
        with app_queue.session() as db:
            rows = db.execute(
                """
                SELECT task_id, event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id IN (?,?)
                GROUP BY task_id, event_type
                """,
                (owner_id, supplement_id),
            ).fetchall()
        events = {(str(row["task_id"] or ""), str(row["event_type"] or "")): int(row["n"]) for row in rows}
        app_ok = bool(
            recovery.get("recovered") == 0
            and recovery.get("reverted") == 1
            and owner_after.get("status") == "failed"
            and supplement_after.get("status") == "pending"
            and not task_is_released_final_reply_owner(app_queue, owner_id)
            and task_is_supplement_context(app_queue, supplement_id)
            and pending_task_has_unacked_bridge_supplement(app_queue, supplement_id, "thread-protocol")
            and "owned mobile_result markers" in str(owner_after.get("error") or "")
            and retry.get("active") is False
            and not app_queue.runtime_get(task_turn_key(owner_id))
            and not push_calls
            and poll_calls
            and events.get((owner_id, "recovery_protocol_violation_no_owned_result")) == 1
            and events.get((owner_id, "app_server_protocol_violation_no_owned_result_manual_required")) == 1
            and events.get((owner_id, "active_slot_released_to_pending"), 0) == 0
            and events.get((supplement_id, "delivery_group_member"), 0) == 0
            and events.get((supplement_id, "delivery_group_member_completed"), 0) == 0
        )
        cdp_queue = MobileQueue(temp / "cdp-queue.db")
        cdp_config = {
            "queue": {"db_path": str(temp / "cdp-queue.db")},
            "security": {"allowed_users": ["protocol-violation@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "accounts": {"users": {"protocol-violation@im.wechat": {"account_id": "primary"}}},
            "openclaw": {"account_id": "primary"},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 30,
                "visible_cdp_no_owned_result_manual_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
        }
        old_stamp = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        cdp_owner_id = "protocol-primary-cdp-owner"
        with cdp_queue.session() as db:
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
                    cdp_owner_id,
                    "openclaw-weixin",
                    "protocol-violation@im.wechat",
                    "",
                    "/ask",
                    "primary cdp prompt",
                    hashlib.sha256(b"primary cdp prompt").hexdigest(),
                    cdp_owner_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "primary",
                    "thread-primary-cdp",
                    "{}",
                    old_stamp,
                    old_stamp,
                    old_stamp,
                    old_stamp,
                ),
            )
        cdp_queue.runtime_set(task_turn_key(cdp_owner_id), "turn-primary-cdp")
        cdp_queue.runtime_set(task_batch_key(cdp_owner_id), "batch-primary-cdp")
        cdp_queue.runtime_set(task_expected_ids_key(cdp_owner_id), json.dumps([cdp_owner_id], ensure_ascii=False))
        cdp_queue.runtime_set(task_ack_code_key(cdp_owner_id), "ack-primary")
        cdp_queue.runtime_set(task_result_code_key(cdp_owner_id), "result-primary")
        mark_waiting_followup_redelivery(
            cdp_queue,
            cdp_queue.get_task(cdp_owner_id) or {},
            "protocol_violation_no_owned_result",
            {"seed": "preexisting aged wait"},
        )

        cdp_poll_calls: list[dict[str, Any]] = []

        def fake_poll_primary_cdp(
            _config: dict[str, Any],
            thread_id: str,
            turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            cdp_poll_calls.append(
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "",
                "ack_seen": False,
                "result_complete": False,
                "terminal_without_text": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "valid": False,
                    "required": True,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": False,
                    "result_complete": False,
                    "expected_task_ids": [cdp_owner_id],
                    "missing_task_ids": [cdp_owner_id],
                },
            }

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_primary_cdp
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            cdp_recovery = recover_active_codex_tasks(cdp_queue, cdp_config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        cdp_after = cdp_queue.get_task(cdp_owner_id) or {}
        with cdp_queue.session() as db:
            cdp_rows = db.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=?
                GROUP BY event_type
                """,
                (cdp_owner_id,),
            ).fetchall()
        cdp_events = {str(row["event_type"] or ""): int(row["n"]) for row in cdp_rows}
        cdp_ok = bool(
            cdp_recovery.get("reverted") == 0
            and cdp_recovery.get("still_waiting") == 1
            and cdp_after.get("status") == "sent_to_codex"
            and not str(cdp_after.get("error") or "")
            and bool(cdp_queue.runtime_get(waiting_followup_redelivery_key(cdp_owner_id)))
            and bool(cdp_queue.runtime_get(task_turn_key(cdp_owner_id)))
            and cdp_events.get("recovery_waiting_followup_redelivery_skipped") == 1
            and cdp_events.get("protocol_violation_no_owned_result_manual_required", 0) == 0
            and cdp_poll_calls
        )
        return {
            "ok": app_ok and cdp_ok,
            "temp_only": True,
            "app_server_case": {
                "ok": app_ok,
                "recovery": recovery,
                "owner_status": owner_after.get("status"),
                "owner_error": owner_after.get("error"),
                "supplement_status": supplement_after.get("status"),
                "retry_reason": retry.get("reason"),
                "retry_active": retry.get("active"),
                "owner_released_final_reply_owner": task_is_released_final_reply_owner(app_queue, owner_id),
                "supplement_is_context": task_is_supplement_context(app_queue, supplement_id),
                "supplement_has_unacked_bridge_payload": pending_task_has_unacked_bridge_supplement(app_queue, supplement_id, "thread-protocol"),
                "push_calls": push_calls,
                "poll_calls": poll_calls,
                "events": {f"{task_id}:{event_type}": count for (task_id, event_type), count in events.items()},
            },
            "primary_cdp_case": {
                "ok": cdp_ok,
                "recovery": cdp_recovery,
                "owner_status": cdp_after.get("status"),
                "error": cdp_after.get("error"),
                "waiting_key_present": bool(cdp_queue.runtime_get(waiting_followup_redelivery_key(cdp_owner_id))),
                "turn_key_present": bool(cdp_queue.runtime_get(task_turn_key(cdp_owner_id))),
                "poll_calls": cdp_poll_calls,
                "events": cdp_events,
            },
            "assertion": "terminal mobile-boundary turn without owned result is bounded: app-server repeated protocol violations fail closed, while parked primary CDP waits without duplicate redelivery or terminal failure",
        }

def app_server_repair_continuation_check() -> dict[str, Any]:
    """Temp-only matrix for app-server no-result/empty-spin repair continuation."""

    def insert_active_task(queue: MobileQueue, task_id: str, text: str, stamp: str, metadata: str = "{}") -> None:
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
                    "repair-continuation@im.wechat",
                    "",
                    "/ask",
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-repair-continuation",
                    metadata,
                    stamp,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), f"turn-{task_id}-old")
        queue.runtime_set(task_batch_key(task_id), f"batch-{task_id}")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), f"ack-{task_id}")
        queue.runtime_set(task_result_code_key(task_id), f"result-{task_id}")

    def run_case(
        name: str,
        poll: dict[str, Any],
        cancel_ok: bool = True,
        dispatch_ok: bool = True,
        existing_attempt: bool = False,
        metadata: str = "{}",
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f"mobile-openclaw-repair-cont-{name}-") as temp_root:
            temp = Path(temp_root)
            queue = MobileQueue(temp / "queue.db")
            now_dt = datetime.now(timezone.utc)
            old_stamp = (now_dt - timedelta(seconds=1200)).isoformat()
            task_id = f"repair-{name}"
            insert_active_task(queue, task_id, f"repair continuation {name}", old_stamp, metadata=metadata)
            if existing_attempt:
                queue.add_event("local", "app_server_repair_continuation_started", {"seed": True}, task_id)
            config = {
                "queue": {"db_path": str(temp / "queue.db")},
                "security": {"allowed_users": ["repair-continuation@im.wechat"]},
                "safety": {"shadow_mode": False, "paused": False},
                "trigger": {
                    "delivery_mode": "codex-app-server",
                    "active_recovery_max_sent_checks_per_cycle": 10,
                    "active_recovery_cooldown_seconds": 1,
                    "active_slot_release_after_seconds": 90,
                    "waiting_ack_after_seconds": 999,
                    "app_server_repair_continuation_after_seconds": 600,
                    "delivery_timeout_seconds": 5,
                },
            }
            calls: dict[str, list[dict[str, Any]]] = {"cancel": [], "client": [], "push": []}
            original_health = globals()["check_codex_health"]
            original_poll = globals()["poll_codex_result"]
            original_cancel = globals()["cancel_codex_generation"]
            original_client = globals()["run_codex_app_server_client"]
            original_push = globals()["push_final_reply_async"]

            def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True, "healthy": True, "mode": "test"}

            def fake_poll_codex_result(
                _config: dict[str, Any],
                thread_id: str,
                turn_id: str,
                _baseline_key: str,
                _client_message_id: str = "",
                expected_task_ids: list[str] | None = None,
                expected_result_codes: dict[str, str] | None = None,
                expected_ack_codes: dict[str, str] | None = None,
            ) -> dict[str, Any]:
                result = dict(poll)
                result.setdefault("ok", True)
                result.setdefault("healthy", True)
                result.setdefault("turn_id", turn_id)
                result.setdefault("expected_task_ids", expected_task_ids or [])
                result.setdefault("expected_result_codes", expected_result_codes or {})
                result.setdefault("expected_ack_codes", expected_ack_codes or {})
                result.setdefault("thread_id", thread_id)
                return result

            def fake_cancel_codex_generation(
                _config: dict[str, Any],
                thread_id: str = "",
                turn_id: str = "",
            ) -> dict[str, Any]:
                calls["cancel"].append({"thread_id": thread_id, "turn_id": turn_id})
                return {"ok": cancel_ok, "cancelled": cancel_ok, "mode": "codex-app-server", "reason": "" if cancel_ok else "cancel_denied"}

            def fake_run_codex_app_server_client(
                _config: dict[str, Any],
                args: list[str],
                prompt: str = "",
                timeout_extra_seconds: int = 0,
            ) -> dict[str, Any]:
                calls["client"].append({"args": args, "prompt": prompt, "timeout_extra_seconds": timeout_extra_seconds})
                if not dispatch_ok:
                    return {"ok": False, "reason": "dispatch_failed"}
                client_id = args[args.index("--client-message-id") + 1] if "--client-message-id" in args else ""
                return {
                    "ok": True,
                    "mode": "codex-app-server",
                    "thread_id": "thread-repair-continuation",
                    "turn_id": f"turn-{task_id}-repair",
                    "client_user_message_id": client_id,
                    "status": "running",
                    "sync_after_dispatch": {"ok": True, "turn_readable": True},
                }

            def fake_push_final_reply_async(
                _queue: MobileQueue,
                task_arg: dict[str, Any],
                text_arg: str,
                _config_arg: dict[str, Any],
                media: str | None = None,
            ) -> dict[str, Any]:
                calls["push"].append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
                return {"ok": True}

            try:
                globals()["check_codex_health"] = fake_check_codex_health
                globals()["poll_codex_result"] = fake_poll_codex_result
                globals()["cancel_codex_generation"] = fake_cancel_codex_generation
                globals()["run_codex_app_server_client"] = fake_run_codex_app_server_client
                globals()["push_final_reply_async"] = fake_push_final_reply_async
                recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
            finally:
                globals()["check_codex_health"] = original_health
                globals()["poll_codex_result"] = original_poll
                globals()["cancel_codex_generation"] = original_cancel
                globals()["run_codex_app_server_client"] = original_client
                globals()["push_final_reply_async"] = original_push

            task_after = queue.get_task(task_id) or {}
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
            return {
                "task_id": task_id,
                "recovery": recovery,
                "task_status": task_after.get("status"),
                "turn_key": queue.runtime_get(task_turn_key(task_id)),
                "batch_key": queue.runtime_get(task_batch_key(task_id)),
                "result_code": queue.runtime_get(task_result_code_key(task_id)),
                "events": events,
                "calls": calls,
                "error": task_after.get("error"),
            }

    terminal_poll = {
        "status": "completed",
        "newText": "",
        "ack_seen": True,
        "result_complete": False,
        "terminal_without_text": True,
        "protocol": "mobile_result_boundary_v2",
        "ownership": {"valid": False, "required": True, "protocol": "mobile_result_boundary_v2", "ack_seen": True, "result_complete": False},
    }
    empty_spin_poll = {
        "status": "inProgress",
        "newText": "",
        "ack_seen": True,
        "result_complete": False,
        "terminal_without_text": False,
        "ownership": {"valid": False, "required": True, "protocol": "mobile_result_boundary_v2", "ack_seen": True, "result_complete": False},
        "in_progress_tools": [],
    }
    tool_progress_poll = {
        **empty_spin_poll,
        "in_progress_tools": [{"title": "slow_real_work", "phase": "inProgress"}],
    }
    cases = {
        "terminal_continuation": run_case("terminal", terminal_poll),
        "empty_spin_continuation": run_case("emptyspin", empty_spin_poll),
        "cancel_failed_defer": run_case("cancel-failed", empty_spin_poll, cancel_ok=False),
        "dispatch_failed_manual": run_case("dispatch-failed", empty_spin_poll, dispatch_ok=False),
        "already_attempted_manual": run_case("already-attempted", empty_spin_poll, existing_attempt=True),
        "attachment_manual": run_case("attachment", terminal_poll, metadata=json.dumps({"attachment_count": 1}, ensure_ascii=False)),
        "attachment_inprogress_manual_review": run_case(
            "attachment-inprogress",
            empty_spin_poll,
            metadata=json.dumps({"attachment_count": 1}, ensure_ascii=False),
        ),
        "tool_progress_observed": run_case("tool-progress", tool_progress_poll),
    }
    terminal_ok = cases["terminal_continuation"]
    empty_ok = cases["empty_spin_continuation"]
    cancel_failed = cases["cancel_failed_defer"]
    dispatch_failed = cases["dispatch_failed_manual"]
    already_attempted = cases["already_attempted_manual"]
    attachment = cases["attachment_manual"]
    attachment_inprogress = cases["attachment_inprogress_manual_review"]
    tool_progress = cases["tool_progress_observed"]
    def dispatch_calls(case: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            call
            for call in case["calls"]["client"]
            if "--dispatch" in [str(item) for item in call.get("args", [])]
        ]

    terminal_dispatches = dispatch_calls(terminal_ok)
    empty_dispatches = dispatch_calls(empty_ok)
    terminal_dispatch_prompt = str(terminal_dispatches[0].get("prompt", "")) if terminal_dispatches else ""
    empty_dispatch_prompt = str(empty_dispatches[0].get("prompt", "")) if empty_dispatches else ""
    ok = bool(
        terminal_ok["events"].get("app_server_repair_continuation_started") == 1
        and terminal_ok["task_status"] == "sent_to_codex"
        and str(terminal_ok["turn_key"] or "").endswith("-repair")
        and terminal_ok["result_code"] == f"result-{terminal_ok['task_id']}"
        and len(terminal_ok["calls"]["cancel"]) == 0
        and len(terminal_dispatches) == 1
        and "[[mobile_ack:" not in terminal_dispatch_prompt
        and "Do not output mobile_ack" in terminal_dispatch_prompt
        and empty_ok["events"].get("app_server_repair_continuation_started") == 1
        and len(empty_ok["calls"]["cancel"]) == 1
        and len(empty_dispatches) == 1
        and "[[mobile_ack:" not in empty_dispatch_prompt
        and "Do not output mobile_ack" in empty_dispatch_prompt
        and cancel_failed["events"].get("app_server_repair_continuation_cancel_failed") == 1
        and cancel_failed["task_status"] == "sent_to_codex"
        and dispatch_failed["events"].get("app_server_repair_continuation_failed") == 1
        and dispatch_failed["events"].get("app_server_inprogress_no_output_manual_review_required") == 1
        and dispatch_failed["task_status"] == "sent_to_codex"
        and already_attempted["events"].get("app_server_inprogress_no_output_manual_review_required") == 1
        and already_attempted["task_status"] == "sent_to_codex"
        and attachment["events"].get("attachment_terminal_without_result_manual_retry_required") == 1
        and attachment["task_status"] == "failed"
        and attachment_inprogress["events"].get("app_server_inprogress_no_output_manual_review_required") == 1
        and attachment_inprogress["events"].get("app_server_protocol_violation_no_owned_result_manual_required", 0) == 0
        and attachment_inprogress["task_status"] == "sent_to_codex"
        and tool_progress["events"].get("app_server_repair_continuation_started", 0) == 0
        and tool_progress["events"].get("app_server_protocol_violation_no_owned_result_manual_required", 0) == 0
        and tool_progress["task_status"] == "sent_to_codex"
    )
    return {
        "ok": ok,
        "temp_only": True,
        "cases": cases,
        "assertion": "app-server active repair cancels exactly one stale/no-result turn before one continuation, preserves original result markers, avoids duplicate side effects, and fails closed without alternate retries",
    }

def historical_owned_result_fallback_check() -> dict[str, Any]:
    """Temp-only check that a late owned result using an older attempt code is recovered once."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-historical-owned-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "historical-owned-task"
        thread_id = "thread-historical-owned"
        latest_turn_id = "turn-latest"
        old_turn_id = "turn-old"
        latest_batch_id = "batch-latest"
        old_batch_id = "batch-old"
        supplement_id = "historical-owned-supplement"
        with queue.session() as db:
            for tid, text, status in [
                (task_id, "owner prompt", "sent_to_codex"),
                (supplement_id, "pending supplement", "pending"),
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
                        "historical-owned@im.wechat",
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
        queue.runtime_set(task_turn_key(task_id), latest_turn_id)
        queue.runtime_set(task_batch_key(task_id), latest_batch_id)
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "latest-ack-code")
        queue.runtime_set(task_result_code_key(task_id), "latest-result-code")
        for turn_id, batch_id, ack_code, result_code in [
            (old_turn_id, old_batch_id, "old-ack-code", "old-result-code"),
            (latest_turn_id, latest_batch_id, "latest-ack-code", "latest-result-code"),
        ]:
            queue.add_event(
                "local",
                "codex_turn_started",
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "client_message_id": batch_id,
                    "expected_task_ids": [task_id],
                    "mobile_protocols": {
                        task_id: {
                            "task_id": task_id,
                            "ack_code": ack_code,
                            "result_code": result_code,
                        }
                    },
                    "delivery_mode": "codex-app-server",
                },
                task_id,
            )
        supplement_payload = {
            "base_message_id": task_id,
            "active_task_id": task_id,
            "thread_id": thread_id,
            "delivery_mode": "codex-app-server",
            "items": [task_supplement_snapshot(queue.get_task(supplement_id) or {}, thread_id)],
            "published_at": now,
            "supplement_signature": "historical-owned-supplement",
        }
        queue.runtime_set(bridge_supplement_key(thread_id), json.dumps(supplement_payload, ensure_ascii=False))
        queue.add_event(
            "local",
            "pending_backlog_supplement_pending_published",
            {
                "active_task_id": task_id,
                "thread_id": thread_id,
                "delivery_mode": "codex-app-server",
                "signature": "historical-owned-supplement",
            },
            supplement_id,
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
        }
        poll_calls: list[dict[str, Any]] = []
        push_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            thread_arg: str,
            turn_arg: str,
            _baseline_key: str,
            client_message_arg: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            call = {
                "thread_id": thread_arg,
                "turn_id": turn_arg,
                "client_message_id": client_message_arg,
                "expected_task_ids": expected_task_ids or [],
                "expected_result_codes": expected_result_codes or {},
                "expected_ack_codes": expected_ack_codes or {},
            }
            poll_calls.append(call)
            if expected_result_codes == {task_id: "old-result-code"} and turn_arg == latest_turn_id:
                return {
                    "ok": True,
                    "healthy": True,
                    "status": "completed",
                    "newText": "late owned answer",
                    "generationActive": False,
                    "result_complete": True,
                    "terminal_without_text": False,
                    "ownership": {
                        "required": True,
                        "valid": True,
                        "protocol": "mobile_result_boundary_v2",
                        "expected_task_ids": [task_id],
                        "matched_task_id": task_id,
                        "matched_result_code": "old-result-code",
                        "result_complete": True,
                    },
                }
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "",
                "generationActive": False,
                "result_complete": False,
                "terminal_without_text": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "required": True,
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "expected_task_ids": [task_id],
                    "missing_task_ids": [task_id],
                    "result_complete": False,
                },
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            first_recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
            second_recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        task_after = queue.get_task(task_id) or {}
        supplement_after = queue.get_task(supplement_id) or {}
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
            first_recovery.get("recovered") == 1
            and first_recovery.get("reverted") == 0
            and second_recovery.get("action") == "no_active_tasks"
            and task_after.get("status") == "done"
            and task_after.get("result") == "late owned answer"
            and supplement_after.get("status") == "pending"
            and task_is_supplement_context(queue, supplement_id)
            and push_calls == [{"task_id": task_id, "text": "late owned answer", "media": ""}]
            and len(poll_calls) == 2
            and poll_calls[0].get("expected_result_codes") == {task_id: "latest-result-code"}
            and poll_calls[1].get("expected_result_codes") == {task_id: "old-result-code"}
            and poll_calls[1].get("turn_id") == latest_turn_id
            and not queue.runtime_get(task_turn_key(task_id))
            and events.get("historical_owned_result_recovered") == 1
            and events.get("recovery_result_pushed") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first_recovery": first_recovery,
            "second_recovery": second_recovery,
            "task_status": task_after.get("status"),
            "task_result": task_after.get("result"),
            "supplement_status": supplement_after.get("status"),
            "supplement_is_context": task_is_supplement_context(queue, supplement_id),
            "push_calls": push_calls,
            "poll_calls": poll_calls,
            "events": events,
            "runtime_turn_present": bool(queue.runtime_get(task_turn_key(task_id))),
            "assertion": "an active task can complete once from a valid owned result emitted with an older attempt result code; later recovery cycles do not duplicate-push",
        }

def thread_history_owned_result_fallback_check() -> dict[str, Any]:
    """Temp-only check that durable thread history can recover a CDP-empty final result."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-thread-history-owned-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "thread-history-owned-task"
        thread_id = "thread-history-owned"
        turn_id = "turn-cdp-empty"
        client_message_id = "batch-thread-history"
        result_code = "thread-history-result-code"
        ack_code = "thread-history-ack-code"
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
                    "thread-history@im.wechat",
                    "",
                    "/ask",
                    "owner prompt",
                    hashlib.sha256(b"owner prompt").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "primary",
                    thread_id,
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), turn_id)
        queue.runtime_set(task_batch_key(task_id), client_message_id)
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), ack_code)
        queue.runtime_set(task_result_code_key(task_id), result_code)
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "client_message_id": client_message_id,
                "expected_task_ids": [task_id],
                "mobile_protocols": {
                    task_id: {
                        "task_id": task_id,
                        "ack_code": ack_code,
                        "result_code": result_code,
                    }
                },
                "delivery_mode": "codex-cdp",
            },
            task_id,
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
        }
        poll_calls: list[dict[str, Any]] = []
        history_calls: list[dict[str, Any]] = []
        push_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_history = globals()["poll_codex_thread_history_owned_result"]
        original_push = globals()["push_final_reply_async"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            thread_arg: str,
            turn_arg: str,
            _baseline_key: str,
            client_message_arg: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "thread_id": thread_arg,
                    "turn_id": turn_arg,
                    "client_message_id": client_message_arg,
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "mode": "codex-cdp",
                "status": "completed",
                "newText": None,
                "generationActive": False,
                "result_complete": False,
                "terminal_without_text": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "required": True,
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "expected_task_ids": [task_id],
                    "expected_result_codes": {task_id: result_code},
                    "result_complete": False,
                },
            }

        def fake_poll_codex_thread_history_owned_result(
            _config: dict[str, Any],
            thread_arg: str,
            turn_arg: str,
            client_message_arg: str,
            expected_task_ids: list[str],
            expected_result_codes: dict[str, str],
            expected_ack_codes: dict[str, str],
        ) -> dict[str, Any]:
            history_calls.append(
                {
                    "thread_id": thread_arg,
                    "turn_id": turn_arg,
                    "client_message_id": client_message_arg,
                    "expected_task_ids": expected_task_ids,
                    "expected_result_codes": expected_result_codes,
                    "expected_ack_codes": expected_ack_codes,
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "mode": "codex-thread-history",
                "thread_history_fallback": True,
                "newText": "thread history owned answer",
                "status": "completed",
                "terminal_without_text": False,
                "result_complete": True,
                "protocol": "mobile_result_boundary_v2",
                "ownership": {
                    "required": True,
                    "valid": True,
                    "protocol": "mobile_result_boundary_v2",
                    "expected_task_ids": [task_id],
                    "expected_result_codes": {task_id: result_code},
                    "matched_task_id": task_id,
                    "matched_result_code": result_code,
                    "result_complete": True,
                    "stripped_text": "thread history owned answer",
                },
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["poll_codex_thread_history_owned_result"] = fake_poll_codex_thread_history_owned_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["poll_codex_thread_history_owned_result"] = original_history
            globals()["push_final_reply_async"] = original_push

        task_after = queue.get_task(task_id) or {}
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
            recovery.get("recovered") == 1
            and task_after.get("status") == "done"
            and task_after.get("result") == "thread history owned answer"
            and poll_calls
            and history_calls
            and history_calls[0].get("expected_result_codes") == {task_id: result_code}
            and push_calls == [{"task_id": task_id, "text": "thread history owned answer", "media": ""}]
            and not queue.runtime_get(task_turn_key(task_id))
            and events.get("recovery_result_pushed") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "task_result": task_after.get("result"),
            "poll_calls": poll_calls,
            "history_calls": history_calls,
            "push_calls": push_calls,
            "events": events,
            "runtime_turn_present": bool(queue.runtime_get(task_turn_key(task_id))),
            "assertion": "CDP terminal_without_text is recovered from exact owned Codex thread history before fail-close or redelivery",
        }

def active_ack_inprogress_observation_check() -> dict[str, Any]:
    """Temp-only check that an acked in-progress turn stays observed, not redelivered."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-active-ack-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["ack-progress@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 5,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
        }
        now = datetime.now(timezone.utc).isoformat()
        task_id = "ack-progress-task"
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
                    "ack-progress@im.wechat",
                    "",
                    "/ask",
                    "acked in-progress task",
                    hashlib.sha256(b"acked in-progress task").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-ack-progress",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), "turn-ack-progress")
        queue.runtime_set(task_batch_key(task_id), "batch-ack-progress")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "ack-code")
        queue.runtime_set(task_result_code_key(task_id), "result-code")

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        poll_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "status": "inProgress",
                "newText": "",
                "ack_seen": True,
                "result_complete": False,
                "terminal_without_text": False,
                "ownership": {
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": True,
                    "result_complete": False,
                },
            }

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll

        task_after = queue.get_task(task_id) or {}
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
        cooldown = str(queue.runtime_get(active_recovery_retry_key(task_id)) or "")
        ok = bool(
            recovery.get("recovered") == 0
            and recovery.get("reverted") == 0
            and task_after.get("status") == "sent_to_codex"
            and queue.runtime_get(task_turn_key(task_id)) == "turn-ack-progress"
            and poll_calls
            and poll_calls[0].get("expected_result_codes") == {task_id: "result-code"}
            and poll_calls[0].get("expected_ack_codes") == {task_id: "ack-code"}
            and bool(cooldown)
            and events.get("active_recovery_retry_scheduled") == 1
            and events.get("unowned_intermediate_seen", 0) == 0
            and latest_task_event_payload(queue, task_id, "active_recovery_retry_scheduled").get("reason") == "ack_seen_waiting_for_owned_result"
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "poll_calls": poll_calls,
            "cooldown_set": bool(cooldown),
            "events": events,
            "assertion": "mobile ack plus in-progress status keeps the active turn observed and prevents duplicate redelivery",
        }

def waiting_followup_owned_result_recovery_check() -> dict[str, Any]:
    """Temp-only check that parked primary CDP tasks still poll for owned results."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-waiting-owned-result-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["waiting-owned-result@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 5,
                "active_slot_release_after_seconds": 30,
                "waiting_ack_after_seconds": 999,
            },
            "openclaw": {"account_id": "primary"},
        }
        now = datetime.now(timezone.utc).isoformat()
        task_id = "waiting-owned-result-task"
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
                    "waiting-owned-result@im.wechat",
                    "",
                    "/ask",
                    "parked primary task",
                    hashlib.sha256(b"parked primary task").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "primary",
                    "thread-waiting-owned-result",
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), "turn-waiting-owned-result")
        queue.runtime_set(task_batch_key(task_id), "batch-waiting-owned-result")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "ack-code")
        queue.runtime_set(task_result_code_key(task_id), "result-code")
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(task_id) or {},
            "generation_active_without_owned_result",
            {"test": "parked before owned result arrives"},
        )

        push_calls: list[dict[str, Any]] = []
        poll_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_push = globals()["push_final_reply_async"]

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "status": "completed",
                "newText": "owned final answer",
                "generationActive": False,
                "ack_seen": False,
                "result_complete": True,
                "terminal_without_text": False,
                "ownership": {
                    "required": True,
                    "protocol": "mobile_result_boundary_v2",
                    "valid": True,
                    "matched_task_id": task_id,
                    "matched_result_code": "result-code",
                    "result_complete": True,
                },
            }

        def fake_push_final_reply_async(
            _queue: MobileQueue,
            task_arg: dict[str, Any],
            text_arg: str,
            _config_arg: dict[str, Any],
            media: str | None = None,
        ) -> dict[str, Any]:
            push_calls.append({"task_id": str(task_arg.get("id") or ""), "text": text_arg, "media": media or ""})
            return {"ok": True, "mode": "test"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["push_final_reply_async"] = original_push

        task_after = queue.get_task(task_id) or {}
        waiting_key_present = bool(queue.runtime_get(waiting_followup_redelivery_key(task_id)))
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
            recovery.get("recovered") == 1
            and recovery.get("reverted") == 0
            and task_after.get("status") == "done"
            and task_after.get("result") == "owned final answer"
            and push_calls == [{"task_id": task_id, "text": "owned final answer", "media": ""}]
            and len(poll_calls) == 1
            and poll_calls[0].get("expected_result_codes") == {task_id: "result-code"}
            and not waiting_key_present
            and events.get("active_waiting_followup_redelivery") == 1
            and events.get("active_waiting_followup_redelivery_cleared") == 1
            and events.get("recovery_result_pushed") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "task_result": task_after.get("result"),
            "push_calls": push_calls,
            "poll_calls": poll_calls,
            "waiting_key_present": waiting_key_present,
            "events": events,
            "assertion": "waiting-followup state blocks redelivery only; it must not block owned result recovery",
        }

def waiting_completed_reply_evidence_check() -> dict[str, Any]:
    """Temp-only check that a waiting owner with durable push evidence is not redelivered."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-waiting-completed-evidence-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        owner_id = "owner-pushed"
        followup_id = "followup-pending"
        thread_id = "thread-visible"
        config = {
            "openclaw": {"account_id": "backup1", "phone_status_ack_events": []},
            "queue": {"db_path": str(temp / "queue.db")},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-cdp",
                "waiting_ack_after_seconds": 999,
                "delivery_retry_seconds": 30,
            },
            "threads": {
                "default_id": "test-thread",
                "items": [{"id": "test-thread", "name": "Test Thread", "thread_id": thread_id}],
            },
        }
        with queue.session() as db:
            for tid, text, status, push_status in [
                (owner_id, "owner prompt", "sent_to_codex", "pushed_to_wecom"),
                (followup_id, "followup prompt", "pending", ""),
            ]:
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
                        push_status,
                        "backup1",
                        thread_id,
                        "{}",
                        now,
                        now,
                        now if status == "pending" else now,
                        now if status != "pending" else now,
                        None,
                        now if push_status == "pushed_to_wecom" else None,
                    ),
                )
        queue.add_event("local", "codex_turn_started", {"thread_id": thread_id, "turn_id": "turn-1"}, owner_id)
        queue.add_event("wecom", "final_reply_visibility_unconfirmed", {"delivery_accepted": True}, owner_id)
        queue.add_event("wecom", "push_result", {"ok": True, "push_status": "pushed_to_wecom"}, owner_id)
        mark_waiting_followup_redelivery(
            queue,
            queue.get_task(owner_id) or {},
            "protocol_violation_no_owned_result",
            {"poll": {}},
        )
        set_active_thread(queue, "user@im.wechat", "test-thread")

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        health_calls: list[dict[str, Any]] = []
        poll_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_poll_config: dict[str, Any]) -> dict[str, Any]:
            health_calls.append({"ok": True})
            return {"healthy": True}

        def fake_poll_codex_result(
            _poll_config: dict[str, Any],
            _thread_id: str,
            _turn_id: str,
            _baseline_key: str,
            _client_message_id: str,
            _expected_task_ids: list[str] | None = None,
            _expected_result_codes: dict[str, str] | None = None,
            _expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append({"thread_id": _thread_id, "turn_id": _turn_id})
            return {"ok": True, "status": "done", "newText": "", "ownership": {"result_complete": False}}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            result = try_complete_owned_result_before_redelivery(
                queue,
                config,
                queue.get_task(owner_id) or {},
                "protocol_violation_no_owned_result",
                {"trigger_task_id": followup_id},
                trigger_task_id=followup_id,
            )
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll

        waiting_key_present = bool(queue.runtime_get(waiting_followup_redelivery_key(owner_id)))
        owner_after = queue.get_task(owner_id) or {}
        followup_after = queue.get_task(followup_id) or {}
        with queue.session() as db:
            owner_events = {
                str(row["event_type"]): int(row["n"])
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (owner_id,),
                ).fetchall()
            }
        ok = bool(
            result.get("completed")
            and owner_after.get("status") == "sent_to_codex"
            and followup_after.get("status") == "pending"
            and not waiting_key_present
            and owner_events.get("pre_redelivery_completed_reply_evidence_consumed", 0) == 1
            and len(health_calls) == 1
            and len(poll_calls) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "gate_result": result,
            "owner_status": owner_after.get("status"),
            "followup_status": followup_after.get("status"),
            "health_calls": health_calls,
            "poll_calls": poll_calls,
            "waiting_key_present": waiting_key_present,
            "owner_events": owner_events,
            "assertion": "a waiting owner with durable final-reply push evidence is treated as already completed and is not redelivered",
        }

def historical_failed_result_filter_check() -> dict[str, Any]:
    """Temp-only check that historical failed-result recovery excludes error-like result text."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-historical-failed-filter-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["failed-filter@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "accounts": {"users": {"failed-filter@im.wechat": {"account_id": "primary"}}},
            "openclaw": {"account_id": "primary"},
        }
        now = datetime.now(timezone.utc).isoformat()

        cases = [
            (
                "historical-good",
                "[[mobile_result_begin:x]]final answer[[mobile_result_end:x]]",
                "failure_close_owned_result_recovered",
            ),
            (
                "historical-mixed",
                "codex app-server client failed: Command '['node', 'x']' timed out after 80 seconds\n"
                "[[mobile_result_begin:y]]recoverable final answer[[mobile_result_end:y]]",
                "failure_close_owned_result_recovered",
            ),
            (
                "historical-bad",
                "codex app-server client failed: Command '['node', 'x']' timed out after 80 seconds",
                "",
            ),
        ]
        with queue.session() as db:
            for tid, result_text, _event_type in cases:
                prompt = f"prompt for {tid}"
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, error, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at, completed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "failed-filter@im.wechat",
                        "",
                        "/ask",
                        prompt,
                        hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        "failed",
                        result_text,
                        "",
                        "",
                        "primary",
                        "thread-filter",
                        "{}",
                        now,
                        now,
                        now,
                    ),
                )
        queue.add_event("local", "failure_close_owned_result_recovered", {"seed": "positive control"}, "historical-good")
        queue.add_event("local", "failure_close_owned_result_recovered", {"seed": "mixed transport wrapper with recoverable result"}, "historical-mixed")

        dry = recover_failed_tasks_with_result_for_reply(queue, config, apply=False, limit=10)
        dry_candidates = {str(item.get("task_id") or ""): dict(item) for item in (dry.get("candidates") or [])}
        dry_skipped = {str(item.get("task_id") or ""): str(item.get("reason") or "") for item in (dry.get("skipped") or [])}

        apply_result = recover_failed_tasks_with_result_for_reply(queue, config, apply=True, limit=10)
        good_after = queue.get_task("historical-good") or {}
        bad_after = queue.get_task("historical-bad") or {}

        ok = bool(
            dry_candidates.get("historical-good", {}).get("evidence_reason") == "failure_close_owned_result_recovered"
            and dry_candidates.get("historical-mixed", {}).get("evidence_reason") == "failure_close_owned_result_recovered"
            and "historical-bad" not in dry_candidates
            and dry_skipped.get("historical-bad") == "error_like_result_text"
            and apply_result.get("recovered_count") == 2
            and good_after.get("status") == "done"
            and good_after.get("push_status") == "reply_pending"
            and (queue.get_task("historical-mixed") or {}).get("status") == "done"
            and (queue.get_task("historical-mixed") or {}).get("push_status") == "reply_pending"
            and bad_after.get("status") == "failed"
            and str(bad_after.get("push_status") or "") == ""
        )
        return {
            "ok": ok,
            "temp_only": True,
            "dry_run": dry,
            "apply_result": apply_result,
            "good_after": {
                "status": good_after.get("status"),
                "push_status": good_after.get("push_status"),
            },
            "mixed_after": {
                "status": (queue.get_task("historical-mixed") or {}).get("status"),
                "push_status": (queue.get_task("historical-mixed") or {}).get("push_status"),
            },
            "bad_after": {
                "status": bad_after.get("status"),
                "push_status": bad_after.get("push_status"),
            },
            "assertion": "historical failed-result recovery requires durable positive evidence, allows mixed transport-wrapper results with recoverable reply text, and rejects pure transport-error result text",
        }

def failed_result_audit_recovery_consistency_check() -> dict[str, Any]:
    """Temp-only check that failed-result audit and recovery share result eligibility rules."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-failed-result-consistency-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["failed-consistency@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "accounts": {"users": {"failed-consistency@im.wechat": {"account_id": "primary"}}},
            "openclaw": {"account_id": "primary"},
        }
        now = datetime.now(timezone.utc).isoformat()
        cases = [
            (
                "pure-transport",
                "codex app-server client failed: Command '['node', 'x']' timed out after 80 seconds",
                "",
            ),
            (
                "wrapped-owned-result",
                "codex app-server client failed: Command '['node', 'x']' timed out after 80 seconds\n"
                "[[mobile_result_begin:wrapped-owned-result:abc123]]recoverable final[[mobile_result_end:wrapped-owned-result:abc123]]",
                "failure_close_owned_result_recovered",
            ),
            (
                "plain-owned-result",
                "plain final reply",
                "failure_close_owned_result_recovered",
            ),
            (
                "missing-evidence",
                "plain final reply but no durable evidence",
                "",
            ),
        ]
        with queue.session() as db:
            for tid, result_text, _event_type in cases:
                prompt = f"prompt for {tid}"
                db.execute(
                    """
                    INSERT INTO mobile_tasks(
                        id, source, external_user, external_conversation, command, text,
                        text_sha256, message_fingerprint, risk_level, status, result, error, push_status,
                        receiver_account_id, codex_thread_id, metadata_json, created_at, updated_at, completed_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        "openclaw-weixin",
                        "failed-consistency@im.wechat",
                        "",
                        "/ask",
                        prompt,
                        hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        tid,
                        "L1",
                        "failed",
                        result_text,
                        "",
                        "",
                        "primary",
                        "thread-consistency",
                        "{}",
                        now,
                        now,
                        now,
                    ),
                )
        for tid, _result_text, event_type in cases:
            if event_type:
                queue.add_event("local", event_type, {"seed": "consistency check"}, tid)

        audit = audit_failed_tasks_with_result_for_reply(queue, limit=10)
        apply_result = recover_failed_tasks_with_result_for_reply(queue, config, apply=True, limit=10)
        eligible_ids = {str(item.get("task_id") or "") for item in audit.get("eligible") or []}
        excluded = {str(item.get("task_id") or ""): str(item.get("excluded_reason") or "") for item in audit.get("excluded") or []}
        recovered_ids = {str(item.get("task_id") or "") for item in apply_result.get("recovered") or []}
        skipped = {str(item.get("task_id") or ""): str(item.get("reason") or "") for item in apply_result.get("skipped") or []}

        ok = bool(
            eligible_ids == {"wrapped-owned-result", "plain-owned-result"}
            and recovered_ids == eligible_ids
            and excluded.get("pure-transport") == "error_like_result_text"
            and skipped.get("pure-transport") == "error_like_result_text"
            and excluded.get("missing-evidence") == "missing_durable_owned_result_evidence"
            and skipped.get("missing-evidence") == "missing_durable_owned_result_evidence"
            and (queue.get_task("wrapped-owned-result") or {}).get("push_status") == "reply_pending"
            and (queue.get_task("plain-owned-result") or {}).get("push_status") == "reply_pending"
            and str((queue.get_task("pure-transport") or {}).get("push_status") or "") == ""
        )
        return {
            "ok": ok,
            "temp_only": True,
            "audit": audit,
            "apply_result": apply_result,
            "eligible_ids": sorted(eligible_ids),
            "recovered_ids": sorted(recovered_ids),
            "excluded": excluded,
            "skipped": skipped,
            "assertion": "failed-result audit and recovery use the same transport-error and durable-owned-result eligibility boundary",
        }

def active_stalled_tool_recovery_check() -> dict[str, Any]:
    """Temp-only check that a stale acked quick-tool hang is cancelled and requeued."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-stalled-tool-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": ["stalled-tool@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 10,
                "active_recovery_cooldown_seconds": 5,
                "active_slot_release_after_seconds": 90,
                "waiting_ack_after_seconds": 999,
                "stalled_tool_recovery_after_seconds": 300,
                "stalled_tool_recovery_allowlist": ["load_workspace_dependencies"],
            },
        }
        now_dt = datetime.now(timezone.utc)
        old_stamp = (now_dt - timedelta(seconds=420)).isoformat()
        task_id = "stalled-tool-task"
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
                    "stalled-tool@im.wechat",
                    "",
                    "/ask",
                    "stalled tool task",
                    hashlib.sha256(b"stalled tool task").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    "thread-stalled-tool",
                    "{}",
                    old_stamp,
                    old_stamp,
                    old_stamp,
                    old_stamp,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), "turn-stalled-tool")
        queue.runtime_set(task_batch_key(task_id), "batch-stalled-tool")
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "ack-code")
        queue.runtime_set(task_result_code_key(task_id), "result-code")

        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        original_cancel = globals()["cancel_codex_generation"]
        poll_calls: list[dict[str, Any]] = []
        cancel_calls: list[dict[str, Any]] = []

        def fake_check_codex_health(_config: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "healthy": True, "mode": "test"}

        def fake_poll_codex_result(
            _config: dict[str, Any],
            thread_id: str,
            turn_id: str,
            _baseline_key: str,
            _client_message_id: str = "",
            expected_task_ids: list[str] | None = None,
            expected_result_codes: dict[str, str] | None = None,
            expected_ack_codes: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            poll_calls.append(
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "expected_task_ids": expected_task_ids or [],
                    "expected_result_codes": expected_result_codes or {},
                    "expected_ack_codes": expected_ack_codes or {},
                }
            )
            return {
                "ok": True,
                "healthy": True,
                "status": "inProgress",
                "newText": "",
                "ack_seen": True,
                "result_complete": False,
                "terminal_without_text": False,
                "ownership": {
                    "valid": False,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": True,
                    "result_complete": False,
                },
                "in_progress_tools": [
                    {
                        "id": "tool-1",
                        "type": "dynamicToolCall",
                        "title": "load_workspace_dependencies",
                        "server": "",
                        "phase": "inProgress",
                        "text": "",
                    }
                ],
            }

        def fake_cancel_codex_generation(
            _config: dict[str, Any],
            thread_id: str = "",
            turn_id: str = "",
        ) -> dict[str, Any]:
            cancel_calls.append({"thread_id": thread_id, "turn_id": turn_id})
            return {"ok": True, "cancelled": True, "mode": "codex-app-server"}

        try:
            globals()["check_codex_health"] = fake_check_codex_health
            globals()["poll_codex_result"] = fake_poll_codex_result
            globals()["cancel_codex_generation"] = fake_cancel_codex_generation
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=10)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
            globals()["cancel_codex_generation"] = original_cancel

        task_after = queue.get_task(task_id) or {}
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
        release_payload = latest_task_event_payload(queue, task_id, "active_slot_released_to_pending")
        requeue_payload = latest_task_event_payload(queue, task_id, "recovery_stalled_tool_requeued")
        retry_payload = latest_task_event_payload(queue, task_id, "delivery_retry_scheduled")
        ok = bool(
            recovery.get("reverted") == 1
            and task_after.get("status") == "pending"
            and not queue.runtime_get(task_turn_key(task_id))
            and poll_calls
            and cancel_calls == [{"thread_id": "thread-stalled-tool", "turn_id": "turn-stalled-tool"}]
            and events.get("recovery_stalled_tool_requeued") == 1
            and events.get("active_slot_released_to_pending") == 1
            and release_payload.get("reason") == "stalled_tool_call_without_owned_result"
            and retry_payload.get("reason") == "stalled_tool_call_without_owned_result"
            and requeue_payload.get("stalled_tool", {}).get("matched_tools")
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "poll_calls": poll_calls,
            "cancel_calls": cancel_calls,
            "events": events,
            "release_reason": release_payload.get("reason"),
            "retry_reason": retry_payload.get("reason"),
            "assertion": "acked in-progress mobile turn stuck on an allowlisted quick tool is cancelled before FIFO redelivery",
        }

def active_progress_observability_check() -> dict[str, Any]:
    """Temp-only check for active poll progress labels and maintenance output."""
    initial = classify_active_poll_observation(
        {"status": "inProgress", "ack_seen": True, "newText": "", "result_complete": False, "in_progress_tools": []},
        20,
        delivery_mode="codex-app-server",
        waiting_ack_after_seconds=60,
        continuation_after_seconds=600,
    )
    waiting = classify_active_poll_observation(
        {"status": "inProgress", "ack_seen": True, "newText": "", "result_complete": False, "in_progress_tools": []},
        120,
        delivery_mode="codex-app-server",
        waiting_ack_after_seconds=60,
        continuation_after_seconds=600,
    )
    continuation_window = classify_active_poll_observation(
        {"status": "inProgress", "ack_seen": True, "newText": "", "result_complete": False, "in_progress_tools": []},
        700,
        delivery_mode="codex-app-server",
        waiting_ack_after_seconds=60,
        continuation_after_seconds=600,
    )
    tool_progress = classify_active_poll_observation(
        {
            "status": "inProgress",
            "ack_seen": True,
            "newText": "",
            "result_complete": False,
            "in_progress_tools": [{"title": "slow_real_work", "phase": "inProgress"}],
        },
        700,
        delivery_mode="codex-app-server",
        waiting_ack_after_seconds=60,
        continuation_after_seconds=600,
    )
    completed = classify_active_poll_observation(
        {"status": "completed", "newText": "done", "result_complete": True},
        30,
        delivery_mode="codex-app-server",
    )
    complete_empty = classify_active_poll_observation(
        {"status": "completed", "newText": "", "result_complete": True},
        30,
        delivery_mode="visible-cdp",
    )
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-progress-observe-", ignore_cleanup_errors=True) as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        old_stamp = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        task_id = "progress-observe"
        with queue.session() as db:
            db.execute(
                """
                INSERT INTO mobile_tasks(
                    id, source, external_user, external_conversation, command, text,
                    text_sha256, message_fingerprint, risk_level, status,
                    receiver_account_id, codex_thread_id, metadata_json,
                    created_at, updated_at, queued_for_codex_at, sent_to_codex_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    "openclaw-weixin",
                    "progress@im.wechat",
                    "",
                    "/ask",
                    "progress observation",
                    hashlib.sha256(b"progress observation").hexdigest(),
                    "progress-observe-fingerprint",
                    "L1",
                    "sent_to_codex",
                    "backup1",
                    "thread-progress",
                    "{}",
                    old_stamp,
                    old_stamp,
                    old_stamp,
                    old_stamp,
                ),
            )
        observation = record_active_poll_observation(
            queue,
            task_id,
            {
                "status": "inProgress",
                "ack_seen": True,
                "newText": "",
                "result_complete": False,
                "in_progress_tools": [],
            },
            700,
            delivery_mode="codex-app-server",
            waiting_ack_after_seconds=60,
            continuation_after_seconds=600,
        )
        from mobile_maintenance import active_observation_buckets, diagnose_system

        active = [
            {
                "id": task_id,
                "status": "sent_to_codex",
                "account": "backup1",
                "receiver_account_id": "backup1",
                "delivery_mode": "codex-app-server",
                "codex_thread_id": "thread-progress",
                "route_key": "codex-app-server:backup1:thread-progress",
                "age_seconds": 700,
            }
        ]
        buckets = active_observation_buckets(
            active,
            {"codex_app_server": {"ok": True}},
            db_path=temp / "queue.db",
            threshold_seconds=300,
        )
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
            "active": active,
            "pending": [],
            "reply_problems": [],
            "routes": {},
            "recent_events": {},
            "event_noise": {},
            "active_observation": buckets,
            "top_active_routes": [],
            "top_pending_routes": [],
            "top_accounts": [],
            "dashboard_live_state": {"ok": True},
        }
        diagnosis = diagnose_system(snapshot)
        issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
        active_issue = next((item for item in issues if isinstance(item, dict) and item.get("code") == "active_tasks_observing"), {})
    ok = bool(
        initial.get("stage") == "inprogress_no_output_initial"
        and waiting.get("stage") == "inprogress_no_output_after_wait_ack"
        and continuation_window.get("stage") == "inprogress_no_output_continuation_window"
        and tool_progress.get("stage") == "tool_in_progress"
        and completed.get("stage") == "completed_result_available"
        and complete_empty.get("stage") == "owned_result_boundary_complete_but_text_empty"
        and observation.get("stage") == "inprogress_no_output_continuation_window"
        and buckets.get("progress_stage_counts", {}).get("inprogress_no_output_continuation_window") == 1
        and active_issue.get("severity") == "low"
        and active_issue.get("safe_auto_fix") in {None, ""}
    )
    return {
        "ok": ok,
        "temp_only": True,
        "classifications": {
            "initial": initial,
            "waiting": waiting,
            "continuation_window": continuation_window,
            "tool_progress": tool_progress,
            "completed": completed,
            "complete_empty": complete_empty,
        },
        "bucket_stage_counts": buckets.get("progress_stage_counts"),
        "active_issue": active_issue,
        "assertion": "active poll observations add progress labels for maintenance without becoming an automatic repair trigger",
    }

def app_server_result_poll_second_chance_check() -> dict[str, Any]:
    """Temp-only check that app-server cold poll timeout gets one retry without redelivery."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-app-poll-warmup-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        now = datetime.now(timezone.utc).isoformat()
        task_id = "app-poll-warmup-task"
        thread_id = "thread-app-poll-warmup"
        turn_id = "turn-app-poll-warmup"
        batch_id = "batch-app-poll-warmup"
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
                    "backup1-user@im.wechat",
                    "",
                    "/ask",
                    "second chance app-server result poll",
                    hashlib.sha256(b"second chance app-server result poll").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    thread_id,
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), turn_id)
        queue.runtime_set(task_batch_key(task_id), batch_id)
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "ack-code")
        queue.runtime_set(task_result_code_key(task_id), "result-code")
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "delivery_timeout_seconds": 1,
                "app_server_result_poll_second_chance_timeout_seconds": 7,
                "active_recovery_max_sent_checks_per_cycle": 1,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 30,
            },
        }
        run_calls: list[dict[str, Any]] = []
        push_calls: list[dict[str, Any]] = []
        original_health = globals()["check_codex_health"]
        original_ensure = globals()["ensure_codex_app_server"]
        original_run_client = globals()["run_codex_app_server_client"]
        original_push = globals()["push_final_reply_async"]
        try:
            globals()["check_codex_health"] = lambda _config: {"ok": True, "healthy": True, "mode": "codex-app-server"}
            globals()["ensure_codex_app_server"] = lambda _config: {
                "ok": True,
                "started": False,
                "host": "127.0.0.1",
                "port": 18791,
            }

            def fake_run_codex_app_server_client(
                config_arg: dict[str, Any],
                args_arg: list[str],
                prompt: str = "",
                timeout_extra_seconds: int = 0,
            ) -> dict[str, Any]:
                run_calls.append(
                    {
                        "args": list(args_arg),
                        "timeout": int((config_arg.get("trigger") or {}).get("delivery_timeout_seconds") or 0),
                        "timeout_extra_seconds": timeout_extra_seconds,
                    }
                )
                if len(run_calls) == 1:
                    return {
                        "ok": False,
                        "healthy": False,
                        "mode": "codex-app-server",
                        "reason": "codex app-server client failed: timeout thread/turns/list",
                    }
                return {
                    "ok": True,
                    "healthy": True,
                    "mode": "codex-app-server",
                    "status": "completed",
                    "newText": "second chance final answer",
                    "result_complete": True,
                    "terminal_without_text": False,
                    "ownership": {
                        "required": True,
                        "valid": True,
                        "expected_task_ids": [task_id],
                        "matched_task_id": task_id,
                        "matched_result_code": "result-code",
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

            globals()["run_codex_app_server_client"] = fake_run_codex_app_server_client
            globals()["push_final_reply_async"] = fake_push_final_reply_async
            recovery = recover_active_codex_tasks(queue, config, max_sent_checks=1)
        finally:
            globals()["check_codex_health"] = original_health
            globals()["ensure_codex_app_server"] = original_ensure
            globals()["run_codex_app_server_client"] = original_run_client
            globals()["push_final_reply_async"] = original_push
        task_after = queue.get_task(task_id) or {}
        with queue.session() as db:
            event_count = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM mobile_events
                WHERE task_id=? AND event_type='app_server_result_poll_second_chance'
                """,
                (task_id,),
            ).fetchone()["n"]
        ok = bool(
            recovery.get("recovered") == 1
            and recovery.get("reverted") == 0
            and task_after.get("status") == "done"
            and task_after.get("result") == "second chance final answer"
            and len(run_calls) == 2
            and run_calls[0].get("timeout") == 1
            and run_calls[1].get("timeout") == 7
            and push_calls == [{"task_id": task_id, "text": "second chance final answer"}]
            and int(event_count or 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "recovery": recovery,
            "task_status": task_after.get("status"),
            "task_result": task_after.get("result"),
            "run_calls": run_calls,
            "push_calls": push_calls,
            "second_chance_event_count": int(event_count or 0),
            "assertion": "app-server result poll timeout gets one longer second chance and completes the existing active task without redelivery",
        }

def app_server_turn_materialization_window_check() -> dict[str, Any]:
    """Temp-only check that fresh app-server notFound waits but stale notFound releases."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-app-turn-materialization-") as temp_root:
        temp = Path(temp_root)
        queue = MobileQueue(temp / "queue.db")
        task_id = "app-turn-materializing-task"
        thread_id = "thread-app-turn-materializing"
        turn_id = "turn-app-turn-materializing"
        batch_id = "batch-app-turn-materializing"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
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
                    "backup1-materializing@im.wechat",
                    "",
                    "/ask",
                    "materializing app-server turn",
                    hashlib.sha256(b"materializing app-server turn").hexdigest(),
                    task_id,
                    "L1",
                    "sent_to_codex",
                    "",
                    "",
                    "backup1",
                    thread_id,
                    "{}",
                    now,
                    now,
                    now,
                    now,
                ),
            )
        queue.runtime_set(task_turn_key(task_id), turn_id)
        queue.runtime_set(task_batch_key(task_id), batch_id)
        queue.runtime_set(task_expected_ids_key(task_id), json.dumps([task_id], ensure_ascii=False))
        queue.runtime_set(task_ack_code_key(task_id), "ack-code")
        queue.runtime_set(task_result_code_key(task_id), "result-code")
        queue.add_event(
            "local",
            "codex_turn_started",
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "client_message_id": batch_id,
                "expected_task_ids": [task_id],
                "delivery_mode": "codex-app-server",
            },
            task_id,
        )
        config = {
            "queue": {"db_path": str(temp / "queue.db")},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "active_recovery_max_sent_checks_per_cycle": 1,
                "active_recovery_cooldown_seconds": 1,
                "active_slot_release_after_seconds": 300,
                "app_server_turn_materialization_grace_seconds": 60,
            },
        }
        original_health = globals()["check_codex_health"]
        original_poll = globals()["poll_codex_result"]
        try:
            globals()["check_codex_health"] = lambda _config: {"ok": True, "healthy": True, "mode": "codex-app-server"}

            def fake_not_found_poll(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "ok": True,
                    "healthy": True,
                    "mode": "codex-app-server",
                    "newText": None,
                    "status": "notFound",
                    "turn_id": turn_id,
                    "protocol": "mobile_result_boundary_v2",
                    "ack_seen": False,
                    "result_complete": False,
                    "ownership": {
                        "required": True,
                        "protocol": "mobile_result_boundary_v2",
                        "valid": False,
                        "expected_task_ids": [task_id],
                        "result_complete": False,
                    },
                }

            globals()["poll_codex_result"] = fake_not_found_poll
            first_recovery = recover_active_codex_tasks(queue, config, max_sent_checks=1)
            first_after = queue.get_task(task_id) or {}
            first_wait_event = latest_task_event_payload(queue, task_id, "app_server_turn_materialization_waiting")

            stale_at = (now_dt - timedelta(seconds=120)).isoformat()
            with queue.session() as db:
                db.execute(
                    """
                    UPDATE mobile_tasks
                    SET sent_to_codex_at=?, queued_for_codex_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (stale_at, stale_at, stale_at, task_id),
                )
                db.execute(
                    """
                    UPDATE mobile_events
                    SET created_at=?
                    WHERE task_id=? AND event_type='codex_turn_started'
                    """,
                    (stale_at, task_id),
                )
            queue.runtime_delete(active_recovery_retry_key(task_id))
            second_recovery = recover_active_codex_tasks(queue, config, max_sent_checks=1)
            second_after = queue.get_task(task_id) or {}
        finally:
            globals()["check_codex_health"] = original_health
            globals()["poll_codex_result"] = original_poll
        with queue.session() as db:
            events = {
                row["event_type"]: int(row["n"] or 0)
                for row in db.execute(
                    """
                    SELECT event_type, COUNT(*) AS n
                    FROM mobile_events
                    WHERE task_id=?
                    GROUP BY event_type
                    """,
                    (task_id,),
                )
            }
        ok = bool(
            first_recovery.get("recovered") == 0
            and first_recovery.get("reverted") == 0
            and first_after.get("status") == "sent_to_codex"
            and first_wait_event.get("reason") == "within_materialization_window"
            and second_recovery.get("reverted") == 1
            and second_after.get("status") == "pending"
            and events.get("app_server_turn_materialization_waiting") == 1
            and events.get("active_slot_released_to_pending") == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first_recovery": first_recovery,
            "second_recovery": second_recovery,
            "first_status": first_after.get("status"),
            "second_status": second_after.get("status"),
            "first_wait_event": first_wait_event,
            "event_counts": events,
            "assertion": "fresh app-server turn/start notFound is observed inside a bounded materialization window, while stale notFound still releases to pending",
        }


def session_store_owned_result_fallback_check() -> dict[str, Any]:
    """Complete-empty and stale-thread reads fall back to exact session evidence."""

    class QueueStub:
        def get_task(self, _task_id: str) -> dict[str, Any]:
            return {"created_at": "2026-07-15T00:00:00Z"}

    task_id = "session-fallback-task"
    result_code = "session-result-code"
    ack_code = "session-ack-code"
    current_poll = {
        "ok": True,
        "newText": None,
        "result_complete": True,
        "owned_result_boundary_complete_but_text_empty": True,
        "ownership": {"valid": True, "result_complete": True, "stripped_text": ""},
    }
    original_historical = globals()["poll_historical_owned_codex_result"]
    original_thread = globals()["poll_codex_thread_history_owned_result"]
    original_session = globals()["find_codex_session_owned_result"]
    globals()["poll_historical_owned_codex_result"] = lambda *_args, **_kwargs: {}
    globals()["poll_codex_thread_history_owned_result"] = lambda *_args, **_kwargs: {
        "ok": False,
        "reason": "no rollout found for thread id stale-thread",
    }
    globals()["find_codex_session_owned_result"] = lambda *_args, **_kwargs: {
        "ok": True,
        "newText": "exact recovered result",
        "result_complete": True,
        "session_store_recovery": True,
        "ownership": {"valid": True, "result_complete": True, "stripped_text": "exact recovered result"},
        "source": {"thread_id": "visible-thread", "sha256": "abc123", "source_line": 42},
    }
    try:
        recovered, text, complete = recover_owned_result_from_history_sources(
            QueueStub(),
            {},
            {},
            task_id,
            "stale-thread",
            "cdp-visible-turn",
            "client-message",
            [task_id],
            {task_id: result_code},
            {task_id: ack_code},
            current_poll,
        )
    finally:
        globals()["poll_historical_owned_codex_result"] = original_historical
        globals()["poll_codex_thread_history_owned_result"] = original_thread
        globals()["find_codex_session_owned_result"] = original_session
    ok = bool(
        text == "exact recovered result"
        and complete
        and recovered.get("session_store_recovery") is True
        and recovered.get("source", {}).get("thread_id") == "visible-thread"
    )
    return {
        "ok": ok,
        "temp_only": True,
        "text": text,
        "complete": complete,
        "source": recovered.get("source"),
        "assertion": "complete-empty visible polling and stale configured thread history recover once from exact visible-session evidence",
    }


_CHECKS = {
    "app_server_turn_materialization_window_check": app_server_turn_materialization_window_check,
    "app_server_result_poll_second_chance_check": app_server_result_poll_second_chance_check,
    "active_progress_observability_check": active_progress_observability_check,
    "active_stalled_tool_recovery_check": active_stalled_tool_recovery_check,
    "failed_result_audit_recovery_consistency_check": failed_result_audit_recovery_consistency_check,
    "historical_failed_result_filter_check": historical_failed_result_filter_check,
    "waiting_completed_reply_evidence_check": waiting_completed_reply_evidence_check,
    "waiting_followup_owned_result_recovery_check": waiting_followup_owned_result_recovery_check,
    "active_ack_inprogress_observation_check": active_ack_inprogress_observation_check,
    "waiting_followup_owned_result_redelivery_gate_check": waiting_followup_owned_result_redelivery_gate_check,
    "base_ack_only_terminal_redelivery_check": base_ack_only_terminal_redelivery_check,
    "failure_close_owned_result_recovery_check": failure_close_owned_result_recovery_check,
    "protocol_violation_no_owned_result_check": protocol_violation_no_owned_result_check,
    "app_server_repair_continuation_check": app_server_repair_continuation_check,
    "historical_owned_result_fallback_check": historical_owned_result_fallback_check,
    "thread_history_owned_result_fallback_check": thread_history_owned_result_fallback_check,
    "session_store_owned_result_fallback_check": session_store_owned_result_fallback_check,
}
