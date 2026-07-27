#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_configured_marketplace_manifest_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            source = home / ".tmp" / "marketplaces" / "open-code-review"
            config = {"marketplaces": {"open-code-review": {"source": str(source)}}}
            files = health.configured_marketplace_files(home, config)
            self.assertEqual(
                files["open-code-review"],
                source / ".agents" / "plugins" / "marketplace.json",
            )

    def test_explicit_config_path_owns_the_plugin_cache_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "windows-profile"
            profile.mkdir()
            config = profile / "config.toml"
            config.write_text(
                '[plugins."example@custom"]\nenabled = true\n',
                encoding="utf-8",
            )
            manifest = profile / "plugins" / "cache" / "custom" / "example" / "1.0.0" / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            with patch.object(health, "EXPECTED_PLUGINS", {}):
                result = health.codex_plugin_config_health(config, run_cli=False)
            self.assertEqual(result["codex_home"], str(profile))
            self.assertNotIn("example@custom", result["missing_cache_plugins"])
            self.assertNotIn("example@custom", result["missing_manifest_plugins"])

    def test_git_marketplace_and_claude_manifest_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            marketplace = profile / ".tmp" / "marketplaces" / "open-code-review"
            market_manifest = marketplace / ".claude-plugin" / "marketplace.json"
            market_manifest.parent.mkdir(parents=True)
            market_manifest.write_text("{}\n", encoding="utf-8")
            plugin_manifest = profile / "plugins" / "cache" / "open-code-review" / "open-code-review" / "1.0.0" / ".claude-plugin" / "plugin.json"
            plugin_manifest.parent.mkdir(parents=True)
            plugin_manifest.write_text("{}\n", encoding="utf-8")
            config = profile / "config.toml"
            config.write_text(
                '[marketplaces.open-code-review]\nsource_type = "git"\nsource = "https://github.com/alibaba/open-code-review.git"\n'
                '[plugins."open-code-review@open-code-review"]\nenabled = true\n',
                encoding="utf-8",
            )
            with patch.object(health, "EXPECTED_PLUGINS", {}):
                result = health.codex_plugin_config_health(config, run_cli=False)
            self.assertTrue(result["marketplaces"]["open-code-review"]["ok"])
            self.assertNotIn("open-code-review@open-code-review", result["missing_manifest_plugins"])

    def test_plan_install_rejects_existing_marketplace_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text("[plugins]\n", encoding="utf-8")
            with patch.object(health, "codex_cli_from_config", return_value="codex.exe"), patch.object(
                health, "_marketplace_rows", return_value={"ok": True, "marketplaces": ["open-code-review"]}
            ), patch.object(health, "codex_plugin_config_health", return_value={"expected_plugins": {}}):
                result = health.plan_install(
                    source="alibaba/open-code-review",
                    ref="v1.7.17",
                    plugin="open-code-review",
                    marketplace="open-code-review",
                    config_path=config,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "marketplace_name_already_exists")

    def test_install_requires_confirmation_before_lease_or_backup(self) -> None:
        plan = {
            "ok": True,
            "state": "ready_to_install",
            "config_path": "/tmp/config.toml",
            "cli": "codex.exe",
            "selector": "open-code-review@open-code-review",
        }
        with patch.object(health, "plan_install", return_value=plan), patch.object(
            health.state_write_authority, "try_acquire_state_write_lease"
        ) as acquire:
            result = health.install(
                source="alibaba/open-code-review",
                ref="v1.7.17",
                plugin="open-code-review",
                marketplace="open-code-review",
                confirm="",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "confirmation_required")
        acquire.assert_not_called()

    def test_remove_requires_confirmation_before_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            config = profile / "config.toml"
            config.write_text(
                '[plugins."sample@sample"]\nenabled = true\n', encoding="utf-8"
            )
            with patch.object(
                health.state_write_authority, "try_acquire_state_write_lease"
            ) as acquire:
                result = health.remove(
                    plugin="sample",
                    marketplace="sample",
                    confirm="",
                    config_path=config,
                )
            self.assertFalse(result["ok"])
            self.assertEqual("confirmation_required", result["reason"])
            acquire.assert_not_called()

    def test_install_uses_lease_backup_and_post_install_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text("[plugins]\n", encoding="utf-8")
            plan = {
                "ok": True,
                "state": "ready_to_install",
                "config_path": str(config),
                "cli": "codex.exe",
                "selector": "open-code-review@open-code-review",
            }
            lease = MagicMock(spec=["advance_generation", "assert_current", "release"])
            post_install = {
                "expected_plugins": {
                    "open-code-review@open-code-review": {
                        "enabled": True,
                        "cache_ok": True,
                        "manifest_ok": True,
                        "cli_ok": True,
                    }
                }
            }
            with patch.object(health, "plan_install", return_value=plan), patch.object(
                health.state_write_authority, "try_acquire_state_write_lease", return_value=lease
            ), patch.object(health, "create_backup", return_value={"ok": True, "backup_id": "test"}), patch.object(
                health, "_run_codex", return_value={"ok": True, "payload": {}}
            ) as run, patch.object(health, "codex_plugin_config_health", return_value=post_install), patch.object(
                health, "_marketplace_rows", return_value={"ok": True, "marketplaces": ["open-code-review"]}
            ):
                result = health.install(
                    source="alibaba/open-code-review",
                    ref="v1.7.17",
                    plugin="open-code-review",
                    marketplace="open-code-review",
                    confirm="INSTALL-CODEX-PLUGIN",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(run.call_count, 2)
            lease.advance_generation.assert_called_once()
            lease.release.assert_called_once()

    def test_remove_converges_stale_plugin_and_unreferenced_marketplace_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            config = profile / "config.toml"
            config.write_text(
                '[marketplaces.sample]\nsource_type = "git"\nsource = "https://example.invalid/sample.git"\n'
                '[plugins."sample@sample"]\nenabled = true\n',
                encoding="utf-8",
            )
            cache_root = profile / "plugins" / "cache" / "sample" / "sample" / "1.0.0"
            cache_root.mkdir(parents=True)
            lease = MagicMock(spec=["advance_generation", "assert_current", "release"])

            def run_cli(_cli: str, args: list[str], timeout: float = 60.0) -> dict[str, object]:
                del timeout
                if args[:2] == ["plugin", "remove"]:
                    shutil.rmtree(profile / "plugins" / "cache" / "sample" / "sample")
                return {"ok": True, "payload": {}}

            with patch.object(health, "codex_cli_from_config", return_value="codex.exe"), patch.object(
                health.state_write_authority, "try_acquire_state_write_lease", return_value=lease
            ), patch.object(health, "create_backup", return_value={"ok": True, "backup_id": "test"}), patch.object(
                health, "_run_codex", side_effect=run_cli
            ) as run:
                result = health.remove(
                    plugin="sample",
                    marketplace="sample",
                    confirm="REMOVE-CODEX-PLUGIN",
                    config_path=config,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["post_remove_absent"])
            self.assertTrue(result["post_remove_marketplace_absent_or_retained"])
            self.assertEqual(run.call_count, 2)
            payload = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("sample@sample", payload.get("plugins", {}))
            self.assertNotIn("sample", payload.get("marketplaces", {}))
            lease.advance_generation.assert_called_once()
            lease.release.assert_called_once()

    def test_remove_converges_orphaned_custom_marketplace_without_plugin_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            config = profile / "config.toml"
            config.write_text(
                '[marketplaces.sample]\nsource_type = "git"\nsource = "https://example.invalid/sample.git"\n',
                encoding="utf-8",
            )
            lease = MagicMock(spec=["advance_generation", "assert_current", "release"])
            with patch.object(health, "codex_cli_from_config", return_value="codex.exe"), patch.object(
                health.state_write_authority, "try_acquire_state_write_lease", return_value=lease
            ), patch.object(health, "create_backup", return_value={"ok": True, "backup_id": "test"}), patch.object(
                health, "_run_codex", return_value={"ok": True, "payload": {}}
            ) as run:
                result = health.remove(
                    plugin="sample",
                    marketplace="sample",
                    confirm="REMOVE-CODEX-PLUGIN",
                    config_path=config,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["operation"]["skipped"])
            self.assertEqual(run.call_args.args[1], ["plugin", "marketplace", "remove", "sample", "--json"])
            payload = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("sample", payload.get("marketplaces", {}))


if __name__ == "__main__":
    unittest.main()
