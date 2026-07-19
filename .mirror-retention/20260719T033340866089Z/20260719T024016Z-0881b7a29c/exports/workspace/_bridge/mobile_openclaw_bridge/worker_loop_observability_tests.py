from __future__ import annotations

import unittest

from worker_loop_observability import worker_loop_has_activity


class WorkerLoopActivityTests(unittest.TestCase):
    def test_skipped_pending_reply_retries_do_not_block_idle_backoff(self) -> None:
        result = {
            "action": "idle",
            "processed": 0,
            "pending_reply_retries": {
                "scheduled": 0,
                "skipped": 3,
                "waiting_context": 0,
            },
        }

        self.assertFalse(worker_loop_has_activity(result))

    def test_scheduled_pending_reply_retry_keeps_worker_responsive(self) -> None:
        result = {
            "action": "recovery_cycle",
            "processed": 0,
            "pending_reply_retries": {
                "scheduled": 1,
                "skipped": 3,
            },
        }

        self.assertTrue(worker_loop_has_activity(result))

    def test_processed_task_remains_activity(self) -> None:
        self.assertTrue(worker_loop_has_activity({"action": "idle", "processed": 1}))

    def test_busy_route_wait_remains_responsive(self) -> None:
        result = {
            "action": "idle_no_dispatchable_thread",
            "processed": 0,
            "skipped_busy_route": 1,
        }

        self.assertTrue(worker_loop_has_activity(result))


if __name__ == "__main__":
    unittest.main()
