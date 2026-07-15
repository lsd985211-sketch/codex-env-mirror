"""Historical failed-result recovery CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for auditing or recovering historical
failed tasks that already have durable owned-result evidence.
Non-goals: recovery eligibility policy, final reply delivery, queue schema, or
bulk recovery semantics.
State behavior: audit and help are read-only; apply remains restricted to one
explicit task id and delegates the existing recovery function.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

from typing import Any, Callable


def register_historical_recovery_parsers(subparsers: Any) -> None:
    recover_failed = subparsers.add_parser(
        "recover-failed-result-replies",
        help="Dry-run or apply a one-time recovery for historical failed tasks that already have a durable result",
    )
    recover_failed.add_argument(
        "--apply",
        action="store_true",
        help="Apply the recovery for a single explicit task id; default is dry-run",
    )
    recover_failed.add_argument("--task-id", default="", help="Recover only this failed task id")
    recover_failed.add_argument("--limit", type=int, default=20, help="Maximum number of candidate tasks to inspect")

    audit_failed = subparsers.add_parser(
        "audit-failed-result-replies",
        help="Read-only audit for failed tasks with result text and why they are eligible or excluded from recovery",
    )
    audit_failed.add_argument("--task-id", default="", help="Audit only this failed task id")
    audit_failed.add_argument("--limit", type=int, default=50, help="Maximum number of tasks to inspect")

    subparsers.add_parser(
        "historical-failed-result-recovery-help",
        help="Read-only operational guidance for auditing and recovering historical failed-result tasks safely",
    )


def run_historical_recovery_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    recover_failed_tasks_with_result_for_reply: Callable[..., dict[str, Any]],
    audit_failed_tasks_with_result_for_reply: Callable[..., dict[str, Any]],
    historical_failed_result_recovery_help: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    if args.cmd == "recover-failed-result-replies":
        if bool(args.apply) and not str(args.task_id or "").strip():
            return (
                {
                    "ok": False,
                    "reason": "task_id_required_for_apply",
                    "message": "Historical failed-result recovery apply is restricted to a single explicit task id.",
                },
                1,
            )
        result = recover_failed_tasks_with_result_for_reply(
            queue,
            config,
            apply=bool(args.apply),
            task_id=str(args.task_id or ""),
            limit=max(1, int(args.limit or 20)),
        )
        return result, 0 if result.get("ok") else 1

    if args.cmd == "audit-failed-result-replies":
        result = audit_failed_tasks_with_result_for_reply(
            queue,
            task_id=str(args.task_id or ""),
            limit=max(1, int(args.limit or 50)),
        )
        return result, 0 if result.get("ok") else 1

    return historical_failed_result_recovery_help(), 0
