#!/usr/bin/env python3
"""Focused regressions for cross-platform Codex startup-baseline adoption."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_baseline_update as baseline_update  # noqa: E402


class CodexBaselineUpdateTests(unittest.TestCase):
    def test_run_audit_uses_current_wsl_python(self) -> None:
        with patch.object(baseline_update.subprocess, "run", return_value=type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()) as run:
            ok, output = baseline_update.run_audit()

        self.assertTrue(ok)
        self.assertEqual("ok", output)
        self.assertEqual(sys.executable, run.call_args.args[0][0])

    def test_wsl_active_baseline_adopts_only_current_mcp_plugins_and_marketplaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            global_config = root / "global.toml"
            baseline_path = root / "baseline.json"
            global_config.write_text(
                "sandbox_mode = \"danger-full-access\"\n"
                "[mcp_servers.node_repl]\ncommand = \"node\"\n"
                "[plugins.example]\nenabled = true\n"
                "[marketplaces.example]\nsource = \"/tmp/example\"\nsource_type = \"local\"\n",
                encoding="utf-8",
            )
            baseline_path.write_text(
                json.dumps(
                    {
                        "configuration_authority": "wsl_active",
                        "global_config": str(global_config),
                        "project_config": "unused",
                        "project_config_required": False,
                        "workspace": "/workspace",
                        "expected_mcp": {"legacy": {"required": False}},
                        "expected_plugins": ["legacy"],
                        "expected_marketplaces": {"legacy": {"source": "old"}},
                        "global_required_values": {"windows.sandbox": "elevated"},
                        "project_required_values": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(baseline_update, "BASELINE_PATH", baseline_path):
                updated, _diff = baseline_update.build_updated_baseline("test wsl active adoption")

            self.assertEqual({"node_repl"}, set(updated["expected_mcp"]))
            self.assertEqual(["example"], updated["expected_plugins"])
            self.assertEqual({"example"}, set(updated["expected_marketplaces"]))
            self.assertNotIn("windows.sandbox", updated["global_required_values"])

    def test_build_updated_baseline_resolves_host_paths_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            global_config = root / "global.toml"
            project_config = root / "project.toml"
            baseline_path = root / "baseline.json"
            global_config.write_text(
                'approval_policy = "never"\n[mcp_servers.node_repl]\ncommand = "node"\n',
                encoding="utf-8",
            )
            project_config.write_text('sandbox_mode = "danger-full-access"\n', encoding="utf-8")
            baseline_path.write_text(
                json.dumps(
                    {
                        "global_config": r"C:\Users\45543\.codex\config.toml",
                        "project_config": r"C:\workspace\.codex\config.toml",
                        "workspace": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace",
                        "expected_mcp": {},
                        "expected_plugins": [],
                        "global_required_values": {},
                        "project_required_values": {},
                    }
                ),
                encoding="utf-8",
            )

            resolved = {
                r"C:\Users\45543\.codex\config.toml": global_config,
                r"C:\workspace\.codex\config.toml": project_config,
            }
            with (
                patch.object(baseline_update, "BASELINE_PATH", baseline_path),
                patch.object(
                    baseline_update.platform_paths,
                    "host_accessible_path",
                    side_effect=lambda value: resolved[str(value)],
                ) as host_path,
            ):
                updated, _diff = baseline_update.build_updated_baseline("test host path resolution")

            self.assertEqual(host_path.call_count, 2)
            self.assertEqual(updated["global_required_values"]["approval_policy"], "never")
            self.assertEqual(updated["project_required_values"]["sandbox_mode"], "danger-full-access")
            self.assertTrue(updated["expected_mcp"]["node_repl"]["required"])


if __name__ == "__main__":
    unittest.main()
