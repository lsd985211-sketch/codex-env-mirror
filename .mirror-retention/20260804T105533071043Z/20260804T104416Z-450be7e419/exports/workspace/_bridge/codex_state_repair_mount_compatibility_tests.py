from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import codex_state_repair_mount_compatibility as compatibility
except ModuleNotFoundError:
    from _bridge import codex_state_repair_mount_compatibility as compatibility


class CodexStateRepairMountCompatibilityTests(unittest.TestCase):
    def test_apply_creates_only_missing_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            config.write_text(
                '[marketplaces.openai-bundled]\nsource = "/mnt/c/Users/test/.codex/.tmp/bundled"\nsource_type = "local"\n',
                encoding="utf-8",
            )
            pairs = []
            for name in ("marketplace", "cache", "runtime"):
                target = root / "targets" / name
                target.mkdir(parents=True)
                pairs.append((root / "aliases" / name, target))

            def create_link(alias: Path, target: Path) -> None:
                alias.symlink_to(target, target_is_directory=True)

            result = compatibility.reconcile(
                config_path=config,
                apply=True,
                platform_name="nt",
                alias_pairs=pairs,
                create_junction=create_link,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["changed"])
            self.assertEqual("applied", result["status"])
            self.assertTrue(all(alias.resolve() == target.resolve() for alias, target in pairs))

    def test_existing_directory_conflict_blocks_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            config.write_text(
                '[marketplaces.openai-bundled]\nsource = "/mnt/c/Users/test/.codex/.tmp/bundled"\n',
                encoding="utf-8",
            )
            alias = root / "alias"
            alias.mkdir()
            target = root / "target"
            target.mkdir()
            called = False

            def create_link(_alias: Path, _target: Path) -> None:
                nonlocal called
                called = True

            result = compatibility.reconcile(
                config_path=config,
                apply=True,
                platform_name="nt",
                alias_pairs=[(alias, target)],
                create_junction=create_link,
            )

            self.assertFalse(result["ok"])
            self.assertEqual("blocked", result["status"])
            self.assertFalse(called)
            self.assertTrue(alias.is_dir())

    def test_non_windows_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                '[marketplaces.openai-bundled]\nsource = "/mnt/c/Users/test/.codex/.tmp/bundled"\n',
                encoding="utf-8",
            )
            result = compatibility.reconcile(config_path=config, apply=True, platform_name="posix")
            self.assertTrue(result["ok"])
            self.assertFalse(result["changed"])
            self.assertEqual("not_windows_host", result["status"])


if __name__ == "__main__":
    unittest.main()
