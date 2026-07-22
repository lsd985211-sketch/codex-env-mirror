from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_capability_routes as routes


class McpCapabilityRoutesTests(unittest.TestCase):
    def test_filesystem_read_route_is_hub_first_without_native_fallback(self) -> None:
        payload = routes.build(write=False)
        route = next(item for item in payload["routes"] if item.get("capability") == "local_filesystem_read")
        chain_ids = [step.get("id") for step in route.get("fallback_chain", [])]

        self.assertEqual(route["execution_affinity"], "hub_first")
        self.assertEqual(route["required_first_step"], "hub_mcp_direct")
        self.assertEqual(route["owner_profile"], "filesystem")
        self.assertEqual(route["direct_hub_tools"], ["owner_mcp.call_readonly"])
        advertised_tools = {
            tool
            for hint in route.get("direct_hub_hints", [])
            for tool in hint.get("typical_tools", [])
        }
        self.assertFalse({"write_file", "edit_file", "move_file", "create_directory"}.intersection(advertised_tools))
        self.assertNotIn("precise_tool_discovery", chain_ids)
        self.assertNotIn("native_mcp", chain_ids)

    def test_filesystem_lookup_resolves_read_route(self) -> None:
        with patch.object(routes, "OUT", Path("__missing_capability_route_index__.json")):
            result = routes.lookup(["filesystem", "filesystem-admin", "read_multiple_files"])
        self.assertTrue(result["matches"])
        self.assertEqual(result["matches"][0]["capability"], "local_filesystem_read")

    def test_code_graph_routes_are_hub_first_and_distinct(self) -> None:
        payload = routes.build(write=False)
        expected = {
            "gitnexus_semantic_graph": "gitnexus.call",
            "graphify_knowledge_graph": "graphify.call",
        }
        for capability, direct_tool in expected.items():
            route = next(item for item in payload["routes"] if item.get("capability") == capability)
            self.assertEqual(route["execution_affinity"], "hub_first")
            self.assertEqual(route["required_first_step"], "hub_mcp_direct")
            self.assertIn(direct_tool, route["direct_hub_tools"])

        with patch.object(routes, "OUT", Path("__missing_capability_route_index__.json")):
            self.assertEqual(routes.lookup(["gitnexus", "semantic code"])["matches"][0]["capability"], "gitnexus_semantic_graph")
            self.assertEqual(routes.lookup(["graphify", "review delta"])["matches"][0]["capability"], "graphify_knowledge_graph")

    def test_cache_identity_rejects_stale_route_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "routes.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "source_sha256": routes.matrix_hash(),
                        "route_definition_sha256": "stale",
                        "routes": [{"capability": "stale"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(routes, "OUT", cache_path):
                payload = routes.load_or_build()

        self.assertEqual(payload["route_definition_sha256"], routes.route_definition_hash())
        self.assertNotEqual(payload["routes"], [{"capability": "stale"}])

    def test_validator_has_real_checks(self) -> None:
        result = routes.validate()
        self.assertTrue(result["checks"])
        self.assertTrue(all("ok" in check for check in result["checks"]))
        self.assertTrue(result["ok"], result["issues"])

    def test_validator_fails_when_filesystem_route_is_removed(self) -> None:
        without_filesystem = [
            item for item in routes.MANUAL_ROUTES if item.get("capability") != "local_filesystem_read"
        ]
        with patch.object(routes, "MANUAL_ROUTES", without_filesystem):
            result = routes.validate()
        self.assertFalse(result["ok"])
        self.assertIn("filesystem_read_route_missing", {item.get("code") for item in result["issues"]})


if __name__ == "__main__":
    unittest.main()
