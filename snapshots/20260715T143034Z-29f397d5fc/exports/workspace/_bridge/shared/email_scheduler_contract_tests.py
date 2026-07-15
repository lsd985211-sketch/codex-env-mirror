#!/usr/bin/env python3
from __future__ import annotations

import unittest

import email_scheduler
import email_state_index


class EmailSchedulerContractTests(unittest.TestCase):
    def test_task_serialization_is_one_physical_line(self) -> None:
        row = {header: "" for header in email_scheduler.MAIL_TASK_HEADERS}
        row["任务名"] = "test"
        row["说明"] = "first\nsecond"

        serialized = email_scheduler.serialize_task_row(row)
        parsed = email_scheduler.parse_task_block("\t".join(email_scheduler.MAIL_TASK_HEADERS), [serialized])

        self.assertEqual(serialized.count("\n"), 0)
        self.assertEqual(parsed["说明"], "first\nsecond")

    def test_reply_task_references_immutable_inbound_payload(self) -> None:
        job = {
            "inbound_message_id": "message-123",
            "from": ["sender@example.com"],
            "subject": "待处理",
            "received_at": "2026-07-08T18:52:00+08:00",
            "body_text": "raw body must not enter the task table",
            "attachments": [],
        }

        task = email_scheduler.build_reply_task_from_inbox_job(job)

        self.assertIn("inbound_payload_ref=message-123", task["说明"])
        self.assertNotIn("raw body must not enter the task table", task["说明"])

    def test_inbox_lifecycle_is_derived_from_job_and_receipt(self) -> None:
        self.assertEqual(email_state_index.inbox_lifecycle_status({}, set()), "new")
        self.assertEqual(
            email_state_index.inbox_lifecycle_status({"status": email_scheduler.INBOX_JOB_PROCESSING}, set()),
            "processing",
        )
        self.assertEqual(
            email_state_index.inbox_lifecycle_status({"status": email_scheduler.INBOX_JOB_DEAD_LETTER}, set()),
            "failed/review",
        )
        self.assertEqual(
            email_state_index.inbox_lifecycle_status({"status": email_scheduler.INBOX_JOB_REPLY_DRAFTED}, set()),
            "failed/review",
        )
        self.assertEqual(
            email_state_index.inbox_lifecycle_status(
                {"status": email_scheduler.INBOX_JOB_PROCESSED, "reply_task_name": "reply-task"},
                {"reply-task"},
            ),
            "replied",
        )


if __name__ == "__main__":
    unittest.main()
