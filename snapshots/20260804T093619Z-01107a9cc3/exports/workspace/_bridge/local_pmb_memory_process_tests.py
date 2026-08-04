#!/usr/bin/env python3
"""Regression tests for hidden PMB process launch selection."""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
import re
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import local_pmb_memory_process as runtime
import local_pmb_memory as owner


class PmbProcessRuntimeTests(unittest.TestCase):
    def test_recall_integration_returns_query_id_without_changing_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(owner, "REPORT_DIR", root), mock.patch.object(
                owner,
                "run_pmb",
                return_value={"ok": True, "stdout": "match", "stderr": ""},
            ):
                result = owner.pmb_recall("测试查询", top_k=3)
            self.assertTrue(result["ok"])
            self.assertTrue(result["has_matches"])
            self.assertRegex(result["query_id"], r"^[0-9a-f]{32}$")
            self.assertEqual("match", result["stdout"])
            self.assertEqual("opaque", result["recall_evidence"]["trace_state"])
            events = owner.read_usage_feedback_events(default_root=root)
            self.assertEqual(2, len(events))
            self.assertEqual(result["query_id"], events[0]["query_id"])
            evidence = events[1]
            self.assertEqual("recall_evidence", evidence["event"])
            self.assertEqual(result["query_id"], evidence["query_id"])
            self.assertEqual("opaque", evidence["l0"]["candidate_trace_state"])
            self.assertEqual([], evidence["l1"]["candidates"])
            self.assertNotIn("match", json.dumps(evidence["l1"]["candidates"], ensure_ascii=False))
            self.assertEqual(
                {
                    "schema", "recall_ok", "has_matches", "elapsed_ms", "top_k",
                    "candidate_trace_state", "candidate_count", "l2_preview_sha256", "degradation_reason",
                },
                set(evidence["l0"]),
            )
            self.assertEqual(owner.sha256_text("match"), evidence["l0"]["l2_preview_sha256"])

    def test_records_bounded_recall_and_feedback_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            query_id = owner.record_recall_usage(
                default_root=root,
                query="  高血压 机制 ",
                top_k=5,
                ok=True,
                has_matches=True,
                elapsed_ms=12,
                preview="x" * 10000,
            )
            feedback = owner.record_usage_feedback(
                default_root=root,
                query_id=query_id,
                feedback_type="used",
                note="内容被用于回答",
            )
            self.assertEqual("used", feedback["feedback_type"])
            summary = owner.usage_feedback_summary(default_root=root)
            self.assertEqual(1, summary["recall_count"])
            self.assertEqual(1, summary["feedback_count"])
            self.assertEqual({"used": 1}, summary["feedback_by_type"])
            self.assertEqual(0, summary["recall_evidence_count"])
            line = (root / "usage_feedback.jsonl").read_text(encoding="utf-8").splitlines()[0]
            self.assertLess(len(line), 1200)
            self.assertEqual("pmb-usage-feedback.recall.v1", json.loads(line)["schema"])

    def test_recall_evidence_distinguishes_empty_and_failed_without_copying_preview(self) -> None:
        empty = owner.build_recall_evidence(
            query_id="empty", top_k=5, ok=True, has_matches=False, elapsed_ms=4, preview="secret=abc"
        )
        failed = owner.build_recall_evidence(
            query_id="failed", top_k=5, ok=False, has_matches=False, elapsed_ms=4, preview="token=abc"
        )
        self.assertEqual("empty", empty["l0"]["candidate_trace_state"])
        self.assertEqual("failed", failed["l0"]["candidate_trace_state"])
        self.assertNotIn("secret=abc", json.dumps(empty, ensure_ascii=False))
        self.assertNotIn("token=abc", json.dumps(failed, ensure_ascii=False))

    def test_evidence_write_failure_does_not_change_recall_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(owner, "REPORT_DIR", root), mock.patch.object(
                owner, "run_pmb", return_value={"ok": True, "stdout": "match", "stderr": ""}
            ), mock.patch.object(owner, "record_recall_evidence", side_effect=OSError("disk unavailable")):
                result = owner.pmb_recall("测试查询", top_k=3)
            self.assertTrue(result["ok"])
            self.assertEqual("match", result["stdout"])
            self.assertFalse(result["recall_evidence"]["ok"])
            self.assertNotIn("usage_feedback", result)

    def test_usage_summary_reports_evidence_coverage_and_trace_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            query_id = owner.record_recall_usage(
                default_root=root, query="测试", top_k=2, ok=True, has_matches=True, elapsed_ms=3, preview="match"
            )
            owner.record_recall_evidence(
                default_root=root, query_id=query_id, top_k=2, ok=True, has_matches=True, elapsed_ms=3, preview="match"
            )
            summary = owner.usage_feedback_summary(default_root=root)
            self.assertEqual(1, summary["recall_evidence_count"])
            self.assertEqual(1, summary["evidence_coverage_count"])
            self.assertEqual(0, summary["evidence_missing_count"])
            self.assertEqual({"opaque": 1}, summary["evidence_trace_state_counts"])

    def test_rejects_unknown_feedback_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                owner.record_usage_feedback(default_root=Path(temp), query_id="q", feedback_type="maybe")

    @unittest.skipIf(os.name == "nt", "WSL interoperability contract")
    def test_owner_uses_wsl_native_runtime_without_windows_interop(self) -> None:
        env = owner.pmb_env()

        self.assertTrue(str(owner.PMB_EXE).endswith("/bin/pmb"))
        self.assertTrue(str(owner.PMB_HOME).startswith(str(Path.home() / ".local" / "share")))
        self.assertEqual(str(owner.PMB_HOME), env["PMB_HOME"])
        self.assertEqual("mcsmanager", env["PMB_WORKSPACE"])
        self.assertNotIn("WSLENV", env)

    def test_missing_retired_tombstones_skips_optional_legacy_archives(self) -> None:
        with patch.object(owner, "retirement_tombstones", return_value=[]):
            archive_root = owner.retired_member_archive_root()
        sources = owner.build_legacy_memory_sources(archive_root)

        self.assertIsNone(archive_root)
        self.assertIn("codex_memory_markdown", sources)
        self.assertNotIn("chroma_memory", sources)

    def test_owner_process_observer_accepts_python_and_pythonw(self) -> None:
        pattern = re.compile(owner.PMB_DAEMON_PROCESS_NAME_REGEX, re.IGNORECASE)
        self.assertIsNotNone(pattern.match("python.exe"))
        self.assertIsNotNone(pattern.match("pythonw.exe"))

    def test_process_count_does_not_claim_a_cold_daemon_is_warm(self) -> None:
        state = owner.effective_daemon_state(
            {"running": True, "warm": False},
            {"root_count": 1},
        )
        self.assertTrue(state["running"])
        self.assertFalse(state["warm"])

    @unittest.skipUnless(os.name == "nt", "Windows process-launch contract")
    def test_daemon_start_uses_pythonw_module_launcher_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pmb_exe = root / "pmb.exe"
            pythonw = root / "pythonw.exe"
            pmb_exe.touch()
            pythonw.touch()
            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch.object(runtime.subprocess, "run", return_value=completed) as run:
                result = runtime.run_pmb_command(
                    pmb_exe=pmb_exe,
                    pmb_pythonw=pythonw,
                    args=["daemon", "start"],
                    cwd=root,
                    env={},
                    timeout=10,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["launcher"], "pythonw_module")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [str(pythonw), "-m", "pmb.cli", "daemon"])
        self.assertTrue(int(run.call_args.kwargs.get("creationflags", 0)) & 0x08000000)

    @unittest.skipUnless(os.name == "nt", "Windows hidden-process contract")
    def test_status_keeps_pmb_entrypoint_but_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pmb_exe = root / "pmb.exe"
            pythonw = root / "pythonw.exe"
            pmb_exe.touch()
            completed = subprocess.CompletedProcess([], 0, stdout="running", stderr="")
            with patch.object(runtime.subprocess, "run", return_value=completed) as run:
                result = runtime.run_pmb_command(
                    pmb_exe=pmb_exe,
                    pmb_pythonw=pythonw,
                    args=["daemon", "status"],
                    cwd=root,
                    env={},
                    timeout=10,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["launcher"], "pmb_entrypoint")
        self.assertEqual(run.call_args.args[0][0], str(pmb_exe))
        self.assertTrue(int(run.call_args.kwargs.get("creationflags", 0)) & 0x08000000)

    @unittest.skipUnless(os.name == "nt", "Windows pythonw contract")
    def test_missing_pythonw_fails_without_falling_back_to_visible_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pmb_exe = root / "pmb.exe"
            pmb_exe.touch()
            with patch.object(runtime.subprocess, "run") as run:
                result = runtime.run_pmb_command(
                    pmb_exe=pmb_exe,
                    pmb_pythonw=root / "missing-pythonw.exe",
                    args=["daemon", "start"],
                    cwd=root,
                    env={},
                    timeout=10,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "pmb_pythonw_missing")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
