#!/usr/bin/env python3
"""Regression tests for shared owner execution-platform admission."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import platform_scope


class PlatformScopeTests(unittest.TestCase):
    def test_windows_is_windows_host(self) -> None:
        with patch.object(platform_scope.os, "name", "nt"):
            self.assertEqual(platform_scope.execution_platform_scope(), "windows_host")

    def test_wsl_environment_is_wsl(self) -> None:
        with patch.object(platform_scope.os, "name", "posix"), patch.dict(
            platform_scope.os.environ, {"WSL_DISTRO_NAME": "Codex-Wsl-Lab"}, clear=False
        ):
            self.assertEqual(platform_scope.execution_platform_scope(), "wsl")

    def test_scope_matching_is_explicit(self) -> None:
        self.assertTrue(platform_scope.platform_scope_matches("all", "wsl"))
        self.assertTrue(platform_scope.platform_scope_matches("windows_host", "windows_host"))
        self.assertFalse(platform_scope.platform_scope_matches("windows_host", "wsl"))


if __name__ == "__main__":
    unittest.main()
