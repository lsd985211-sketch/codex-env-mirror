from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _bridge.shared import backup_router


class WorkGitBackupRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.git_root = root / "codex-workspace"
        self.project_root = self.git_root / "workspace"
        self.bridge_root = self.project_root / "_bridge"
        self.legacy_root = self.bridge_root / "backups"
        self.external_root = root / ".codex-app" / "backups" / "work-git"
        (self.git_root / ".git").mkdir(parents=True)
        self.bridge_root.mkdir(parents=True)
        self.patches = [
            mock.patch.object(backup_router, "PROJECT_ROOT", self.project_root),
            mock.patch.object(backup_router, "BRIDGE_ROOT", self.bridge_root),
            mock.patch.object(backup_router, "WORK_GIT_ROOT", self.git_root),
            mock.patch.object(backup_router, "WORK_GIT_BACKUP_ROOT", self.external_root),
            mock.patch.object(backup_router, "LEGACY_WORK_GIT_BACKUP_ROOT", self.legacy_root),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def test_work_git_source_routes_outside_repository(self) -> None:
        self.legacy_root.mkdir(parents=True)
        source = self.bridge_root / "owner.py"
        source.write_text("owner = True\n", encoding="utf-8")

        result = backup_router.plan([str(source)], category="owner", remark="test")

        item = result["items"][0]
        backup_path = Path(item["backup_path"]).resolve()
        self.assertTrue(result["ok"])
        self.assertEqual(item["route"], "wsl_work_git_external_backup_root")
        self.assertTrue(backup_router.is_relative_to(backup_path, self.external_root))
        self.assertFalse(backup_router.is_relative_to(backup_path, self.git_root))

    def test_create_and_validate_use_external_manifest(self) -> None:
        source = self.bridge_root / "owner.py"
        source.write_text("owner = True\n", encoding="utf-8")

        created = backup_router.create_backup([str(source)], category="owner", remark="test")
        validated = backup_router.validate(str(self.external_root))

        self.assertTrue(created["ok"])
        self.assertTrue(validated["ok"])
        self.assertEqual(validated["manifest_count"], 1)
        self.assertFalse(self.legacy_root.exists())

    def test_migration_rewrites_manifest_and_removes_legacy_tree_after_validation(self) -> None:
        backup_set = self.legacy_root / "manual" / "202607" / "owner" / "set-1"
        backup_file = backup_set / "_bridge" / "owner.py"
        backup_file.parent.mkdir(parents=True)
        backup_file.write_text("owner = True\n", encoding="utf-8")
        digest = backup_router.sha256_file(backup_file)
        manifest = {
            "schema": "backup_router.manifest.v1",
            "items": [
                {
                    "source_path": str(self.bridge_root / "owner.py"),
                    "backup_dir": str(backup_set.parent),
                    "backup_set_dir": str(backup_set),
                    "backup_path": str(backup_file),
                    "backup_sha256": digest,
                }
            ],
        }
        (backup_set / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        result = backup_router.migrate_work_git_backups(apply=True)

        destination = Path(result["destination_root"])
        migrated_manifest = destination / "manual" / "202607" / "owner" / "set-1" / "manifest.json"
        migrated = json.loads(migrated_manifest.read_text(encoding="utf-8"))
        migrated_item = migrated["items"][0]
        self.assertTrue(result["ok"])
        self.assertTrue(result["removed_source_root"])
        self.assertFalse(self.legacy_root.exists())
        self.assertTrue(Path(migrated_item["backup_path"]).exists())
        self.assertTrue(backup_router.is_relative_to(Path(migrated_item["backup_path"]), self.external_root))
        self.assertEqual(migrated_item["backup_sha256"], backup_router.sha256_file(Path(migrated_item["backup_path"])))

    def test_migration_refuses_unmanifested_legacy_files(self) -> None:
        unmanifested = self.legacy_root / "manual" / "orphan.py"
        unmanifested.parent.mkdir(parents=True)
        unmanifested.write_text("orphan = True\n", encoding="utf-8")

        result = backup_router.migrate_work_git_backups(apply=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "legacy_files_without_manifests")
        self.assertTrue(unmanifested.exists())
        self.assertFalse(self.external_root.exists())


if __name__ == "__main__":
    unittest.main()
