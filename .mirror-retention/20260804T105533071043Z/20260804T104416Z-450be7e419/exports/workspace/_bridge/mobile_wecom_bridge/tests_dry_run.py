#!/usr/bin/env python3
"""Dry-run checks for the WeCom mobile bridge core."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mobile_queue import MobileQueue, sha256_text


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy, got {value!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "mobile_bridge.db"
        config = {
            "security": {
                "allowed_users": ["tester"],
                "confirmation_secret_hash": sha256_text("985confirm"),
                "confirmation_ttl_seconds": 300,
            },
            "safety": {
                "shadow_mode": True,
                "paused": False,
                "max_input_chars": 2000,
            },
            "trigger": {
                "cooldown_seconds": 10,
            },
        }
        queue = MobileQueue(db_path, config=config)

        status = queue.enqueue("/status", source="dry-run", external_user="tester")
        assert_eq(status["risk_level"], "L0", "status risk")
        assert_eq(status["status"], "pending", "status task state")
        assert_eq(status["auth"], "verified", "status auth")

        ask = queue.enqueue("/ask 分析 latest.log 中的崩溃原因", source="dry-run", external_user="tester")
        assert_eq(ask["risk_level"], "L1", "ask risk")
        assert_eq(ask["status"], "pending", "ask task state")

        duplicate = queue.enqueue(
            "/ask 分析 latest.log 中的崩溃原因",
            source="dry-run",
            external_user="tester",
        )
        assert_eq(duplicate["duplicate"], True, "duplicate flag")
        assert_eq(duplicate["id"], ask["id"], "duplicate id")

        risky = queue.enqueue("请关闭服务器并运行脚本", source="dry-run", external_user="tester")
        assert_eq(risky["risk_level"], "L2", "risky risk")
        assert_eq(risky["status"], "pending", "risky task state")

        forbidden = queue.enqueue("删除整个服务器目录", source="dry-run", external_user="tester")
        assert_eq(forbidden["risk_level"], "L3", "forbidden risk")
        assert_eq(forbidden["status"], "waiting_confirmation", "forbidden task state")
        ok, msg = queue.confirm(forbidden["id"], "wrong")
        assert_eq(ok, False, "wrong confirm ok")
        assert_eq(msg, "confirmation secret mismatch", "wrong confirm msg")
        ok, msg = queue.confirm(forbidden["id"], "985confirm")
        assert_eq(ok, True, "confirm ok")
        assert_eq(msg, "confirmed", "confirm msg")

        unknown = queue.enqueue("/ask hello", source="dry-run", external_user="intruder")
        assert_eq(unknown["auth"], "unverified", "unknown auth")
        assert_eq(unknown["status"], "rejected", "unknown rejected")

        long_msg = queue.enqueue("x" * 2001, source="dry-run", external_user="tester")
        assert_eq(long_msg["risk_level"], "L3", "long risk")
        assert_eq(long_msg["status"], "rejected", "long rejected")

        pending = queue.list_pending(limit=10)
        pending_ids = [task["id"] for task in pending]
        assert_true(status["id"] in pending_ids, "status pending")
        assert_true(ask["id"] in pending_ids, "ask pending")
        assert_true(risky["id"] in pending_ids, "risky pending")
        assert_true(forbidden["id"] in pending_ids, "confirmed forbidden pending")

        ok, msg = queue.queue_for_codex([ask["id"]], "thread-test")
        assert_eq(ok, True, "queue codex ok")
        assert_eq(msg, "queued_for_codex", "queue codex msg")
        ok, msg = queue.queue_for_codex([status["id"]], "thread-test")
        assert_eq(ok, False, "active lock ok")
        assert_eq(msg, "another mobile task is already active", "active lock msg")
        queue.mark_sent_to_codex([ask["id"]])
        task = queue.get_task(ask["id"])
        assert_eq(task["status"], "sent_to_codex", "sent status")
        queue.mark_processing(ask["id"])
        queue.complete(ask["id"], "done")
        queue.mark_pushed(ask["id"], True)
        task = queue.get_task(ask["id"])
        assert_eq(task["status"], "pushed_to_wecom", "pushed status")

        health = queue.health()
        assert_eq(health["ok"], True, "health ok")
        assert_eq(health["shadow_mode"], True, "shadow mode")

        tasks = queue.list_tasks(limit=20)
        print(json.dumps({"ok": True, "health": health, "tasks": tasks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
