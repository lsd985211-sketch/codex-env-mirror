#!/usr/bin/env python3
"""Focused contract tests for managed Graphify and GitNexus Hub adapters."""

from __future__ import annotations

import unittest
from unittest import mock

import local_mcp_hub_graph_tools as graph_tools
import local_mcp_hub_catalog as hub_catalog


class GraphToolAdapterTests(unittest.TestCase):
    def test_rejects_working_directory_outside_work_git(self) -> None:
        payload = graph_tools.gitnexus_list_tools({"working_directory": "/tmp"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "working_directory_must_be_within_wsl_work_git")

    def test_rejects_graph_outside_managed_state(self) -> None:
        payload = graph_tools.graphify_list_tools({"graph_path": "/tmp/graph.json"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "graph_path_must_be_within_managed_graphify_state_root")

    def test_refuses_upstream_mutating_tool(self) -> None:
        with mock.patch.object(
            graph_tools,
            "_fresh_stdio_call",
            return_value={"ok": False, "reason": "mcp_tool_is_not_read_only", "tool": "rename"},
        ):
            payload = graph_tools.gitnexus_call({"tool": "rename"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "mcp_tool_is_not_read_only")
        self.assertEqual(payload["alias"], "gitnexus.call")

    def test_managed_tool_paths_are_absolute_and_pinned(self) -> None:
        status = graph_tools.validate()
        self.assertTrue(graph_tools.GITNEXUS_BIN.endswith("/gitnexus"))
        self.assertTrue(graph_tools.GRAPHIFY_BIN.endswith("/graphify"))
        self.assertIn("fresh_stdio_per_call_exit", status["lifecycle"])

    def test_graphify_allowlist_excludes_unknown_tools(self) -> None:
        self.assertIn("graph_stats", graph_tools.GRAPHIFY_READ_ONLY_TOOLS)
        self.assertNotIn("write_graph", graph_tools.GRAPHIFY_READ_ONLY_TOOLS)

    def test_graph_tools_are_default_hub_entries(self) -> None:
        for tool in ("gitnexus.list_tools", "gitnexus.call", "graphify.list_tools", "graphify.call"):
            self.assertTrue(hub_catalog.is_default_exposed(tool))


if __name__ == "__main__":
    unittest.main()
