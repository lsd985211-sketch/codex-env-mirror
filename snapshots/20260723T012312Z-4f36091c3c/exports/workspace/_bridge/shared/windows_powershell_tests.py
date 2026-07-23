#!/usr/bin/env python3
"""Focused tests for Windows PowerShell encoded-command transport."""

from __future__ import annotations

import unittest

from shared.windows_powershell import decode_encoded_command, encoded_command_arguments


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


if __name__ == "__main__":
    unittest.main()
