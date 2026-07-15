"""Basic queue command adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for simple queue inspection commands
and the explicit stuck-task fail command.
Non-goals: worker dispatch, task enqueue policy, permission checks, Weixin
delivery, or stuck-task selection semantics.
State behavior: list, pending, get, and health are read-only; stuck-tasks writes
only when --mark-failed and --confirm mark-failed are both present.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main
for health, list, pending, get, and stuck-tasks.
"""

from __future__ import annotations

from typing import Any, Callable


def register_queue_command_parsers(subparsers: Any) -> None:
    subparsers.add_parser("health", help="Queue health")

    list_parser = subparsers.add_parser("list", help="List recent tasks")
    list_parser.add_argument("--limit", type=int, default=10)

    pending_parser = subparsers.add_parser("pending", help="List pending tasks")
    pending_parser.add_argument("--limit", type=int, default=10)

    stuck_parser = subparsers.add_parser("stuck-tasks", help="List or explicitly fail active queued/sent Codex tasks")
    stuck_parser.add_argument("--mark-failed", action="store_true", help="Mark listed active tasks failed")
    stuck_parser.add_argument("--confirm", default="", help="Required literal value: mark-failed")
    stuck_parser.add_argument("--reason", default="Marked failed by operator after stuck-task review")

    get_parser = subparsers.add_parser("get", help="Get one task")
    get_parser.add_argument("task_id")


def run_queue_command(
    args: Any,
    queue: Any,
    *,
    active_tasks: Callable[[Any], list[dict[str, Any]]],
    mark_failed: Callable[[Any, list[str], str], list[dict[str, Any]]],
) -> dict[str, Any] | list[dict[str, Any]]:
    if args.cmd == "health":
        return queue.health()
    if args.cmd == "list":
        return queue.list_tasks(args.limit)
    if args.cmd == "pending":
        return queue.list_pending(args.limit)
    if args.cmd == "get":
        return queue.get_task(args.task_id) or {"error": "not found"}
    tasks = active_tasks(queue)
    if args.mark_failed:
        if args.confirm != "mark-failed":
            return {
                "ok": False,
                "reason": "refusing to mark tasks failed without --confirm mark-failed",
                "active_tasks": tasks,
            }
        results = mark_failed(queue, [str(task["id"]) for task in tasks], args.reason)
        return {"ok": True, "marked_failed": results}
    return {"ok": True, "active_tasks": tasks, "read_only": True}
