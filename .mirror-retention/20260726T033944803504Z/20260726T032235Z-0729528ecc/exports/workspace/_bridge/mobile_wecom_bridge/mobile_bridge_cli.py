#!/usr/bin/env python3
"""Local CLI for inspecting and updating the mobile bridge queue."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from mobile_queue import MobileQueue


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "mobile_bridge.db"
DEFAULT_CONFIG = ROOT / "config.local.json"


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def load_config(path: str) -> dict:
    config_path = Path(path)
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8-sig"))
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mobile bridge queue CLI")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Queue database path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Bridge config path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enqueue = sub.add_parser("enqueue", help="Add a local dry-run task")
    p_enqueue.add_argument("text")
    p_enqueue.add_argument("--user", default="local-cli")

    p_list = sub.add_parser("list", help="List recent tasks")
    p_list.add_argument("--limit", type=int, default=10)

    p_pending = sub.add_parser("pending", help="List pending tasks")
    p_pending.add_argument("--limit", type=int, default=10)

    p_get = sub.add_parser("get", help="Get one task")
    p_get.add_argument("task_id")

    p_claim = sub.add_parser("claim", help="Claim one pending task")
    p_claim.add_argument("task_id")
    p_claim.add_argument("--agent", default="codex")

    p_done = sub.add_parser("done", help="Complete one task")
    p_done.add_argument("task_id")
    p_done.add_argument("result")

    p_fail = sub.add_parser("fail", help="Mark one task failed")
    p_fail.add_argument("task_id")
    p_fail.add_argument("result")

    p_confirm = sub.add_parser("confirm", help="Confirm one waiting task")
    p_confirm.add_argument("task_id")
    p_confirm.add_argument("secret")

    p_cancel = sub.add_parser("cancel", help="Cancel one open task")
    p_cancel.add_argument("task_id")

    sub.add_parser("health", help="Run queue health checks")

    p_queue = sub.add_parser("queue-codex", help="Move pending tasks into queued_for_codex")
    p_queue.add_argument("thread_id")
    p_queue.add_argument("task_ids", nargs="+")

    p_sent = sub.add_parser("sent-codex", help="Mark queued tasks as sent_to_codex")
    p_sent.add_argument("task_ids", nargs="+")

    sub.add_parser("expire-stale", help="Deprecated compatibility no-op; active Codex tasks are not time-expired")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    queue = MobileQueue(args.db, config=config)

    if args.cmd == "enqueue":
        print_json(queue.enqueue(args.text, source="local-cli", external_user=args.user))
    elif args.cmd == "list":
        print_json(queue.list_tasks(args.limit))
    elif args.cmd == "pending":
        print_json(queue.list_pending(args.limit))
    elif args.cmd == "get":
        print_json(queue.get_task(args.task_id) or {"error": "not found"})
    elif args.cmd == "claim":
        ok, msg = queue.claim(args.task_id, args.agent)
        print_json({"ok": ok, "message": msg})
    elif args.cmd == "done":
        queue.complete(args.task_id, args.result, "done")
        print_json({"ok": True, "status": "done"})
    elif args.cmd == "fail":
        queue.complete(args.task_id, args.result, "failed")
        print_json({"ok": True, "status": "failed"})
    elif args.cmd == "confirm":
        ok, msg = queue.confirm(args.task_id, args.secret)
        print_json({"ok": ok, "message": msg})
    elif args.cmd == "cancel":
        ok, msg = queue.cancel(args.task_id)
        print_json({"ok": ok, "message": msg})
    elif args.cmd == "health":
        print_json(queue.health())
    elif args.cmd == "queue-codex":
        ok, msg = queue.queue_for_codex(args.task_ids, args.thread_id)
        print_json({"ok": ok, "message": msg})
    elif args.cmd == "sent-codex":
        queue.mark_sent_to_codex(args.task_ids)
        print_json({"ok": True, "status": "sent_to_codex"})
    elif args.cmd == "expire-stale":
        print_json({"expired": queue.expire_stale_codex_tasks()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
