#!/usr/bin/env python3
"""Focused tests for durable long-command receipts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from shared import long_command_receipt as owner


class LongCommandReceiptTests(unittest.TestCase):
    def run_in(self, root: str, task_id: str, source: str, *, timeout: int = 5, max_bytes: int = 512) -> dict[str, object]:
        with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": root}):
            return owner.run_command(task_id, [sys.executable, "-c", source], timeout_seconds=timeout, max_inline_bytes=max_bytes)

    def test_success_is_terminal_and_status_reads_same_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "success", "print('done')")
            with patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
                status = owner.status("success")
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(result["terminal"])
        self.assertEqual(result["completed_at"], status["completed_at"])

    def test_failure_retains_stderr_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "failure", "import sys; print('bad', file=sys.stderr); raise SystemExit(7)")
        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual(7, result["exit_code"])
        self.assertIn("bad", result["stderr"])

    def test_long_output_is_bounded_but_full_artifact_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "bounded", "print('x' * 5000)", max_bytes=300)
            raw = Path(temp, "bounded", "stdout.log").read_text(encoding="utf-8")
        self.assertTrue(result["stdout_truncated"])
        self.assertLess(len(result["stdout"]), len(raw))
        self.assertTrue(str(result["raw_result_ref"]).startswith("artifact:"))

    def test_timeout_terminates_and_writes_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_in(temp, "timeout", "import time; time.sleep(5)", timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual("timed_out", result["status"])
        self.assertTrue(result["terminal"])
        self.assertIsInstance(result["exit_code"], int)

    def test_unreaped_timeout_is_not_reported_as_terminal(self) -> None:
        process = Mock(pid=1234)
        process.wait.side_effect = [subprocess.TimeoutExpired("test", 2), subprocess.TimeoutExpired("test", 2)]
        process.poll.return_value = None
        with patch.object(owner.os, "name", "nt"), self.assertRaisesRegex(RuntimeError, "process_not_reaped_after_kill"):
            owner.terminate_group(process)

    def test_permission_error_means_process_exists(self) -> None:
        with patch.object(owner.os, "kill", side_effect=PermissionError):
            self.assertTrue(owner.process_alive(1234))

    def test_invalid_task_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_task_id"):
            owner.task_dir("../escape")

    def test_start_returns_before_worker_and_wait_consumes_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            submitted = owner.start_command(
                "detached-success",
                [sys.executable, "-c", "import time; time.sleep(0.05); print('detached')"],
                timeout_seconds=5,
            )
            result = owner.wait_for_terminal("detached-success", wait_seconds=5, interval_seconds=0.02)
        self.assertTrue(submitted["ok"])
        self.assertEqual("submitted", submitted["status"])
        self.assertTrue(result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertIn("detached", result["stdout"])

    def test_status_marks_missing_worker_without_terminal_receipt_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            directory = owner.task_dir("lost-worker")
            owner.write_json_atomic(directory / "state.json", {"schema": owner.SCHEMA, "task_id": "lost-worker", "status": "running", "pid": 301, "supervisor_pid": 302})
            with patch.object(owner, "process_alive", return_value=False), patch.object(owner.time, "monotonic", return_value=100.0):
                pending = owner.status("lost-worker")
                pending["finalization_deadline_monotonic"] = 99.0
                owner.write_json_atomic(directory / "state.json", pending)
                result = owner.status("lost-worker")
        self.assertFalse(result["ok"])
        self.assertEqual("monitor_lost", result["status"])
        self.assertEqual("worker_and_command_exited_without_terminal_receipt", result["reason"])

    def test_status_gives_worker_a_finalization_grace_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            directory = owner.task_dir("finalization-race")
            owner.write_json_atomic(directory / "state.json", {"schema": owner.SCHEMA, "task_id": "finalization-race", "status": "running", "pid": 401, "supervisor_pid": 402})
            with patch.object(owner, "process_alive", return_value=False):
                pending = owner.status("finalization-race")
                self.assertEqual("running", pending["status"])
                self.assertTrue(pending["finalization_pending"])
                pending["finalization_deadline_monotonic"] = 1
                owner.write_json_atomic(directory / "state.json", pending)
                with patch.object(owner.time, "monotonic", return_value=2):
                    failed = owner.status("finalization-race")
        self.assertEqual("monitor_lost", failed["status"])

    def test_intent_submission_reuses_terminal_receipt_without_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            marker = Path(temp) / "runs.txt"
            command = [
                sys.executable,
                "-c",
                f"from pathlib import Path; p=Path({str(marker)!r}); p.write_text((p.read_text() if p.exists() else '') + 'run\\n')",
            ]
            submitted = owner.submit_or_reuse("mirror:publish:stable-head", command, timeout_seconds=5)
            terminal = owner.wait_for_terminal(str(submitted["task_id"]), wait_seconds=5, interval_seconds=0.02)
            replay = owner.submit_or_reuse("mirror:publish:stable-head", command, timeout_seconds=5)
            run_count = marker.read_text(encoding="utf-8")
        self.assertTrue(terminal["ok"])
        self.assertTrue(replay["submit_ok"])
        self.assertTrue(replay["reused"])
        self.assertEqual("terminal", replay["reuse_state"])
        self.assertEqual("run\n", run_count)

    def test_same_intent_with_different_execution_signature_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            first = owner.submit_or_reuse("mirror:refresh:stable-head", [sys.executable, "-c", "print('one')"], timeout_seconds=5)
            owner.wait_for_terminal(str(first["task_id"]), wait_seconds=5, interval_seconds=0.02)
            conflict = owner.submit_or_reuse("mirror:refresh:stable-head", [sys.executable, "-c", "print('two')"], timeout_seconds=5)
        self.assertFalse(conflict["ok"])
        self.assertEqual("task_id_execution_signature_conflict", conflict["reason"])

    def test_follow_compacts_output_and_preserves_terminal_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            submitted = owner.submit_or_reuse(
                "validation:large-output",
                [sys.executable, "-c", "print('x' * 5000)"],
                timeout_seconds=5,
                max_inline_bytes=5000,
            )
            result = owner.follow_command(str(submitted["task_id"]), wait_seconds=5, interval_seconds=0.02)
        self.assertTrue(result["terminal"])
        self.assertEqual(0, result["exit_code"])
        self.assertIn("raw_result_ref", result)
        self.assertIn("stdout_preview", result)
        self.assertNotIn("stdout", result)
        self.assertFalse(result["business_command_resubmit_allowed"])
        self.assertEqual("default_bounded", result["output_mode"])
        self.assertEqual("log", result["context_projection"]["source_kind"])
        self.assertEqual(1.0, result["context_projection"]["functional_recall"])

    def test_compact_and_full_use_one_projection_owner_with_real_detail_difference(self) -> None:
        payload = {
            "schema": f"{owner.SCHEMA}.result",
            "ok": False,
            "task_id": "projection-detail",
            "intent_id": "test:projection-detail",
            "status": "failed",
            "terminal": True,
            "reason": "command_failed",
            "exit_code": 7,
            "stdout": "out" * 12000,
            "stderr": "root-cause\n" + "err" * 12000,
            "raw_result_ref": "artifact:/tmp/long-command-result",
            "stdout_ref": "artifact:/tmp/long-command-result/stdout.log",
            "stderr_ref": "artifact:/tmp/long-command-result/stderr.log",
        }
        compact = owner.project_status(payload)
        full = owner.project_status(payload, detail="full")
        compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
        full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
        self.assertEqual("default_bounded", compact["output_mode"])
        self.assertEqual("full_bounded", full["output_mode"])
        self.assertGreater(full_bytes, compact_bytes)
        self.assertLessEqual(compact_bytes, 12 * 1024)
        self.assertLessEqual(full_bytes, 36 * 1024)
        self.assertEqual(compact["raw_result_ref"], full["raw_result_ref"])
        self.assertEqual("log", full["context_projection"]["source_kind"])
        self.assertFalse(full["context_projection"]["headroom_executed"])

    def test_converge_submits_once_and_returns_only_terminal_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            marker = Path(temp) / "runs.txt"
            command = [
                sys.executable,
                "-c",
                f"import time; from pathlib import Path; p=Path({str(marker)!r}); p.write_text('run\\n'); time.sleep(0.1); print('done')",
            ]
            result = owner.converge_or_reuse(
                "test:single-consume", command, timeout_seconds=5, interval_seconds=0.02
            )
            replay = owner.converge_or_reuse(
                "test:single-consume", command, timeout_seconds=5, interval_seconds=0.02
            )
            self.assertTrue(result["terminal"])
            self.assertEqual("completed", result["status"])
            self.assertTrue(result["single_consume"])
            self.assertNotIn("next_action", result)
            self.assertTrue(replay["submission_reused"])
            self.assertEqual("run\n", marker.read_text(encoding="utf-8"))

    def test_consume_terminal_by_intent_recovers_after_transport_loss_without_starting_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            command = [sys.executable, "-c", "print('durable')"]
            original = owner.converge_or_reuse(
                "test:transport-loss", command, timeout_seconds=5, interval_seconds=0.02
            )
            with patch.object(owner, "start_command", side_effect=AssertionError("must not submit")):
                recovered = owner.consume_terminal_by_intent(
                    "test:transport-loss", command, timeout_seconds=5
                )

        self.assertTrue(original["terminal"])
        self.assertTrue(recovered["terminal"])
        self.assertTrue(recovered["recovered_from_durable_receipt"])
        self.assertEqual(original["exit_code"], recovered["exit_code"])
        self.assertFalse(recovered["business_command_resubmit_allowed"])

    def test_consume_terminal_by_intent_blocks_signature_mismatch_without_submit(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            command = [sys.executable, "-c", "print('first')"]
            owner.converge_or_reuse("test:signature-guard", command, timeout_seconds=5, interval_seconds=0.02)
            with patch.object(owner, "start_command", side_effect=AssertionError("must not submit")):
                recovered = owner.consume_terminal_by_intent(
                    "test:signature-guard",
                    [sys.executable, "-c", "print('different')"],
                    timeout_seconds=5,
                )

        self.assertFalse(recovered["ok"])
        self.assertEqual("intent_execution_signature_conflict", recovered["reason"])
        self.assertFalse(recovered["business_command_resubmit_allowed"])

    def test_worker_injects_durable_execution_context_into_business_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            output = Path(temp) / "child-context.json"
            command = [
                sys.executable,
                "-c",
                (
                    "import json, os; from pathlib import Path; "
                    f"Path({str(output)!r}).write_text(json.dumps({{k: os.environ.get(k, '') for k in "
                    "('CODEX_LONG_COMMAND_TASK_ID','CODEX_LONG_COMMAND_INTENT_ID','CODEX_LONG_COMMAND_EXECUTION_SIGNATURE')}))"
                ),
            ]
            result = owner.converge_or_reuse(
                "test:child-context", command, timeout_seconds=5, interval_seconds=0.02
            )
            context = json.loads(output.read_text(encoding="utf-8"))

        self.assertTrue(result["terminal"])
        self.assertEqual("test:child-context", context["CODEX_LONG_COMMAND_INTENT_ID"])
        self.assertTrue(context["CODEX_LONG_COMMAND_TASK_ID"].startswith("intent-"))
        self.assertEqual(64, len(context["CODEX_LONG_COMMAND_EXECUTION_SIGNATURE"]))

    def test_concurrent_converge_starts_one_business_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            marker = Path(temp) / "runs.txt"
            command = [
                sys.executable,
                "-c",
                f"import os, time; fd=os.open({str(marker)!r}, os.O_CREAT | os.O_WRONLY | os.O_APPEND); os.write(fd, b'run\\n'); os.close(fd); time.sleep(0.1)",
            ]
            barrier = threading.Barrier(2)
            results: list[dict[str, object]] = []

            def converge() -> None:
                barrier.wait()
                results.append(owner.converge_or_reuse("test:concurrent-single-consume", command, timeout_seconds=5, interval_seconds=0.02))

            left = threading.Thread(target=converge)
            right = threading.Thread(target=converge)
            left.start()
            right.start()
            left.join()
            right.join()

            self.assertEqual(2, len(results))
            self.assertTrue(all(result["terminal"] for result in results))
            self.assertEqual("run\n", marker.read_text(encoding="utf-8"))

    def test_running_observation_never_emits_poll_command(self) -> None:
        with patch.object(owner, "wait_for_terminal", return_value={
            "schema": f"{owner.SCHEMA}.status",
            "task_id": "running",
            "status": "deferred",
            "terminal": False,
            "reason": "terminal_receipt_not_ready",
        }):
            result = owner.follow_command("running", wait_seconds=0)
        self.assertTrue(result["observation_only"])
        self.assertNotIn("next_action", result)
        self.assertNotIn("follow --task-id", json.dumps(result))
        self.assertNotIn("status --task-id", json.dumps(result))

    def test_convergence_deadline_surfaces_recovery_without_a_poll_command(self) -> None:
        with (
            patch.object(owner, "start_command", return_value={"submit_ok": True, "reused": False}),
            patch.object(owner, "wait_for_terminal", return_value={"status": "deferred", "terminal": False}),
        ):
            result = owner.converge_command("deadline", [sys.executable, "-c", "print('unused')"], timeout_seconds=1)
        self.assertTrue(result["recovery_required"])
        self.assertEqual("inspect_unconsumable_receipt_and_route_owner_recovery", result["next_action"])
        self.assertNotIn("follow --task-id", json.dumps(result))
        self.assertNotIn("status --task-id", json.dumps(result))

    def test_unconsumable_stopped_receipt_routes_recovery_without_follow_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CODEX_LONG_COMMAND_RECEIPT_ROOT": temp}):
            directory = owner.task_dir("lost-terminal")
            owner.write_json_atomic(
                directory / "state.json",
                {
                    "schema": f"{owner.SCHEMA}.result",
                    "task_id": "lost-terminal",
                    "status": "monitor_lost",
                    "terminal": False,
                    "reason": "worker_and_command_exited_without_terminal_receipt",
                    "raw_result_ref": f"artifact:{directory}",
                },
            )
            result = owner.follow_command("lost-terminal", wait_seconds=0)
        self.assertTrue(result["recovery_required"])
        self.assertEqual("inspect_unconsumable_receipt_and_route_owner_recovery", result["next_action"])
        self.assertNotIn("follow --task-id", result["next_action"])
        self.assertFalse(result["business_command_resubmit_allowed"])


if __name__ == "__main__":
    unittest.main()
