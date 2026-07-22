"""Codex thread route CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for thread-route, account-onboarding
sync, thread visibility checks, desktop sync checks, and thread prewarm.
Non-goals: thread selection policy, app-server implementation, queue schema,
or Codex delivery/recovery semantics.
State behavior: route list/get, visibility, desktop-sync, and prewarm are
bounded diagnostics or explicit operations; thread-route set/clear and
account-onboarding-sync --apply preserve their existing explicit write
contracts.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

from typing import Any, Callable


def register_thread_route_parsers(subparsers: Any) -> None:
    thread_route = subparsers.add_parser("thread-route", help="Manage Weixin user to Codex thread routing")
    thread_route.add_argument("action", choices=["list", "get", "set", "clear"])
    thread_route.add_argument("--user", default="", help="Weixin external user id")
    thread_route.add_argument("--thread", default="", help="Configured thread stable id/name/alias/number for set")

    account_sync = subparsers.add_parser("account-onboarding-sync", help="Sync persisted OpenClaw accounts into bridge users and Codex thread routes")
    account_sync.add_argument("--apply", action="store_true", help="Create missing Codex threads and persist thread routes; default is dry-run")

    thread_visibility = subparsers.add_parser("thread-visibility-check", help="Check whether a Codex thread can be resumed, read, and listed")
    thread_visibility.add_argument("--thread-id", default="", help="Raw Codex thread id")
    thread_visibility.add_argument("--thread", default="", help="Configured thread stable id/name/alias/number")
    thread_visibility.add_argument("--stabilize-name", action="store_true", help="Set the configured thread title while checking")

    desktop_sync = subparsers.add_parser("desktop-sync-check", help="Check mobile task sync via Codex turns, not stale thread preview")
    desktop_sync.add_argument("--task-id", default="", help="Mobile task id; uses its Codex thread and marker")
    desktop_sync.add_argument("--thread-id", default="", help="Raw Codex thread id")
    desktop_sync.add_argument("--expected-task-id", action="append", default=[], help="Task marker expected inside a Codex turn")

    thread_prewarm = subparsers.add_parser("thread-prewarm", help="Run a bounded Codex app-server thread prewarm")
    thread_prewarm.add_argument("--thread-id", required=True, help="Raw Codex thread id")
    thread_prewarm.add_argument("--thread-name", default="", help="Optional thread name to stabilize while prewarming")


def run_thread_route_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    account_onboarding_sync: Callable[..., dict[str, Any]],
    active_thread_key: Callable[[str], str],
    desktop_sync_check_app_server: Callable[..., dict[str, Any]],
    find_thread: Callable[..., dict[str, Any] | None],
    inspect_codex_thread_app_server: Callable[..., dict[str, Any]],
    run_thread_prewarm: Callable[..., dict[str, Any]],
    set_active_thread: Callable[..., None],
    thread_route_diagnostics: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    if args.cmd == "account-onboarding-sync":
        result = account_onboarding_sync(queue, config, apply=bool(args.apply))
        return result, 0 if result.get("ok") or not args.apply else 1

    if args.cmd == "thread-visibility-check":
        item = find_thread(config, args.thread) if args.thread else None
        thread_id = str(args.thread_id or (item or {}).get("thread_id") or "").strip()
        thread_name = str((item or {}).get("name") or "").strip()
        if not thread_id:
            return {"ok": False, "reason": "--thread-id or --thread is required"}, 1
        result = inspect_codex_thread_app_server(
            config,
            thread_id,
            thread_name,
            stabilize_name=bool(args.stabilize_name),
        )
        if item:
            result["configured_thread"] = item
        return result, 0 if result.get("ok") else 1

    if args.cmd == "desktop-sync-check":
        task = queue.get_task(args.task_id) if args.task_id else None
        thread_id = str(args.thread_id or (task or {}).get("codex_thread_id") or "").strip()
        expected_ids = [str(item).strip() for item in (args.expected_task_id or []) if str(item).strip()]
        if task and not expected_ids:
            expected_ids = [str(task.get("id") or "").strip()]
        if not thread_id:
            return {"ok": False, "reason": "--thread-id or --task-id with codex_thread_id is required"}, 1
        result = desktop_sync_check_app_server(config, thread_id, expected_ids)
        result["preview_policy"] = "thread/list.preview is treated as advisory only; Codex turns are the sync source of truth"
        if task:
            result["task"] = {
                "id": task.get("id"),
                "status": task.get("status"),
                "text": task.get("text"),
                "result_present": bool(task.get("result")),
                "pushed_at": task.get("pushed_at"),
            }
        return result, 0 if result.get("ok") else 1

    if args.cmd == "thread-prewarm":
        result = run_thread_prewarm(queue, config, args.thread_id, args.thread_name)
        return result, 0 if result.get("ok") else 1

    if args.action == "list":
        routes: list[dict[str, Any]] = []
        with queue.session() as db:
            rows = db.execute(
                """
                SELECT key, value, updated_at FROM mobile_runtime
                WHERE key LIKE 'user_active_thread:%'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        for row in rows:
            user = str(row["key"]).removeprefix("user_active_thread:")
            thread = find_thread(config, str(row["value"] or ""))
            diagnostics = thread_route_diagnostics(queue, config, user, thread)
            routes.append(
                {
                    "external_user": user,
                    "thread_key": row["value"],
                    "thread": thread,
                    **diagnostics,
                    "updated_at": row["updated_at"],
                }
            )
        return {"ok": True, "routes": routes}, 0

    if args.action == "get":
        if not args.user:
            return {"ok": False, "reason": "--user is required"}, 1
        value = queue.runtime_get(active_thread_key(args.user))
        thread = find_thread(config, value) if value else None
        return {
            "ok": True,
            "external_user": args.user,
            "thread_key": value,
            "thread": thread,
            **thread_route_diagnostics(queue, config, args.user, thread),
        }, 0

    if args.action == "set":
        if not args.user or not args.thread:
            return {"ok": False, "reason": "--user and --thread are required"}, 1
        thread = find_thread(config, args.thread)
        if not thread:
            return {"ok": False, "reason": "configured thread not found", "selector": args.thread}, 1
        set_active_thread(queue, args.user, thread["id"])
        queue.add_event(
            "local",
            "thread_route_set",
            {"external_user": args.user, "thread_id": thread["id"], "codex_thread_id": thread["thread_id"]},
        )
        return {"ok": True, "external_user": args.user, "thread": thread}, 0

    if not args.user:
        return {"ok": False, "reason": "--user is required"}, 1
    queue.runtime_delete(active_thread_key(args.user))
    queue.add_event("local", "thread_route_cleared", {"external_user": args.user})
    return {"ok": True, "external_user": args.user, "cleared": True}, 0
