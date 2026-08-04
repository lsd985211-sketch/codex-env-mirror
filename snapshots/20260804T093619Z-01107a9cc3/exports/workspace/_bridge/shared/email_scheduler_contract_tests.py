#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import email_scheduler
import email_state_index


class EmailSchedulerContractTests(unittest.TestCase):
    def test_deeptutor_artifact_delivery_is_exactly_mapped_and_idempotent(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "outputs"
            artifact_root.mkdir()
            artifact = artifact_root / "lecture.html"
            artifact.write_text("<html>lecture</html>", encoding="utf-8")
            identity_table = root / "identities.txt"
            identity_table.write_text(
                """## 身份 1
- 身份名：主发送者
### 对应账号
- sender@example.test
### 对应 SMTP
- smtp.example.test
- 465
- SSL/TLS
- test-secret
## 身份 2
- 身份名：学习者
### 对应账号
- 123456@qq.com
""",
                encoding="utf-8",
            )
            task_table = root / "tasks.txt"
            stage_root = root / "state"
            paths = {
                "MAIL_IDENTITY_TXT": identity_table,
                "MAIL_TASK_TXT": task_table,
                "SCHEDULE_RUNS_DIR": stage_root / "schedule_runs",
                "CONTENT_JOBS_DIR": stage_root / "content_jobs",
                "DRAFT_ITEMS_DIR": stage_root / "draft_items",
                "OUTBOX_ITEMS_DIR": stage_root / "outbox_items",
                "OUTBOX_INDEX_PATH": stage_root / "outbox_index.json",
                "DELIVERY_JOBS_DIR": stage_root / "delivery_jobs",
            }
            with patch.multiple(email_scheduler, **paths):
                request = dict(
                    partner_id="medical-textbook-tutor",
                    source_channel="napcat",
                    source_sender_id="123456",
                    source_chat_id="private:123456",
                    session_key="napcat:private:123456",
                    topic="休克",
                    artifacts=[{
                        "path": str(artifact), "url": "/api/outputs/lecture.html",
                        "filename": "lecture.html", "mime_type": "text/html",
                    }],
                    artifact_root=artifact_root,
                )
                first = email_scheduler.create_deeptutor_artifact_delivery(**request)
                second = email_scheduler.create_deeptutor_artifact_delivery(**request)
                outbox = email_scheduler.read_stage(
                    "outbox_item", email_scheduler.outbox_item_id(first["schedule_run_id"])
                )

            self.assertTrue(first["created"])
            self.assertFalse(first["sends_mail"])
            self.assertFalse(second["created"])
            self.assertEqual(first["delivery_id"], second["delivery_id"])
            task = email_scheduler.parse_task_table(task_table)[0]
            metadata = email_scheduler.task_metadata(task)
            self.assertEqual(metadata["source_sender_id"], "123456")
            self.assertEqual(metadata["recipient_account"], "123456@qq.com")
            self.assertEqual(metadata["sender_account"], "sender@example.test")
            self.assertEqual(metadata["partner_id"], "medical-textbook-tutor")
            self.assertEqual(metadata["artifact_filenames"], "lecture.html")
            self.assertTrue(metadata["artifact_sha256"])
            self.assertEqual(metadata["content_mode"], "static")
            self.assertIn("/api/outputs/lecture.html", email_scheduler.extract_static_body(task))
            self.assertEqual(email_scheduler.extract_static_body(task), "http://localhost:3782/api/outputs/lecture.html")
            self.assertEqual(email_scheduler.parse_attachment_paths(task), [artifact])
            runtime = email_scheduler.build_task_runtime(task, email_scheduler.parse_identity_table(identity_table))
            self.assertEqual(runtime["content_mode"], "static")
            self.assertEqual(runtime["attachments"], [artifact])
            self.assertEqual(outbox["attachments"], [str(artifact)])

    def test_deeptutor_artifact_delivery_rejects_non_napcat_source(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "outputs"
            artifact_root.mkdir()
            artifact = artifact_root / "lecture.html"
            artifact.write_text("<html>lecture</html>", encoding="utf-8")
            identity_table = root / "identities.txt"
            identity_table.write_text(
                """## 身份 1
- 身份名：主发送者
### 对应账号
- sender@example.test
### 对应 SMTP
- smtp.example.test
- 465
- SSL/TLS
- test-secret
## 身份 2
- 身份名：学习者
### 对应账号
- 123456@qq.com
""",
                encoding="utf-8",
            )
            task_table = root / "tasks.txt"
            with patch.multiple(email_scheduler, MAIL_IDENTITY_TXT=identity_table, MAIL_TASK_TXT=task_table):
                result = email_scheduler.create_deeptutor_artifact_delivery(
                    partner_id="medical-textbook-tutor",
                    source_channel="wechat",
                    source_sender_id="123456",
                    source_chat_id="private:123456",
                    session_key="wechat:private:123456",
                    topic="讲义",
                    artifacts=[{"path": str(artifact), "url": "/api/outputs/lecture.html"}],
                    artifact_root=artifact_root,
                )

            self.assertFalse(result["created"])
            self.assertEqual(result["reason"], "unsupported_source_channel")
            self.assertFalse(task_table.exists())

    def test_deeptutor_artifact_delivery_does_not_guess_an_unmapped_recipient(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identity_table = root / "identities.txt"
            identity_table.write_text("", encoding="utf-8")
            task_table = root / "tasks.txt"
            with patch.multiple(email_scheduler, MAIL_IDENTITY_TXT=identity_table, MAIL_TASK_TXT=task_table):
                result = email_scheduler.create_deeptutor_artifact_delivery(
                    partner_id="medical-textbook-tutor",
                    source_channel="napcat", source_sender_id="999", source_chat_id="private:999",
                    session_key="napcat:private:999", topic="讲义", artifacts=[], artifact_root=root,
                )

            self.assertFalse(result["created"])
            self.assertEqual(result["reason"], "recipient_identity_missing")
            self.assertFalse(task_table.exists())
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
