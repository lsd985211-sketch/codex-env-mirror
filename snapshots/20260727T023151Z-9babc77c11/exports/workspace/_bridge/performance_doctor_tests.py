#!/usr/bin/env python3
"""Focused regression tests for performance doctor process sampling."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import performance_doctor


class FakeProcess:
    def __init__(self, info: dict[str, object], dynamic_cmdline: list[str] | None = None) -> None:
        self.info = info
        self.dynamic_cmdline = dynamic_cmdline or []
        self.cmdline_calls = 0

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=16 * 1024 * 1024, private=12 * 1024 * 1024)

    def cpu_times(self) -> SimpleNamespace:
        return SimpleNamespace(user=1.0, system=0.5)

    def io_counters(self) -> SimpleNamespace:
        return SimpleNamespace(read_bytes=100, write_bytes=50)

    def cmdline(self) -> list[str]:
        self.cmdline_calls += 1
        return self.dynamic_cmdline

    def exe(self) -> str:
        return str(self.info.get("exe") or "")


class PerformanceDoctorSamplingTests(unittest.TestCase):
    def test_powershell_sampling_uses_shared_resolved_encoded_command(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout='{"items": []}', stderr="")
        with patch.object(
            performance_doctor,
            "powershell_encoded_command",
            return_value=["/host/powershell.exe", "-EncodedCommand", "abc"],
        ) as build, patch.object(performance_doctor.subprocess, "run", return_value=completed) as run:
            performance_doctor.run_powershell_json("Write-Output '{}'", timeout=5)

        build.assert_called_once()
        self.assertEqual("/host/powershell.exe", run.call_args.args[0][0])

    def fake_psutil(self, processes: list[FakeProcess], captured_attrs: list[list[str]]) -> SimpleNamespace:
        def process_iter(attrs: list[str]) -> list[FakeProcess]:
            captured_attrs.append(list(attrs))
            return processes

        return SimpleNamespace(
            process_iter=process_iter,
            AccessDenied=RuntimeError,
            NoSuchProcess=RuntimeError,
            ZombieProcess=RuntimeError,
        )

    def test_quick_sampling_avoids_parent_map_and_limits_command_lines(self) -> None:
        python = FakeProcess(
            {"pid": 10, "name": "python.exe", "create_time": 1.0},
            ["python.exe", "worker.py"],
        )
        notepad = FakeProcess({"pid": 11, "name": "notepad.exe", "create_time": 1.0})
        captured_attrs: list[list[str]] = []

        with patch.dict(sys.modules, {"psutil": self.fake_psutil([python, notepad], captured_attrs)}):
            rows = performance_doctor.process_rows_psutil(
                include_parent=False,
                command_line_scope="relevant",
            )

        self.assertEqual(captured_attrs, [["pid", "name", "create_time"]])
        self.assertEqual(python.cmdline_calls, 1)
        self.assertEqual(notepad.cmdline_calls, 0)
        self.assertEqual(rows[0]["command_line"], "python.exe worker.py")
        self.assertEqual(rows[1]["command_line"], "")
        self.assertTrue(all(row["parent_pid"] == 0 and row["parent_name"] == "" for row in rows))

    def test_default_sampling_preserves_parent_and_full_command_line_contract(self) -> None:
        parent = FakeProcess(
            {
                "pid": 20,
                "ppid": 0,
                "name": "python.exe",
                "cmdline": ["python.exe", "parent.py"],
                "exe": "python.exe",
                "create_time": 1.0,
            }
        )
        child = FakeProcess(
            {
                "pid": 21,
                "ppid": 20,
                "name": "node.exe",
                "cmdline": ["node.exe", "child.js"],
                "exe": "node.exe",
                "create_time": 1.0,
            }
        )
        captured_attrs: list[list[str]] = []

        with patch.dict(sys.modules, {"psutil": self.fake_psutil([parent, child], captured_attrs)}):
            rows = performance_doctor.process_rows_psutil()

        self.assertEqual(
            captured_attrs,
            [["pid", "name", "create_time", "ppid", "cmdline", "exe"]],
        )
        self.assertEqual(rows[1]["parent_pid"], 20)
        self.assertEqual(rows[1]["parent_name"], "python.exe")
        self.assertEqual(rows[1]["command_line"], "node.exe child.js")


if __name__ == "__main__":
    unittest.main()
