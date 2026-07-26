"""Worker loop CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and command dispatch for worker-once and
worker-loop.
Non-goals: worker_once business logic, delivery policy, queue schema, recovery
semantics, or permission decisions.
State behavior: worker-once and worker-loop intentionally mutate queue state by
delegating to the existing worker_once function; this module only preserves the
CLI loop/reload/logging shell.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


def register_worker_loop_parsers(subparsers: Any) -> None:
    worker_once = subparsers.add_parser("worker-once", help="Run one worker polling cycle")
    worker_once.add_argument("--limit", type=int, default=5)
    worker_once.add_argument("--task-id", default="", help="Process only this pending task if it is still pending")

    worker_loop = subparsers.add_parser("worker-loop", help="Run worker polling loop")
    worker_loop.add_argument("--limit", type=int, default=5)
    worker_loop.add_argument("--interval", type=int, default=10)
    worker_loop.add_argument("--max-cycles", type=int, default=0)
    worker_loop.add_argument(
        "--log-mode",
        choices=["summary", "full", "quiet"],
        default="summary",
        help="Control worker-loop stdout volume; summary suppresses repeated idle JSON",
    )


def run_worker_command(
    args: Any,
    config: dict[str, Any],
    *,
    load_config: Callable[[Path], dict[str, Any]],
    db_path_from_config: Callable[[dict[str, Any]], Path],
    queue_from_config: Callable[[dict[str, Any]], Any],
    worker_once: Callable[..., dict[str, Any]],
    print_json: Callable[[Any], None],
    worker_loop_has_activity: Callable[[dict[str, Any]], bool],
    worker_loop_should_log: Callable[[dict[str, Any], str], tuple[bool, str]],
    worker_loop_summary: Callable[[int, dict[str, Any]], dict[str, Any]],
) -> int:
    if args.cmd == "worker-once":
        queue = queue_from_config(config)
        print_json(worker_once(queue, config, args.limit, args.task_id))
        return 0

    run_worker_loop_command(
        args,
        config,
        load_config=load_config,
        db_path_from_config=db_path_from_config,
        queue_from_config=queue_from_config,
        worker_once=worker_once,
        print_json=print_json,
        worker_loop_has_activity=worker_loop_has_activity,
        worker_loop_should_log=worker_loop_should_log,
        worker_loop_summary=worker_loop_summary,
    )
    return 0


def run_worker_loop_command(
    args: Any,
    config: dict[str, Any],
    *,
    load_config: Callable[[Path], dict[str, Any]],
    db_path_from_config: Callable[[dict[str, Any]], Path],
    queue_from_config: Callable[[dict[str, Any]], Any],
    worker_once: Callable[..., dict[str, Any]],
    print_json: Callable[[Any], None],
    worker_loop_has_activity: Callable[[dict[str, Any]], bool],
    worker_loop_should_log: Callable[[dict[str, Any], str], tuple[bool, str]],
    worker_loop_summary: Callable[[int, dict[str, Any]], dict[str, Any]],
) -> None:
    cycles = 0
    previous_log_signature = ""
    idle_streak = 0
    config_path = Path(args.config)
    queue_db_path = db_path_from_config(config)
    queue = queue_from_config(config)
    base_interval = max(1, int(args.interval))
    idle_backoff_seconds = [
        base_interval,
        max(base_interval * 2, base_interval),
        max(base_interval * 5, base_interval),
        max(base_interval * 10, base_interval),
        max(base_interval * 30, base_interval),
    ]
    while True:
        cycles += 1
        config = load_config(config_path)
        config["_config_path"] = str(config_path)
        current_db_path = db_path_from_config(config)
        if current_db_path != queue_db_path:
            queue = queue_from_config(config)
            queue_db_path = current_db_path
        else:
            queue.config = dict(config)
        result = worker_once(queue, config, args.limit)
        if args.log_mode == "full":
            print_json({"cycle": cycles, **result})
        elif args.log_mode == "summary":
            should_log, previous_log_signature = worker_loop_should_log(result, previous_log_signature)
            if should_log:
                print_json(worker_loop_summary(cycles, result))
        has_activity = worker_loop_has_activity(result)
        if has_activity:
            idle_streak = 0
        else:
            idle_streak += 1
        if args.max_cycles and cycles >= args.max_cycles:
            break
        if has_activity or idle_streak <= 1:
            sleep_seconds = base_interval
        else:
            backoff_index = min(idle_streak - 2, len(idle_backoff_seconds) - 1)
            sleep_seconds = idle_backoff_seconds[backoff_index]
        time.sleep(sleep_seconds)
