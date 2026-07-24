#!/usr/bin/env python3
"""Focused tests for Windows PowerShell encoded-command transport."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from shared import windows_powershell
from shared.windows_powershell import (
    decode_encoded_command,
    encoded_command_arguments,
    powershell_encoded_command,
    powershell_file_command,
)


class WindowsPowerShellTests(unittest.TestCase):
    def test_encoded_command_round_trips_chinese_path_without_a_bom(self) -> None:
        script = "$path = 'C:\\Users\\45543\\Desktop\\Codex资源库\\图片'"
        args = encoded_command_arguments(script)
        self.assertEqual(["-NoProfile", "-NonInteractive", "-EncodedCommand"], args[:3])
        self.assertEqual(script, decode_encoded_command(args[3]))
        self.assertTrue(args[3].isascii())

    def test_empty_script_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encoded_command_arguments("")

    def test_wsl_resolution_does_not_depend_on_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "powershell.exe"
            executable.write_bytes(b"MZ")
            with patch.object(windows_powershell, "WINDOWS_POWERSHELL_WSL_PATH", executable), patch.object(
                windows_powershell.shutil, "which", return_value=None
            ):
                self.assertEqual(str(executable), windows_powershell.resolve_powershell_executable())

    def test_encoded_command_uses_resolved_executable_and_preserves_options(self) -> None:
        command = powershell_encoded_command(
            "Write-Output 'ok'",
            executable="/host/powershell.exe",
            execution_policy_bypass=True,
            window_style_hidden=True,
            no_logo=True,
        )
        self.assertEqual("/host/powershell.exe", command[0])
        self.assertIn("-ExecutionPolicy", command)
        self.assertIn("-WindowStyle", command)
        self.assertEqual("Write-Output 'ok'", decode_encoded_command(command[-1]))

    def test_file_command_keeps_script_and_arguments_after_file_switch(self) -> None:
        command = powershell_file_command(
            r"C:\Codex\restart.ps1",
            "-Mode",
            "dry-run",
            executable="/host/powershell.exe",
            execution_policy_bypass=True,
        )
        self.assertEqual("/host/powershell.exe", command[0])
        self.assertEqual([r"C:\Codex\restart.ps1", "-Mode", "dry-run"], command[command.index("-File") + 1 :])


if __name__ == "__main__":
    unittest.main()
