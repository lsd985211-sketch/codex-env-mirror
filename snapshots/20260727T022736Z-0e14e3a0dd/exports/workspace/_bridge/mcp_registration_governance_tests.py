from __future__ import annotations

import unittest

from codex_baseline_update import mcp_specs
from codex_state_repair import remove_table_tree
from mcp_execution_priority import (
    DESKTOP_NATIVE_MCP_NAMES,
    HUB_MANAGED_MCP_NAMES,
    LAZY_NATIVE_MCP_NAMES,
    resolve_execution_priority,
)
from mcp_route_policy import call_priority_pack
from resource_process_doctor import resource_process_issues


class McpRegistrationGovernanceTests(unittest.TestCase):
    def test_hub_managed_profiles_have_zero_desktop_budget(self) -> None:
        for profile in HUB_MANAGED_MCP_NAMES:
            result = resolve_execution_priority(profile, "arbitrary_tool")
            self.assertEqual(result["execution_affinity"], "hub_first")
            self.assertEqual(result["registration_mode"], "hub_managed")
            self.assertEqual(result["desktop_instance_budget"], 0)
            self.assertEqual(result["lifecycle"], "fresh_stdio_per_call_exit")

    def test_desktop_native_profiles_remain_registered_by_contract(self) -> None:
        self.assertIn("node_repl", DESKTOP_NATIVE_MCP_NAMES)
        self.assertIn("gui-automation", DESKTOP_NATIVE_MCP_NAMES)
        self.assertIn("playwright", DESKTOP_NATIVE_MCP_NAMES)
        self.assertNotIn("filesystem", DESKTOP_NATIVE_MCP_NAMES)
        self.assertNotIn("filesystem-admin", DESKTOP_NATIVE_MCP_NAMES)

    def test_filesystem_admin_is_hub_first_for_reads_and_gateway_for_writes(self) -> None:
        self.assertIn("filesystem-admin", HUB_MANAGED_MCP_NAMES)
        read_pack = call_priority_pack("filesystem-admin", "read_text_file", "resource_acquisition")
        write_pack = call_priority_pack("filesystem-admin", "write_file", "resource_acquisition")
        self.assertEqual(read_pack["required_first_step"], "hub_mcp_direct")
        self.assertEqual(read_pack["preferred_direct_hub_tool"], "owner_mcp.call_readonly")
        self.assertEqual(write_pack["required_first_step"], "hub_mcp_gateway")
        self.assertEqual(resolve_execution_priority("filesystem-admin", "write_file")["desktop_instance_budget"], 0)

    def test_heavy_session_profiles_use_lazy_native_startup(self) -> None:
        self.assertEqual(
            LAZY_NATIVE_MCP_NAMES,
            {"chrome-devtools", "gui-automation", "next-ai-drawio", "playwright"},
        )
        for profile in LAZY_NATIVE_MCP_NAMES:
            result = resolve_execution_priority(profile, "arbitrary_tool")
            self.assertEqual(result["startup_mode"], "lazy_stdio_proxy")
            self.assertEqual(result["startup_child_budget"], 0)

    def test_remove_table_tree_removes_profile_and_nested_tables_only(self) -> None:
        text = """x = 1

[mcp_servers.filesystem]
command = 'fs'

[mcp_servers.filesystem.env]
A = '1'

[mcp_servers.\"node-repl\"]
command = 'node'
"""
        updated, changed = remove_table_tree(
            text,
            ("mcp_servers.filesystem", 'mcp_servers."filesystem"'),
        )
        self.assertTrue(changed)
        self.assertNotIn("mcp_servers.filesystem", updated)
        self.assertIn('mcp_servers."node-repl"', updated)

    def test_process_doctor_marks_pre_restart_hub_desktop_roots(self) -> None:
        issues = resource_process_issues(
            [
                {
                    "group": "context7_mcp",
                    "category": "docs_mcp_proxy",
                    "count": 1,
                    "root_instance_count": 1,
                    "working_set_mb": 18.0,
                    "host_root_counts": {"desktop_app_server": 1},
                    "effective_expected_max": 2,
                }
            ],
            {"state": "ok", "observations": []},
            {},
        )
        issue = next(item for item in issues if item.get("code") == "hub_managed_desktop_roots_pending_restart")
        self.assertEqual(issue["profiles"][0]["profile"], "context7")
        self.assertEqual(issue["profiles"][0]["desktop_instance_budget"], 0)

    def test_baseline_preserves_hub_specs_but_marks_them_nonblocking(self) -> None:
        existing = {
            "expected_mcp": {
                "filesystem": {"required": True, "command": "fs"},
                "node_repl": {"required": True, "command": "node"},
            }
        }
        config = {"mcp_servers": {"node_repl": {"required": True, "command": "node"}}}
        specs = mcp_specs(config, existing)
        self.assertEqual(specs["filesystem"]["registration_mode"], "hub_managed")
        self.assertFalse(specs["filesystem"]["required"])
        self.assertEqual(specs["node_repl"]["registration_mode"], "desktop_native")


if __name__ == "__main__":
    unittest.main()
