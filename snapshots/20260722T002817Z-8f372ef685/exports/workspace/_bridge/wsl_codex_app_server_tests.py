#!/usr/bin/env python3
"""Focused tests for the WSL user-level Codex app-server owner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wsl_codex_app_server as owner


class WslCodexAppServerTests(unittest.TestCase):
    def test_unit_is_user_scoped_unix_socket_and_no_tcp(self) -> None:
        content = owner.unit_content(
            executable=Path("/usr/bin/codex"),
            codex_home=Path("/home/test/.codex"),
            workspace=Path("/home/test/workspace"),
        )
        self.assertIn("app-server --listen unix://%t/codex-app-server.sock", content)
        self.assertIn("Environment=CODEX_HOME=/home/test/.codex", content)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertNotIn("0.0.0.0", content)
        self.assertNotIn("--remote-control", content)

    def test_plan_reports_executable_and_content_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(owner, "unit_path", return_value=Path(temp_dir) / owner.SERVICE_NAME), patch.object(owner, "codex_executable", return_value=Path("/usr/bin/sh")):
            payload = owner.plan(workspace=Path(temp_dir), codex_home=Path(temp_dir) / ".codex")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["executable_sha256"])
        self.assertTrue(payload["unit_sha256"])
        self.assertFalse(payload["would_change"] is False)

    def test_install_requires_confirmation_without_systemctl_write(self) -> None:
        with patch.object(owner, "plan", return_value={"ok": True, "blockers": []}) as planned, patch.object(owner, "systemctl") as systemctl:
            payload = owner.install("")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_confirmation"], owner.INSTALL_CONFIRM)
        planned.assert_called_once()
        systemctl.assert_not_called()

    def test_status_never_claims_tcp_or_windows_desktop_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(owner, "unit_path", return_value=Path(temp_dir) / owner.SERVICE_NAME), patch.object(owner, "systemctl", return_value={"ok": False, "stdout": "", "stderr": ""}):
            payload = owner.status()
        self.assertFalse(payload["boundary"]["tcp_exposed"])
        self.assertFalse(payload["boundary"]["windows_desktop_owner_replaced"])
        self.assertFalse(payload["boundary"]["root_or_system"])

    def test_executable_prefers_linux_entry_over_windows_path(self) -> None:
        with patch.dict(owner.os.environ, {}, clear=False):
            owner.os.environ.pop("CODEX_APP_SERVER_EXECUTABLE", None)
            with patch.object(owner.Path, "is_file", autospec=True, side_effect=lambda path: str(path) in {"/usr/bin/codex", "/mnt/c/Windows/codex.exe"}), patch.object(owner.os, "access", return_value=True):
                self.assertNotIn("/mnt/c/", str(owner.codex_executable()))
                self.assertTrue(str(owner.codex_executable()).startswith("/usr/"))


if __name__ == "__main__":
    unittest.main()
