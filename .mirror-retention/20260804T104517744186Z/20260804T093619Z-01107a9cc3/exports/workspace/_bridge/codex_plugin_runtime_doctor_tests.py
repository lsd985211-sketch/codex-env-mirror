#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import codex_plugin_runtime_doctor as doctor


class CodexPluginRuntimeDoctorTests(unittest.TestCase):
    def test_exit_context_is_not_misclassified_as_plugin_cause(self) -> None:
        lines = [
            '2026-07-16T10:48:51.596Z info app_server_connection.closed code=3221225786 reason="plugin/list failed with status 401 Unauthorized"',
        ]
        events = doctor.classify_log_lines(lines, Path("desktop.log"), datetime.now(timezone.utc))
        self.assertEqual([item["kind"] for item in events], ["external_appserver_interrupt"])
        self.assertEqual(events[0]["cause"], "external_control_event")

    def test_native_addon_failure_keeps_component_path(self) -> None:
        lines = [
            "2026-07-16T10:47:08.775Z error [desktop-notifications][unhandled-rejection] Error: A dynamic link library (DLL) initialization routine failed.",
            r"\\?\C:\Program Files\WindowsApps\OpenAI.Codex_26.707.9981.0_x64__x\app\serialport\serialport.node",
        ]
        events = doctor.classify_log_lines(lines, Path("desktop.log"), datetime.now(timezone.utc))
        self.assertEqual(events[0]["kind"], "native_addon_initialization_failure")
        self.assertTrue(events[0]["addon_path"].endswith("serialport.node"))

    def test_wer_parser_reads_utf8_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Report.wer"
            path.write_text("EventType=APPCRASH\nSig[6].Value=c06d007f\n", encoding="utf-8")
            values = doctor.parse_wer(path)
        self.assertEqual(values["EventType"], "APPCRASH")
        self.assertEqual(values["Sig[6].Value"], "c06d007f")

    def test_aggregate_keeps_actionable_classes_separate(self) -> None:
        events = [
            {"kind": "remote_catalog_auth_unavailable", "occurred_at": "2026-07-16T00:00:00+00:00", "source": "a"},
            {"kind": "native_addon_initialization_failure", "occurred_at": "2026-07-16T00:00:01+00:00", "source": "b"},
        ]
        rows = doctor.aggregate(events)
        self.assertEqual({item["code"] for item in rows}, {"remote_catalog_auth_unavailable", "native_addon_initialization_failure"})


if __name__ == "__main__":
    unittest.main()
