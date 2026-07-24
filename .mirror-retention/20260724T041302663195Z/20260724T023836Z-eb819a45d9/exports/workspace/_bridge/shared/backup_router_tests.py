from __future__ import annotations

import json
import shutil
import subprocess
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
        self.git_external_root = root / ".codex-app" / "backups" / "git-repositories"
        self.unified_root = root / ".codex-app" / "backups" / "unified"
        (self.git_root / ".git").mkdir(parents=True)
        self.bridge_root.mkdir(parents=True)
        self.patches = [
            mock.patch.object(backup_router, "PROJECT_ROOT", self.project_root),
            mock.patch.object(backup_router, "BRIDGE_ROOT", self.bridge_root),
            mock.patch.object(backup_router, "WORK_GIT_ROOT", self.git_root),
            mock.patch.object(backup_router, "WORK_GIT_BACKUP_ROOT", self.external_root),
            mock.patch.object(backup_router, "GIT_REPOSITORY_BACKUP_ROOT", self.git_external_root),
            mock.patch.object(backup_router, "UNIFIED_BACKUP_ROOT", self.unified_root),
            mock.patch.object(backup_router, "LEGACY_WORK_GIT_BACKUP_ROOT", self.legacy_root),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def initialize_git(self, root: Path, files: dict[str, str]) -> None:
        shutil.rmtree(root / ".git", ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Backup Router Tests"], check=True)
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "baseline"], check=True)

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

    def test_clean_tracked_file_uses_git_head_reference_without_duplicate_copy(self) -> None:
        self.initialize_git(self.git_root, {"workspace/_bridge/owner.py": "owner = True\n"})
        source = self.git_root / "workspace" / "_bridge" / "owner.py"

        planned = backup_router.plan([str(source)], category="owner", remark="git-reference")
        created = backup_router.create_backup([str(source)], category="owner", remark="git-reference")
        validated = backup_router.validate(str(self.external_root))

        item = planned["items"][0]
        self.assertTrue(planned["ok"])
        self.assertEqual("git_head_reference", item["backup_mode"])
        self.assertFalse(item["copy_required"])
        self.assertEqual("", item["backup_path"])
        self.assertEqual(0, created["copied_count"])
        self.assertEqual(1, created["git_reference_count"])
        self.assertTrue(validated["ok"])

    def test_dirty_tracked_file_keeps_exact_external_copy(self) -> None:
        self.initialize_git(self.git_root, {"workspace/_bridge/owner.py": "owner = True\n"})
        source = self.git_root / "workspace" / "_bridge" / "owner.py"
        source.write_text("owner = False\n", encoding="utf-8")

        result = backup_router.create_backup([str(source)], category="owner", remark="dirty-copy")

        item = result["items"][0]
        self.assertTrue(result["ok"])
        self.assertEqual("external_copy", item["backup_mode"])
        self.assertEqual(1, result["copied_count"])
        self.assertTrue(Path(item["backup_path"]).is_file())
        self.assertEqual(source.read_bytes(), Path(item["backup_path"]).read_bytes())

    def test_directory_backup_copies_once_and_validates_tree_digest(self) -> None:
        source = Path(self.temp.name) / "runtime-state"
        (source / "nested").mkdir(parents=True)
        (source / "state.sqlite").write_bytes(b"sqlite-state")
        (source / "nested" / "receipt.json").write_text('{"ok": true}\n', encoding="utf-8")

        created = backup_router.create_backup([str(source)], category="runtime", remark="directory")
        item = created["items"][0]
        validated = backup_router.validate_manifest(Path(created["manifest_paths"][0]))

        self.assertTrue(created["ok"])
        self.assertEqual("directory", item["source_kind"])
        self.assertEqual(2, item["file_count"])
        self.assertEqual(2, item["directory_count"])
        self.assertTrue(Path(item["backup_path"]).is_dir())
        self.assertEqual(item["source_sha256"], item["backup_sha256"])
        self.assertTrue(validated["ok"], validated)

    def test_directory_manifest_detects_changed_backup_file(self) -> None:
        source = Path(self.temp.name) / "runtime-state"
        source.mkdir()
        (source / "state.sqlite").write_bytes(b"sqlite-state")
        created = backup_router.create_backup([str(source)], category="runtime", remark="directory-drift")
        item = created["items"][0]
        (Path(item["backup_path"]) / "state.sqlite").write_bytes(b"changed")

        validated = backup_router.validate_manifest(Path(created["manifest_paths"][0]))

        self.assertFalse(validated["ok"])
        self.assertEqual("hash_mismatch", validated["issues"][0]["reason"])

    def test_staged_file_never_uses_head_reference_even_when_worktree_matches_head(self) -> None:
        self.initialize_git(self.git_root, {"workspace/_bridge/owner.py": "owner = True\n"})
        source = self.git_root / "workspace" / "_bridge" / "owner.py"
        source.write_text("owner = False\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.git_root), "add", "--", "workspace/_bridge/owner.py"], check=True)
        source.write_text("owner = True\n", encoding="utf-8")

        result = backup_router.create_backup([str(source)], category="owner", remark="staged-copy")

        self.assertTrue(result["ok"])
        self.assertEqual("external_copy", result["items"][0]["backup_mode"])
        self.assertEqual(source.read_bytes(), Path(result["items"][0]["backup_path"]).read_bytes())

    def test_repeated_backup_calls_use_distinct_manifest_sets(self) -> None:
        source = self.bridge_root / "owner.py"
        source.write_text("owner = True\n", encoding="utf-8")

        first = backup_router.create_backup([str(source)], category="owner", remark="same-operation")
        second = backup_router.create_backup([str(source)], category="owner", remark="same-operation")

        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["manifest_paths"], second["manifest_paths"])
        self.assertTrue(Path(first["manifest_paths"][0]).is_file())
        self.assertTrue(Path(second["manifest_paths"][0]).is_file())

    def test_malformed_manifest_fails_closed_without_raising(self) -> None:
        manifest = self.external_root / "malformed" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"schema": "backup_router.manifest.v2", "items": [None, {"backup_mode": "external_copy"}]}),
            encoding="utf-8",
        )

        result = backup_router.validate_manifest(manifest)

        self.assertFalse(result["ok"])
        self.assertEqual({"manifest_item_invalid", "backup_path_missing"}, {item["reason"] for item in result["issues"]})

    def test_windows_manifest_path_is_projected_before_validation(self) -> None:
        backup_file = Path(self.temp.name) / "host-backup.bin"
        backup_file.write_bytes(b"host-backup")
        manifest = self.external_root / "windows-path" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema": "backup_router.manifest.v2",
                    "items": [
                        {
                            "backup_mode": "external_copy",
                            "backup_path": r"C:\Backups\host-backup.bin",
                            "backup_sha256": backup_router.sha256_file(backup_file),
                            "source_kind": "file",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(backup_router, "host_accessible_path", return_value=backup_file) as project:
            result = backup_router.validate_manifest(manifest)

        self.assertTrue(result["ok"], result)
        project.assert_called_once_with(r"C:\Backups\host-backup.bin", platform_name=backup_router.os.name)

    def test_other_git_repository_ignores_internal_backup_directory(self) -> None:
        mirror = Path(self.temp.name) / "codex-env-mirror"
        self.initialize_git(mirror, {"manifests/policy.json": "{}\n"})
        (mirror / "_backup").mkdir()
        source = mirror / "manifests" / "policy.json"

        result = backup_router.plan([str(source)], category="control-plane", remark="mirror")

        item = result["items"][0]
        self.assertTrue(result["ok"])
        self.assertEqual("git_repository_external_backup_root", item["route"])
        self.assertTrue(backup_router.is_relative_to(Path(item["backup_dir"]), self.git_external_root))
        self.assertFalse(backup_router.is_relative_to(Path(item["backup_dir"]), mirror))

    def test_non_git_fallback_never_uses_work_git(self) -> None:
        source = Path(self.temp.name) / "runtime-state" / "state.sqlite"
        source.parent.mkdir()
        source.write_bytes(b"sqlite-state")

        result = backup_router.plan([str(source)], category="runtime", remark="runtime")

        item = result["items"][0]
        self.assertTrue(result["ok"])
        self.assertEqual("fallback_unified_backup_root", item["route"])
        self.assertTrue(backup_router.is_relative_to(Path(item["backup_dir"]), self.unified_root))
        self.assertFalse(backup_router.is_relative_to(Path(item["backup_dir"]), self.git_root))

    def test_destination_inside_git_worktree_fails_closed(self) -> None:
        source = self.bridge_root / "owner.py"
        source.write_text("owner = True\n", encoding="utf-8")
        with mock.patch.object(backup_router, "WORK_GIT_BACKUP_ROOT", self.git_root / "_backup"):
            result = backup_router.plan([str(source)], category="owner", remark="blocked")

        self.assertFalse(result["ok"])
        self.assertFalse(result["items"][0]["destination_policy_ok"])

    def test_relative_path_prefers_current_worktree_before_legacy_project_root(self) -> None:
        source = self.bridge_root / "owner.py"
        source.write_text("owner = True\n", encoding="utf-8")
        with mock.patch("pathlib.Path.cwd", return_value=self.git_root):
            result = backup_router.plan(["workspace/_bridge/owner.py"], category="owner", remark="cwd")

        self.assertTrue(result["ok"])
        self.assertEqual(source.resolve(), Path(result["items"][0]["source_path"]))

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

    def test_generic_git_repository_migration_rewrites_and_removes_internal_root(self) -> None:
        mirror = Path(self.temp.name) / "codex-env-mirror"
        self.initialize_git(mirror, {"README.md": "mirror\n"})
        backup_set = mirror / "_backup" / "202607" / "policy" / "set-1"
        backup_file = backup_set / "manifests" / "policy.json"
        backup_file.parent.mkdir(parents=True)
        backup_file.write_text("{}\n", encoding="utf-8")
        digest = backup_router.sha256_file(backup_file)
        manifest = {
            "schema": "backup_router.manifest.v1",
            "items": [{
                "source_path": str(mirror / "manifests" / "policy.json"),
                "backup_dir": str(backup_set.parent),
                "backup_set_dir": str(backup_set),
                "backup_path": str(backup_file),
                "backup_sha256": digest,
            }],
        }
        (backup_set / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        result = backup_router.migrate_git_repository_backups(str(mirror), apply=True)

        destination = Path(result["destination_root"])
        migrated_manifest = destination / "202607" / "policy" / "set-1" / "manifest.json"
        migrated = json.loads(migrated_manifest.read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["removed_source_root"])
        self.assertFalse((mirror / "_backup").exists())
        self.assertTrue(Path(migrated["items"][0]["backup_path"]).is_file())
        self.assertTrue(backup_router.is_relative_to(destination, self.git_external_root))


if __name__ == "__main__":
    unittest.main()
