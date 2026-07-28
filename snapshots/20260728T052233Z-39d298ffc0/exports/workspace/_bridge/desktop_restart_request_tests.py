#!/usr/bin/env python3
"""Focused tests for the typed Desktop full-restart request lifecycle."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import desktop_restart_request as restart


class DesktopRestartRequestTests(unittest.TestCase):
    @staticmethod
    def _write_consuming_request(path: Path) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        request = {
            "schema": restart.SCHEMA,
            "request_id": "request-1",
            "requested_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "reason": "test",
            "token": restart.TOKEN,
        }
        request["input_signature"] = restart._signature(request)
        path.write_text(json.dumps(request), encoding="utf-8")
        return request

    def test_plan_reuses_the_existing_typed_launcher_task(self) -> None:
        invocation = {"ok": True, "confirmation": "RUN-WINDOWS-EXECUTION:desktop.start_elevated"}
        with patch.object(restart.windows_execution_agent, "invoke_plan", return_value=invocation):
            payload = restart.plan()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["confirmation"], restart.CONFIRM)
        self.assertFalse(payload["force_kill_allowed"])

    def test_apply_writes_a_signed_short_lived_request_before_task_handoff(self) -> None:
        root = Path(tempfile.mkdtemp())
        request_path = root / "request.json"
        receipt_path = root / "receipt.json"
        invocation_plan = {"ok": True, "confirmation": "RUN-WINDOWS-EXECUTION:desktop.start_elevated"}
        invocation = {"ok": True, "status": "accepted"}
        with patch.object(restart.windows_execution_agent, "invoke_plan", return_value=invocation_plan), patch.object(
            restart.windows_execution_agent, "invoke", return_value=invocation
        ) as invoked:
            payload = restart.apply(restart.CONFIRM, request_path=request_path, receipt_path=receipt_path)

        self.assertTrue(payload["ok"])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(request["schema"], restart.SCHEMA)
        self.assertEqual(request["token"], restart.TOKEN)
        self.assertEqual(request["input_signature"], restart._signature(request))
        invoked.assert_called_once_with(restart.OPERATION, invocation_plan["confirmation"])

    def test_status_requires_both_launcher_receipt_and_live_git_worker(self) -> None:
        root = Path(tempfile.mkdtemp())
        receipt_path = root / "receipt.json"
        receipt_path.write_text(
            json.dumps({"ok": True, "status": "completed", "force_kill_used": False}),
            encoding="utf-8",
        )
        payload = restart.status(
            desktop_snapshot=lambda: {"desktop_classifier_recognized": True},
            request_path=root / "request.json",
            receipt_path=receipt_path,
        )
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["acceptance"]["launcher_receipt_complete"])
        self.assertTrue(payload["acceptance"]["desktop_classifier_recognized"])

    def test_graceful_exit_rejects_unsigned_or_mismatched_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "consuming.json"
            request = self._write_consuming_request(path)
            payload = restart.signal_graceful_exit(
                request["request_id"],
                "wrong-signature",
                consuming_path=path,
            )
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["requested"])
        self.assertEqual(payload["reason"], "restart_request_invalid_or_expired")

    def test_graceful_exit_uses_native_quit_ipc_and_requires_process_quiescence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "consuming.json"
            request = self._write_consuming_request(path)
            client = unittest.mock.MagicMock()
            client.evaluate.return_value = {"ok": True, "reason": "electron_quit_ipc_sent"}
            with patch.object(restart, "_desktop_process_ids", side_effect=[[18500], []]), patch.object(
                restart.codex_desktop_model_runtime,
                "_find_codex_page",
                return_value=(9231, "ws://127.0.0.1:9231/devtools/page/1", [{}], ""),
            ), patch.object(restart.codex_desktop_model_runtime, "_CdpClient", return_value=client):
                payload = restart.signal_graceful_exit(
                    request["request_id"],
                    request["input_signature"],
                    consuming_path=path,
                    wait_seconds=1,
                )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["method"], "electron_quit_ipc")
        self.assertFalse(payload["force_kill_used"])
        expression = client.evaluate.call_args.args[0]
        self.assertIn("sendMessageFromView", expression)
        self.assertIn("quit-app", expression)

    def test_quit_expression_uses_the_installed_preload_bridge_contract(self) -> None:
        expression = restart._quit_expression()
        self.assertIn("window.electronBridge", expression)
        self.assertIn("sendMessageFromView", expression)
        self.assertIn("{type:'quit-app'}", expression)
        self.assertNotIn("Browser.close", expression)


if __name__ == "__main__":
    unittest.main()
