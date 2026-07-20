#!/usr/bin/env python3
"""Regression tests for GitHub CLI command classification in the local Hub."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import sys


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import local_mcp_hub as hub_module  # noqa: E402


class LocalMcpHubGithubTests(unittest.TestCase):
    def test_auth_status_is_read_only_but_token_output_remains_blocked(self) -> None:
        hub = hub_module.LocalMcpHub()
        with patch.object(hub_module, "run_text_command", return_value={"ok": True, "returncode": 0}) as run:
            status = hub.github_gh({"args": ["auth", "status"]})

        self.assertTrue(status["ok"])
        run.assert_called_once_with(["gh", "auth", "status"], timeout=60, input_text="")
        blocked = hub.github_gh({"args": ["auth", "status", "--show-token"]})
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "github_token_printing_blocked")

    def test_auth_login_still_requires_write_acknowledgement(self) -> None:
        hub = hub_module.LocalMcpHub()
        result = hub.github_gh({"args": ["auth", "login"]})

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "write_ack_required")


if __name__ == "__main__":
    unittest.main()
