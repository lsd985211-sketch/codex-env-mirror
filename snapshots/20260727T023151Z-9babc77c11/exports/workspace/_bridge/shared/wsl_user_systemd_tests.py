#!/usr/bin/env python3
"""Focused regression tests for shared WSL user-systemd primitives."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wsl_user_systemd as owner


class WslUserSystemdTests(unittest.TestCase):
    def test_unit_path_value_is_absolute_unquoted_and_systemd_escaped(self) -> None:
        self.assertEqual(
            owner.unit_path_value(Path("/home/test/work space/50%")),
            r"/home/test/work\x20space/50%%",
        )
        with self.assertRaises(ValueError):
            owner.unit_path_value(Path("relative/path"))

    def test_unit_status_requires_file_and_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.service"
            path.write_text("[Service]\n", encoding="utf-8")
            results = [
                {"ok": True, "stdout": "LoadState=loaded\nActiveState=active\nSubState=running\nExecMainPID=42\n"},
                {"ok": True, "stdout": "enabled"},
                {"ok": True, "stdout": "active"},
            ]
            with patch.object(owner, "systemctl", side_effect=results):
                status = owner.unit_status("demo.service", path)
        self.assertTrue(status["ok"])
        self.assertEqual(status["systemd"]["ExecMainPID"], "42")

    def test_install_is_atomic_and_scoped_to_declared_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.service"
            with patch.object(owner, "systemctl", return_value={"ok": True, "stdout": ""}) as systemctl:
                result = owner.install_user_unit(
                    service_name="demo.service",
                    path=path,
                    content="[Service]\nExecStart=/bin/true\n",
                    backup_category="test",
                    backup_purpose="test-install",
                    backup_remark="test-unit",
                    backup_trigger="test",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(path.read_text(encoding="utf-8"), "[Service]\nExecStart=/bin/true\n")
            self.assertEqual(systemctl.call_args_list[0].args, ("daemon-reload",))
            self.assertEqual(systemctl.call_args_list[1].args[:3], ("enable", "--now", "demo.service"))

    def test_existing_unit_backup_failure_blocks_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.service"
            path.write_text("old\n", encoding="utf-8")
            with patch.object(owner, "create_backup", return_value={"ok": False}), patch.object(
                owner, "systemctl"
            ) as systemctl:
                result = owner.install_user_unit(
                    service_name="demo.service",
                    path=path,
                    content="new\n",
                    backup_category="test",
                    backup_purpose="test-install",
                    backup_remark="test-unit",
                    backup_trigger="test",
                )
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            systemctl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
