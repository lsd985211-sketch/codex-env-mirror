#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_plugin_config_health as health


class CodexPluginConfigHealthTests(unittest.TestCase):
    def test_all_enabled_plugins_are_checked_even_when_not_in_static_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config = home / "config.toml"
            config.write_text(
                '[plugins."browser@openai-bundled"]\nenabled = true\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch.object(health, "EXPECTED_PLUGINS", {}):
                result = health.codex_plugin_config_health(config, run_cli=False)

            self.assertIn("browser@openai-bundled", result["missing_cache_plugins"])
            self.assertIn("browser@openai-bundled", result["missing_manifest_plugins"])
            self.assertTrue(result["expected_plugins"]["browser@openai-bundled"]["configured_discovery"])

    def test_enabled_plugin_identity_requires_marketplace_suffix(self) -> None:
        configured, invalid = health.configured_enabled_plugins(
            {"plugins": {"browser": {"enabled": True}}}
        )
        self.assertEqual(configured, {})
        self.assertEqual(invalid, ["browser"])


if __name__ == "__main__":
    unittest.main()
