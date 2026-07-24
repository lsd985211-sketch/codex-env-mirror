"""Control, onboarding, and prompt-contract regression checks for the mobile bridge.

Owns: temp-only self-tests for OpenClaw account onboarding, mobile repair/control
receipt contracts, compact mobile execution prompts, permission prompt shape, and
owned-result marker parsing.
Non-goals: production control-command execution, permission enforcement,
thread-route mutation, or reply delivery.
State behavior: checks use synthetic queues/config files and may monkeypatch CLI
helpers; each check is rebound to the CLI global namespace to preserve legacy
fixture behavior after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_control_contract_regression_check(name: str, env: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a moved control/onboarding regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown control contract regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound(*args, **kwargs)

def auto_onboarding_check() -> dict[str, Any]:
    """Exercise auto-onboarding against temporary config/db only."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-onboarding-") as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        reuse_user = "probe-reuse@im.wechat"
        create_user = "probe-create@im.wechat"
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["backup1", "backup2"], ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "backup1.json").write_text(
            json.dumps({"userId": reuse_user, "token": "token-reuse"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "backup2.json").write_text(
            json.dumps({"userId": create_user, "token": "token-create"}, ensure_ascii=False),
            encoding="utf-8",
        )
        config_path = temp / "config.local.json"
        config_data: dict[str, Any] = {
            "openclaw": {
                "account_id": "backup1",
                "state_dir": str(state_dir),
            },
            "queue": {
                "db_path": str(temp / "mobile_openclaw_bridge.db"),
            },
            "security": {
                "allowed_users": [],
            },
            "safety": {
                "shadow_mode": False,
                "paused": False,
            },
            "trigger": {
                "delivery_mode": "codex-app-server",
                "codex_app_server_host": "127.0.0.1",
                "codex_app_server_port": 18791,
                "delivery_timeout_seconds": 2,
            },
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": "weixin-user-probe-reuse",
                        "name": "微信用户 probe-reuse 独立对话",
                        "description": f"微信用户 {reuse_user} 的独立对话线程",
                        "aliases": ["probe-reuse"],
                        "thread_id": "thread-reuse",
                    }
                ],
            },
        }
        save_config(config_path, config_data)
        config = load_config(config_path)
        config["_config_path"] = str(config_path)
        queue = queue_from_config(config)

        reuse_result = auto_create_thread_route_for_user(queue, config, reuse_user)
        reuse_route = get_active_thread(queue, config, reuse_user, use_default=False)

        original_create = globals()["create_codex_thread_app_server"]
        original_inspect = globals()["inspect_codex_thread_app_server"]

        def fake_create_codex_thread_app_server(_config: dict[str, Any], thread_name: str) -> dict[str, Any]:
            return {
                "ok": True,
                "mode": "test",
                "thread_id": "thread-created",
                "thread": {"id": "thread-created"},
                "name": thread_name,
            }

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "mode": "test",
                "thread_id": thread_id,
                "listed": True,
                "listed_status": "idle",
                "listed_title": thread_name,
                "stabilize_name": stabilize_name,
            }

        try:
            globals()["create_codex_thread_app_server"] = fake_create_codex_thread_app_server
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            create_result = auto_create_thread_route_for_user(queue, config, create_user)
        finally:
            globals()["create_codex_thread_app_server"] = original_create
            globals()["inspect_codex_thread_app_server"] = original_inspect

        create_route = get_active_thread(queue, config, create_user, use_default=False)
        persisted = load_config(config_path)
        persisted_items = persisted.get("threads", {}).get("items", [])
        created_items = [
            item for item in persisted_items
            if str(item.get("description") or "").find(create_user) >= 0
        ]
        ok = bool(
            reuse_result.get("ok")
            and not reuse_result.get("created")
            and reuse_route
            and reuse_route.get("thread_id") == "thread-reuse"
            and create_result.get("ok")
            and create_result.get("created")
            and create_route
            and create_route.get("thread_id") == "thread-created"
            and len(created_items) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "reuse": {
                "ok": bool(reuse_result.get("ok")),
                "created": bool(reuse_result.get("created")),
                "thread_id": reuse_route.get("thread_id") if reuse_route else "",
            },
            "create": {
                "ok": bool(create_result.get("ok")),
                "created": bool(create_result.get("created")),
                "thread_id": create_route.get("thread_id") if create_route else "",
                "persisted_items": len(created_items),
            },
        }


def account_onboarding_sync_check() -> dict[str, Any]:
    """Exercise QR-login account sync using only temp config/db and fake Codex thread creation."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-account-sync-") as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        existing_user = "existing-account@im.wechat"
        new_user = "new-account@im.wechat"
        missing_token_user = "missing-token@im.wechat"
        fail_user = "failroute@im.wechat"
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["backup1", "backup2", "backup3", "backup4"], ensure_ascii=False),
            encoding="utf-8",
        )
        account_data = {
            "backup1": {"userId": existing_user, "token": "token-existing"},
            "backup2": {"userId": new_user, "token": "token-new"},
            "backup3": {"userId": missing_token_user},
            "backup4": {"userId": fail_user, "token": "token-fail"},
        }
        for account_id, data in account_data.items():
            (accounts_dir / f"{account_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        config_path = temp / "config.local.json"
        existing_thread_key = onboarding_thread_placeholder_id(existing_user)
        config_data: dict[str, Any] = {
            "openclaw": {"account_id": "primary", "state_dir": str(state_dir)},
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": []},
            "safety": {"shadow_mode": False, "paused": False},
            "threads": {
                "default_id": "",
                "items": [
                    {
                        "id": existing_thread_key,
                        "name": onboarding_thread_name(existing_user),
                        "description": f"微信用户 {existing_user} 的独立对话线程",
                        "aliases": [existing_user.split("@", 1)[0]],
                        "thread_id": "thread-existing",
                    }
                ],
            },
            "trigger": {"delivery_mode": "codex-app-server", "codex_app_server_port": 18791},
        }
        save_config(config_path, config_data)
        config = load_config(config_path)
        config["_config_path"] = str(config_path)
        queue = queue_from_config(config)
        dry_run = account_onboarding_sync(queue, config, apply=False)

        original_create = globals()["create_codex_thread_app_server"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        created_names: list[str] = []

        def fake_create_codex_thread_app_server(_config: dict[str, Any], thread_name: str) -> dict[str, Any]:
            created_names.append(thread_name)
            if fail_user.split("@", 1)[0][:10] in thread_name:
                return {"ok": False, "mode": "test", "reason": "forced failure"}
            return {"ok": True, "mode": "test", "thread_id": f"thread-{len(created_names)}", "thread": {"id": f"thread-{len(created_names)}"}}

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "mode": "test",
                "thread_id": thread_id,
                "listed": True,
                "listed_status": "idle",
                "listed_title": thread_name,
                "stabilize_name": stabilize_name,
                "light": light,
            }

        try:
            globals()["create_codex_thread_app_server"] = fake_create_codex_thread_app_server
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            applied = account_onboarding_sync(queue, config, apply=True)
        finally:
            globals()["create_codex_thread_app_server"] = original_create
            globals()["inspect_codex_thread_app_server"] = original_inspect

        persisted = load_config(config_path)
        items = persisted.get("threads", {}).get("items", [])
        new_items = [item for item in items if new_user in str(item.get("description") or "")]
        fail_items = [item for item in items if fail_user in str(item.get("description") or "")]
        existing_items = [item for item in items if existing_user in str(item.get("description") or "")]
        user_rows = []
        with queue.session() as db:
            user_rows = [dict(row) for row in db.execute("SELECT external_user, allow_trigger FROM mobile_users ORDER BY external_user").fetchall()]
        ok = bool(
            not dry_run.get("applied")
            and int((dry_run.get("before") or {}).get("missing_count") or 0) == 2
            and not any(action.get("action") == "create_thread_route" for action in dry_run.get("actions") or [])
            and not applied.get("ok")
            and len(new_items) == 1
            and len(fail_items) == 0
            and len(existing_items) == 1
            and missing_token_user not in json.dumps(persisted, ensure_ascii=False)
            and any(row.get("external_user") == new_user and int(row.get("allow_trigger") or 0) == 1 for row in user_rows)
            and any(item.get("external_user") == fail_user for item in applied.get("failed") or [])
        )
        return {
            "ok": ok,
            "temp_only": True,
            "dry_run_missing": int((dry_run.get("before") or {}).get("missing_count") or 0),
            "applied_ok": bool(applied.get("ok")),
            "created_thread_names": created_names,
            "new_route_count": len(new_items),
            "failed_route_count": len(fail_items),
            "existing_route_count": len(existing_items),
            "failed": applied.get("failed", []),
            "user_rows": user_rows,
        }


def account_onboarding_worker_lifecycle_check() -> dict[str, Any]:
    """Temp-only check for worker-side QR account onboarding drift repair."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-worker-onboarding-") as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["backup2", "backup3"], ensure_ascii=False),
            encoding="utf-8",
        )
        new_user = "worker-new@im.wechat"
        fail_user = "worker-fail@im.wechat"
        (accounts_dir / "backup2.json").write_text(
            json.dumps({"userId": new_user, "token": "token-new"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "backup3.json").write_text(
            json.dumps({"userId": fail_user, "token": "token-fail"}, ensure_ascii=False),
            encoding="utf-8",
        )
        config_path = temp / "config.local.json"
        config_data: dict[str, Any] = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
                "account_onboarding_worker_sync_cooldown_seconds": 600,
            },
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": []},
            "safety": {"shadow_mode": False, "paused": False},
            "threads": {"default_id": "", "items": []},
            "trigger": {"delivery_mode": "codex-app-server", "codex_app_server_port": 18791},
        }
        save_config(config_path, config_data)
        config = load_config(config_path)
        config["_config_path"] = str(config_path)
        queue = queue_from_config(config)

        original_create = globals()["create_codex_thread_app_server"]
        original_inspect = globals()["inspect_codex_thread_app_server"]
        create_calls: list[str] = []

        def fake_create_codex_thread_app_server(_config: dict[str, Any], thread_name: str) -> dict[str, Any]:
            create_calls.append(thread_name)
            if fail_user.split("@", 1)[0][:10] in thread_name:
                return {"ok": False, "mode": "test", "reason": "forced failure"}
            return {
                "ok": True,
                "mode": "test",
                "thread_id": f"worker-thread-{len(create_calls)}",
                "thread": {"id": f"worker-thread-{len(create_calls)}"},
            }

        def fake_inspect_codex_thread_app_server(
            _config: dict[str, Any],
            thread_id: str,
            thread_name: str = "",
            stabilize_name: bool = False,
            light: bool = False,
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "healthy": True,
                "mode": "test",
                "thread_id": thread_id,
                "listed": True,
                "listed_status": "idle",
                "listed_title": thread_name,
                "stabilize_name": stabilize_name,
                "light": light,
            }

        try:
            globals()["create_codex_thread_app_server"] = fake_create_codex_thread_app_server
            globals()["inspect_codex_thread_app_server"] = fake_inspect_codex_thread_app_server
            first = worker_once(queue, config, limit=1)
            second = worker_once(queue, config, limit=1)
        finally:
            globals()["create_codex_thread_app_server"] = original_create
            globals()["inspect_codex_thread_app_server"] = original_inspect

        persisted = load_config(config_path)
        items = persisted.get("threads", {}).get("items", [])
        new_items = [item for item in items if new_user in str(item.get("description") or "")]
        fail_items = [item for item in items if fail_user in str(item.get("description") or "")]
        drift_after_first = (first.get("account_onboarding_sync") or {}).get("sync", {}).get("after", {})
        second_onboarding = second.get("account_onboarding_sync") or {}
        user_rows = []
        events = []
        with queue.session() as db:
            user_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT external_user, allow_trigger FROM mobile_users ORDER BY external_user"
                ).fetchall()
            ]
            events = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT event_type, payload_json
                    FROM mobile_events
                    WHERE event_type LIKE 'openclaw_account_onboarding_worker_sync%'
                    ORDER BY id
                    """
                ).fetchall()
            ]
        ok = bool(
            first.get("ok")
            and (first.get("account_onboarding_sync") or {}).get("action") == "applied"
            and not (first.get("account_onboarding_sync") or {}).get("ok")
            and len(new_items) == 1
            and len(fail_items) == 0
            and any(row.get("external_user") == new_user and int(row.get("allow_trigger") or 0) == 1 for row in user_rows)
            and not any(row.get("external_user") == fail_user for row in user_rows)
            and second_onboarding.get("action") == "cooldown"
            and len(create_calls) == 2
            and int(drift_after_first.get("missing_count") or 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "first_action": (first.get("account_onboarding_sync") or {}).get("action"),
            "first_ok": (first.get("account_onboarding_sync") or {}).get("ok"),
            "second_action": second_onboarding.get("action"),
            "created_thread_names": create_calls,
            "new_route_count": len(new_items),
            "failed_route_count": len(fail_items),
            "user_rows": user_rows,
            "events": events,
            "drift_after_first": drift_after_first,
        }


def mobile_repair_command_entry_check() -> dict[str, Any]:
    """Exercise mobile repair control parsing against temporary state only."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-repair-command-") as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        admin_user = "repair-admin@im.wechat"
        other_user = "repair-other@im.wechat"
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["primary", "backup1"], ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "primary.json").write_text(
            json.dumps({"userId": admin_user}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "backup1.json").write_text(
            json.dumps({"userId": other_user}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": []},
            "trigger": {"delivery_mode": "codex-app-server"},
        }
        queue = queue_from_config(config)
        calls: dict[str, list[dict[str, Any]]] = {"repair": [], "system": [], "reply": []}

        original_repair = globals()["run_mobile_repair_control"]
        original_system = globals()["run_mobile_system_maintenance_control"]
        original_reply = globals()["reply_to_weixin"]

        def fake_run_mobile_repair_control(
            _queue: MobileQueue,
            _config: dict[str, Any],
            mode: str,
            apply_safe: bool = True,
        ) -> dict[str, Any]:
            calls["repair"].append({"mode": mode, "apply_safe": apply_safe})
            specialized = {"last", "active", "cdp", "backlog", "supplement", "plugins", "tools"}
            if mode in specialized:
                return {
                    "ok": True,
                    "control": "repair",
                    "mode": mode,
                    "applied": False,
                    "specialized_mode": True,
                    "summary": f"repair {mode} 专项执行完成。",
                    "actions_blocked": ["fixture_blocked"],
                    "evidence": {"issue_codes": ["fixture_issue"], "active_task_ids": ["fixture-active"]},
                }
            return {
                "ok": True,
                "control": "repair",
                "mode": mode,
                "applied": bool(apply_safe and mode == "safe"),
                "diagnosis": {"issues": [{"code": "fixture_issue"}]},
                "repair": {
                    "actions": [
                        {
                            "code": "fixture_action",
                            "result": {"applied": bool(apply_safe and mode == "safe")},
                        }
                    ]
                },
            }

        def fake_run_mobile_system_maintenance_control(
            apply_safe: bool = True,
            *,
            external_user: str = "",
            account_id: str = "",
        ) -> dict[str, Any]:
            calls["system"].append({"apply_safe": apply_safe, "external_user": external_user, "account_id": account_id})
            return {
                "ok": True,
                "control": "repair",
                "mode": "system",
                "async": True,
                "started": True,
                "request_id": "fixture-mobile-repair",
                "pid": 12345,
                "log_path": str(temp / "system-maintenance.log"),
            }

        def fake_reply_to_weixin(
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            send: bool,
            media: str | None = None,
        ) -> dict[str, Any]:
            calls["reply"].append(
                {
                    "to": str(task.get("external_user") or ""),
                    "text": text,
                    "send": send,
                    "media": str(media or ""),
                }
            )
            return {"ok": True, "send": send, "text": text}

        try:
            globals()["run_mobile_repair_control"] = fake_run_mobile_repair_control
            globals()["run_mobile_system_maintenance_control"] = fake_run_mobile_system_maintenance_control
            globals()["reply_to_weixin"] = fake_reply_to_weixin
            repair_default = maybe_handle_control_message(queue, temp / "config.json", config, "repair", admin_user, "conv")
            repair_bridge_default = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge", admin_user, "conv")
            repair_status = maybe_handle_control_message(queue, temp / "config.json", config, "/repair_bridge status", admin_user, "conv")
            repair_active = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge active", admin_user, "conv")
            repair_cdp = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge cdp", admin_user, "conv")
            repair_backlog = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge backlog", admin_user, "conv")
            repair_supplement = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge supplement", admin_user, "conv")
            repair_plugins = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge plugins", admin_user, "conv")
            repair_tools = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge tools", admin_user, "conv")
            repair_last = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge last", admin_user, "conv")
            repair_rejected = maybe_handle_control_message(queue, temp / "config.json", config, "repair", other_user, "conv")
            repair_bridge_rejected = maybe_handle_control_message(queue, temp / "config.json", config, "repair bridge", other_user, "conv")
            plain_text = maybe_handle_control_message(queue, temp / "config.json", config, "repair一下桥接", admin_user, "conv")
        finally:
            globals()["run_mobile_repair_control"] = original_repair
            globals()["run_mobile_system_maintenance_control"] = original_system
            globals()["reply_to_weixin"] = original_reply

        specialized_results = [repair_active, repair_cdp, repair_backlog, repair_supplement, repair_plugins, repair_tools, repair_last]
        ok = (
            bool(repair_default and repair_default.get("ok") and repair_default.get("mode") == "system")
            and bool(repair_bridge_default and repair_bridge_default.get("ok") and repair_bridge_default.get("mode") == "safe")
            and bool(repair_status and repair_status.get("ok") and repair_status.get("mode") == "status")
            and all(bool(item and item.get("ok") and item.get("specialized_mode")) for item in specialized_results)
            and bool(repair_rejected and not repair_rejected.get("ok") and repair_rejected.get("status") == "rejected")
            and bool(repair_bridge_rejected and not repair_bridge_rejected.get("ok") and repair_bridge_rejected.get("status") == "rejected")
            and plain_text is None
            and calls["system"] == [{"apply_safe": True, "external_user": admin_user, "account_id": "primary"}]
            and calls["repair"] == [
                {"mode": "safe", "apply_safe": True},
                {"mode": "status", "apply_safe": True},
                {"mode": "active", "apply_safe": True},
                {"mode": "cdp", "apply_safe": True},
                {"mode": "backlog", "apply_safe": True},
                {"mode": "supplement", "apply_safe": True},
                {"mode": "plugins", "apply_safe": True},
                {"mode": "tools", "apply_safe": True},
                {"mode": "last", "apply_safe": True},
            ]
            and len(calls["reply"]) == 12
        )
        return {
            "ok": ok,
            "temp_only": True,
            "cases": {
                "repair_default": repair_default,
                "repair_bridge_default": repair_bridge_default,
                "repair_status_compat": repair_status,
                "repair_active": repair_active,
                "repair_cdp": repair_cdp,
                "repair_backlog": repair_backlog,
                "repair_supplement": repair_supplement,
                "repair_plugins": repair_plugins,
                "repair_tools": repair_tools,
                "repair_last": repair_last,
                "non_admin_rejected": repair_rejected,
                "non_admin_bridge_rejected": repair_bridge_rejected,
                "plain_text_not_control": plain_text,
            },
            "calls": calls,
            "assertion": (
                "mobile text 'repair' maps to total computer maintenance for primary admin; repair bridge and /repair_bridge "
                "remain compatible with bridge repair; specialized bridge modes route to bounded executors; non-admin is rejected; "
                "ordinary repair-prefixed prose is not captured"
            ),
        }


def control_receipt_contract_check() -> dict[str, Any]:
    """Temp-only regression for mobile control command reply receipts."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-control-receipt-") as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        admin_user = "receipt-admin@im.wechat"
        other_user = "receipt-other@im.wechat"
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["primary", "backup1"], ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "primary.json").write_text(
            json.dumps({"userId": admin_user}, ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "backup1.json").write_text(
            json.dumps({"userId": other_user}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "security": {"allowed_users": []},
            "trigger": {"delivery_mode": "codex-app-server"},
        }
        queue = queue_from_config(config)
        calls: list[dict[str, Any]] = []
        original_system = globals()["run_mobile_system_maintenance_control"]
        original_reply = globals()["reply_to_weixin"]

        def fake_run_mobile_system_maintenance_control(
            apply_safe: bool = True,
            *,
            external_user: str = "",
            account_id: str = "",
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "control": "repair",
                "mode": "system",
                "async": True,
                "started": True,
                "request_id": "fixture-receipt-repair",
                "pid": 23456,
                "log_path": str(temp / "maintenance.log"),
                "apply_safe": apply_safe,
                "external_user": external_user,
                "account_id": account_id,
            }

        def fake_reply_to_weixin(
            task: dict[str, Any],
            text: str,
            _config: dict[str, Any],
            send: bool,
            media: str | None = None,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "to": str(task.get("external_user") or ""),
                    "account_id": str(task.get("receiver_account_id") or ""),
                    "text_chars": len(str(text or "")),
                    "send": send,
                    "media": str(media or ""),
                }
            )
            return {
                "ok": True,
                "delivery_accepted": True,
                "phone_visible_confirmed": False,
                "send": send,
                "media": str(media or ""),
            }

        try:
            globals()["run_mobile_system_maintenance_control"] = fake_run_mobile_system_maintenance_control
            globals()["reply_to_weixin"] = fake_reply_to_weixin
            repair_result = maybe_handle_control_message(queue, temp / "config.json", config, "repair", admin_user, "conv")
            status_result = maybe_handle_control_message(queue, temp / "config.json", config, "status", admin_user, "conv")
            rejected_result = maybe_handle_control_message(queue, temp / "config.json", config, "repair", other_user, "conv")
        finally:
            globals()["run_mobile_system_maintenance_control"] = original_system
            globals()["reply_to_weixin"] = original_reply

        with queue.session() as db:
            healthy = control_reply_receipt_health(db)
            events = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT event_type, payload_json
                    FROM mobile_events
                    WHERE event_type LIKE 'control_reply_%'
                       OR event_type IN ('system_maintenance_control_started','control_rejected','user_status_replied')
                    ORDER BY id
                    """
                ).fetchall()
            ]
        queue.add_event(
            "openclaw-weixin",
            "system_maintenance_control_started",
            {"command": "repair", "ok": True, "legacy_fixture_without_receipt": True},
        )
        with queue.session() as db:
            broken = control_reply_receipt_health(db)
        receipt_ids = [
            str((result or {}).get("reply", {}).get("control_receipt_id") or "")
            for result in (repair_result, status_result, rejected_result)
        ]
        ok = bool(
            healthy.get("ok")
            and int(healthy.get("outbox_count") or 0) == 3
            and int(healthy.get("terminal_count") or 0) == 3
            and int(healthy.get("missing_terminal_count") or 0) == 0
            and int(healthy.get("missing_receipt_action_count") or 0) == 0
            and all(item.startswith("ctrl-") for item in receipt_ids)
            and len(set(receipt_ids)) == 3
            and int(broken.get("missing_receipt_action_count") or 0) == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "results": {
                "repair": repair_result,
                "status": status_result,
                "rejected": rejected_result,
            },
            "receipt_ids": receipt_ids,
            "reply_calls": calls,
            "healthy_contract": healthy,
            "broken_fixture_contract": broken,
            "events": events,
            "assertion": (
                "mobile control commands create durable receipt ids, outbox records, and sent/failed terminal records; "
                "legacy action events without receipt_id are diagnosed instead of being treated as complete"
            ),
        }


def mobile_repair_specialized_modes_check() -> dict[str, Any]:
    """Temp-only check that specialized repair modes are scoped and bounded."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-repair-specialized-", ignore_cleanup_errors=True) as temp_root:
        temp = Path(temp_root)
        state_dir = temp / "state"
        accounts_dir = state_dir / "openclaw-weixin" / "accounts"
        accounts_dir.mkdir(parents=True, exist_ok=True)
        admin_user = "repair-admin@im.wechat"
        (state_dir / "openclaw-weixin" / "accounts.json").write_text(
            json.dumps(["primary"], ensure_ascii=False),
            encoding="utf-8",
        )
        (accounts_dir / "primary.json").write_text(
            json.dumps({"userId": admin_user}, ensure_ascii=False),
            encoding="utf-8",
        )
        codex_home = temp / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        config_toml = codex_home / "config.toml"
        config_toml.write_text("", encoding="utf-8")
        config = {
            "openclaw": {
                "account_id": "primary",
                "state_dir": str(state_dir),
            },
            "queue": {"db_path": str(temp / "queue.db")},
            "codex": {"config_path": str(config_toml)},
            "security": {"allowed_users": []},
            "trigger": {
                "delivery_mode": "codex-app-server",
                "codex_cdp_no_start": True,
                "codex_cdp_port": 65530,
                "codex_app_server_port": 65531,
            },
        }
        queue = queue_from_config(config)
        globals_before = {
            "doctor_report": globals()["doctor_report"],
            "repair_report": globals()["repair_report"],
            "tool_registry_health": globals()["tool_registry_health"],
            "codex_mcp_config_health": globals()["codex_mcp_config_health"],
            "codex_plugin_config_health": globals()["codex_plugin_config_health"],
        }

        calls = {"doctor_report": 0, "repair_report": 0, "tool_registry_health": 0, "codex_mcp_config_health": 0, "codex_plugin_config_health": 0}

        def forbidden_global_report(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("specialized modes must not call broad doctor/repair reports")

        def fake_tool_registry_health(_queue: MobileQueue, _config: dict[str, Any]) -> dict[str, Any]:
            calls["tool_registry_health"] += 1
            return {"ok": True, "status": "ok", "recommendations": []}

        def fake_codex_mcp_config_health(_config: dict[str, Any]) -> dict[str, Any]:
            calls["codex_mcp_config_health"] += 1
            return {
                "ok": True,
                "config_path": str(config_toml),
                "repairable_missing": [],
                "repairable_drifted": [],
            }

        def fake_codex_plugin_config_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls["codex_plugin_config_health"] += 1
            return {
                "ok": True,
                "config_path": str(config_toml),
                "config_parse_ok": True,
                "missing_enabled_plugins": [],
            }

        globals()["doctor_report"] = forbidden_global_report
        globals()["repair_report"] = forbidden_global_report
        globals()["tool_registry_health"] = fake_tool_registry_health
        globals()["codex_mcp_config_health"] = fake_codex_mcp_config_health
        globals()["codex_plugin_config_health"] = fake_codex_plugin_config_health
        modes = ["last", "active", "cdp", "backlog", "supplement", "plugins", "tools"]
        try:
            results = {
                mode: run_mobile_repair_control(queue, config, mode, apply_safe=False)
                for mode in modes
            }
        finally:
            globals().update(globals_before)
        with queue.session() as db:
            task_count = db.execute("SELECT COUNT(*) AS n FROM mobile_tasks").fetchone()["n"]
        ok = bool(
            all(item.get("ok") and item.get("specialized_mode") for item in results.values())
            and all(not item.get("unsupported_mode") for item in results.values())
            and not any(item.get("report", {}).get("repair", {}).get("dry_run_contract", {}).get("sends_weixin_messages") for item in results.values() if isinstance(item.get("report"), dict))
            and task_count == 0
            and results["tools"].get("actions_blocked")
            and results["cdp"].get("actions_blocked")
            and results["supplement"].get("actions_blocked")
            and calls["doctor_report"] == 0
            and calls["repair_report"] == 0
            and calls["tool_registry_health"] == 1
            and calls["codex_mcp_config_health"] == 1
            and calls["codex_plugin_config_health"] == 1
        )
        return {
            "ok": ok,
            "temp_only": True,
            "modes": {
                mode: {
                    "ok": result.get("ok"),
                    "applied": result.get("applied"),
                    "specialized_mode": result.get("specialized_mode"),
                    "actions_taken": result.get("actions_taken"),
                    "actions_blocked": result.get("actions_blocked"),
                    "evidence": result.get("evidence"),
                }
                for mode, result in results.items()
            },
            "calls": calls,
            "task_count_after": task_count,
            "assertion": "specialized repair modes run bounded scoped checks without broad maintenance reports, and do not send replies, create tasks, install tools, or force active-task mutation",
        }


def mobile_execution_contract_prompt_check() -> dict[str, Any]:
    """Temp-only check that generated mobile prompts preserve execute-before-result rules."""
    task = {
        "id": "mobile-contract-task",
        "risk_level": "L1",
        "external_user": "contract@im.wechat",
        "command": "/ask",
        "text": "继续工作",
        "metadata_json": "{}",
    }
    prompt = task_prompt(
        [task],
        mobile_batch_id="mobile-contract-batch",
        bridge_thread_id="thread-mobile-contract",
        config={"security": {"allowed_users": ["contract@im.wechat"]}},
    )
    lines = [line.strip() for line in prompt.splitlines()]
    rules_line = next((line for line in lines if line.startswith("rules=")), "")
    supplement_line = next((line for line in lines if line.startswith("supplement_contract=")), "")
    rules = json.loads(rules_line.split("=", 1)[1]) if rules_line else {}
    supplement = json.loads(supplement_line.split("=", 1)[1]) if supplement_line else {}
    valid_gate = final_reply_prompt_contract_gate(prompt, ["mobile-contract-task"])
    legacy_prompt = prompt.replace("first=ack_then_continue", "first=ack_only")
    legacy_prompt = legacy_prompt.replace("\"ack_must_continue\":true,", "")
    legacy_prompt = legacy_prompt.replace("\"ack_only_is_protocol_failure\":true,", "")
    legacy_gate = final_reply_prompt_contract_gate(legacy_prompt, ["mobile-contract-task"])
    checks = {
        "schema_v2": "prompt_schema=mobile-openclaw-final-reply/v2" in lines,
        "ack_first": rules.get("ack_first") is True,
        "ack_means_received_only": rules.get("ack_means_received_only") is True,
        "ack_must_continue": rules.get("ack_must_continue") is True,
        "ack_only_is_protocol_failure": rules.get("ack_only_is_protocol_failure") is True,
        "result_after_work_only": rules.get("result_after_work_only") is True,
        "mobile_equals_desktop_quality": rules.get("mobile_equals_desktop_quality") is True,
        "permission_table": bool(rules.get("permission_table")),
        "supplement_required": supplement.get("required") is True,
        "supplement_tools": supplement.get("tools") == ["bridge.get_pending_batch", "bridge.ack_message"],
        "supplement_when": supplement.get("when") == ["after_ack_before_work", "before_final_result"],
        "supplement_fallback": supplement.get("fallback") == "supplement-fallback-v1",
        "supplement_fail_closed": supplement.get("fail_closed") is True,
        "protocol_markers": "ack=[[" in prompt and "result_begin=[[" in prompt and "result_end=[[" in prompt,
        "output_contract": "output_contract task_id=mobile-contract-task" in prompt
        and "first=ack_then_continue" in prompt
        and "weixin_text" in prompt,
        "dispatch_contract_gate_accepts_generated_prompt": valid_gate.get("ok") is True,
        "dispatch_contract_gate_rejects_legacy_ack_only": legacy_gate.get("ok") is False
        and any(
            item.get("code") == "legacy_ack_only_contract"
            for item in legacy_gate.get("validation", {}).get("issues", [])
        ),
        "prompt_budget": len(prompt) < 2150,
    }
    missing = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not missing,
        "temp_only": True,
        "missing": missing,
        "prompt_length": len(prompt),
        "rules_keys": sorted(rules.keys()),
        "supplement_keys": sorted(supplement.keys()),
        "valid_gate": valid_gate,
        "legacy_gate": legacy_gate,
        "prompt_excerpt": "\n".join(
            line for line in lines if line.startswith(("prompt_schema=", "rules=", "output_contract", "supplement_contract="))
        ),
        "assertion": (
            "mobile delegation prompts use compact v2 schema while preserving ack/result ownership, "
            "execute-before-result, permission authority, and supplement get/ack with fallback"
        ),
    }


def mobile_permission_prompt_compact_check() -> dict[str, Any]:
    """Temp-only check that mobile prompts carry compact permission metadata."""
    user = "compact-admin@im.wechat"
    task = {
        "id": "mobile-permission-compact-task",
        "risk_level": "L1",
        "external_user": user,
        "receiver_account_id": "primary",
        "command": "/ask",
        "text": "测试权限提示词长度",
        "metadata_json": json.dumps({"receiver_account_id": "primary"}, ensure_ascii=False),
    }
    config = {
        "security": {"allowed_users": [user]},
        "openclaw_accounts": {"primary": {"userId": user, "token": "present"}},
    }
    prompt = task_prompt([task], mobile_batch_id="mobile-permission-compact-batch", config=config)
    permission_lines = [line.strip() for line in prompt.splitlines() if line.strip().startswith("permission=")]
    parsed: dict[str, Any] = {}
    if permission_lines:
        parsed = json.loads(permission_lines[0].split("=", 1)[1])
    forbidden_keys = {
        "allowed_actions",
        "ask_policy",
        "admin_may_execute_unspecified_action_with_audit",
        "must_check_permission_table",
    }
    ok = bool(
        len(permission_lines) == 1
        and parsed.get("schema") == "mobile-permission-context-compact/v1"
        and parsed.get("permission_profile") in {"admin", "user", "blocked"}
        and parsed.get("permission_table_ref")
        and isinstance(parsed.get("admin_superuser"), bool)
        and not (forbidden_keys & set(parsed))
        and "allowed_actions" not in prompt
        and len(permission_lines[0]) < 520
        and len(prompt) < 1800
    )
    return {
        "ok": ok,
        "temp_only": True,
        "permission_line_length": len(permission_lines[0]) if permission_lines else 0,
        "permission_keys": sorted(parsed.keys()),
        "forbidden_present": sorted(forbidden_keys & set(parsed)),
        "prompt_length": len(prompt),
        "assertion": "mobile prompts reference the permission table through compact metadata instead of embedding full capability and ask-policy payloads",
    }


def result_ownership_check() -> dict[str, Any]:
    """Temp-only regression check for result ownership helpers."""
    with tempfile.TemporaryDirectory(prefix="mobile-openclaw-ownership-") as temp_root:
        temp = Path(temp_root)
        config = {
            "queue": {"db_path": str(temp / "mobile_openclaw_bridge.db")},
            "security": {"allowed_users": ["ownership-probe@im.wechat"]},
            "safety": {"shadow_mode": False, "paused": False},
        }
        queue = queue_from_config(config)
        first = queue.enqueue(
            "ownership probe alpha",
            source="openclaw-weixin",
            external_user="ownership-probe@im.wechat",
            external_conversation="probe",
            metadata={"msg_id": "ownership-probe-alpha"},
        )
        second = queue.enqueue(
            "ownership probe beta",
            source="openclaw-weixin",
            external_user="ownership-probe@im.wechat",
            external_conversation="probe",
            metadata={"msg_id": "ownership-probe-beta"},
        )
        task_ids = [str(first["id"]), str(second["id"])]
        batch_id = "mobile-openclaw-test-batch"
        protocols = mobile_protocols([first, second], batch_id)
        for tid in task_ids:
            queue.runtime_set(task_turn_key(tid), "turn-1")
            queue.runtime_set(task_batch_key(tid), batch_id)
            queue.runtime_set(task_expected_ids_key(tid), json.dumps(task_ids, ensure_ascii=False))
            queue.runtime_set(task_ack_code_key(tid), protocols[tid]["ack_code"])
            queue.runtime_set(task_result_code_key(tid), protocols[tid]["result_code"])
        queued_ok, queued_message = queue.queue_for_codex(task_ids, "thread-ownership", lock_scope="thread")
        if queued_ok:
            queue.mark_sent_to_codex(task_ids)

        client_message_id, expected_task_ids = task_batch_runtime(queue, task_ids[0])
        expected_result_codes = task_result_code_runtime(queue, expected_task_ids)
        result_block_text = (
            protocols[task_ids[0]]["ack_marker"]
            + "\n"
            + protocols[task_ids[0]]["result_begin_marker"]
            + "\nowned final reply\n"
            + protocols[task_ids[0]]["result_end_marker"]
        )
        stripped_result_block = strip_mobile_result_markers(result_block_text)
        mismatch_poll = {
            "ok": True,
            "healthy": True,
            "status": "completed",
            "newText": None,
            "ownership_mismatch": True,
            "ownership": {
                "valid": False,
                "client_message_id": batch_id,
                "expected_task_ids": task_ids,
                "missing_task_ids": ["task-beta"],
            },
        }
        incomplete_protocol_poll = {
            "ok": True,
            "healthy": True,
            "status": "running",
            "newText": None,
            "protocol": "mobile_result_boundary_v2",
            "result_complete": False,
            "terminal_without_text": False,
            "ownership": {"valid": False, "protocol": "mobile_result_boundary_v2", "ack_seen": True},
        }
        terminal_protocol_poll = dict(incomplete_protocol_poll)
        terminal_protocol_poll.update({"status": "completed", "terminal_without_text": True})
        match_poll = {
            "ok": True,
            "healthy": True,
            "status": "completed",
            "newText": "owned final reply",
            "ownership_mismatch": False,
            "ownership": {
                "valid": True,
                "client_message_id": batch_id,
                "expected_task_ids": task_ids,
                "missing_task_ids": [],
            },
        }
        record_unowned_intermediate_result(queue, task_ids[0], mismatch_poll)
        first_after = queue.get_task(task_ids[0]) or {}
        runtime_retained = bool(queue.runtime_get(task_turn_key(task_ids[0]))) and bool(queue.runtime_get(task_batch_key(task_ids[0])))
        with queue.session() as db:
            event_count = db.execute(
                "SELECT COUNT(*) FROM mobile_events WHERE task_id=? AND event_type='unowned_intermediate_seen'",
                (task_ids[0],),
            ).fetchone()[0]
        return {
            "ok": bool(
                queued_ok
                and client_message_id == batch_id
                and expected_task_ids == task_ids
                and expected_result_codes == {tid: protocols[tid]["result_code"] for tid in task_ids}
                and stripped_result_block == "owned final reply"
                and poll_has_ownership_mismatch(mismatch_poll)
                and not poll_has_ownership_mismatch(incomplete_protocol_poll)
                and poll_has_ownership_mismatch(terminal_protocol_poll)
                and not poll_has_ownership_mismatch(match_poll)
                and first_after.get("status") == "sent_to_codex"
                and runtime_retained
                and int(event_count or 0) == 1
            ),
            "temp_only": True,
            "client_message_id": client_message_id,
            "expected_task_ids": expected_task_ids,
            "expected_result_codes": expected_result_codes,
            "stripped_result_block": stripped_result_block,
            "queued": {"ok": queued_ok, "message": queued_message},
            "mismatch_detected": poll_has_ownership_mismatch(mismatch_poll),
            "incomplete_protocol_waits": not poll_has_ownership_mismatch(incomplete_protocol_poll),
            "terminal_protocol_retries": poll_has_ownership_mismatch(terminal_protocol_poll),
            "match_accepted": not poll_has_ownership_mismatch(match_poll),
            "status_after_mismatch": first_after.get("status"),
            "runtime_retained_after_mismatch": runtime_retained,
            "unowned_intermediate_events": int(event_count or 0),
        }

_CHECKS = {
    "auto_onboarding_check": auto_onboarding_check,
    "account_onboarding_sync_check": account_onboarding_sync_check,
    "account_onboarding_worker_lifecycle_check": account_onboarding_worker_lifecycle_check,
    "mobile_repair_command_entry_check": mobile_repair_command_entry_check,
    "control_receipt_contract_check": control_receipt_contract_check,
    "mobile_repair_specialized_modes_check": mobile_repair_specialized_modes_check,
    "mobile_execution_contract_prompt_check": mobile_execution_contract_prompt_check,
    "mobile_permission_prompt_compact_check": mobile_permission_prompt_compact_check,
    "result_ownership_check": result_ownership_check,
}
