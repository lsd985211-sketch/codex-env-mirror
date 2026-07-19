#!/usr/bin/env python3
"""Regression tests for hidden PMB process launch selection."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import patch

import local_pmb_memory_process as runtime
import local_pmb_memory as owner


class PmbProcessRuntimeTests(unittest.TestCase):
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
