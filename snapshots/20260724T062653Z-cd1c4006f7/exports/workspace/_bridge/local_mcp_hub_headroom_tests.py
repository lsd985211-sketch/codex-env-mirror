#!/usr/bin/env python3

from __future__ import annotations

import unittest
from unittest.mock import patch

import local_mcp_hub_headroom as adapter


class LocalMcpHubHeadroomTests(unittest.TestCase):
    def test_compress_routes_through_owner_command_and_fixed_allowlist(self) -> None:
        command = {"ok": True, "command": ["python", "owner.py", "serve"], "working_directory": "/tmp"}
        with patch.object(adapter.headroom_runtime, "command_spec", return_value=command), patch.object(
            adapter,
            "fresh_stdio_call",
            return_value={"ok": True, "result": {"content": [{"type": "text", "text": "{}"}]}},
        ) as call:
            result = adapter.call("headroom_compress", {"content": "large payload", "timeout_seconds": 20})
        self.assertTrue(result["ok"], result)
        self.assertEqual(call.call_args.kwargs["allowed_tools"], adapter.UPSTREAM_TOOLS)
        self.assertEqual(call.call_args.kwargs["arguments"], {"content": "large payload"})

    def test_rejects_unregistered_tool_and_oversized_content(self) -> None:
        self.assertEqual(adapter.call("memory_search", {})["reason"], "headroom_tool_not_allowlisted")
        result = adapter.call("headroom_compress", {"content": "x" * (adapter.MAX_CONTENT_CHARS + 1)})
        self.assertEqual(result["reason"], "content_exceeds_bounded_limit")

    def test_validate_preserves_memory_and_provider_boundaries(self) -> None:
        with patch.object(adapter.headroom_runtime, "validate", return_value={"ok": True}):
            result = adapter.validate()
        self.assertTrue(result["ok"])
        self.assertIn("PMB", result["memory_boundary"])
        self.assertFalse(result["provider_config_modified"])


if __name__ == "__main__":
    unittest.main()
