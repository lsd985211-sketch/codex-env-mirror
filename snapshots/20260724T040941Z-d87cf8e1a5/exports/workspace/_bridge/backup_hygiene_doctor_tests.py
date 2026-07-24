from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _bridge import backup_hygiene_doctor as hygiene


class WorkGitBackupHygieneTests(unittest.TestCase):
    def test_archive_root_is_outside_active_work_git(self) -> None:
        work_git_root = hygiene.ROOT.parent.resolve()
        archive_root = hygiene.ARCHIVE_ROOT.resolve()

        self.assertFalse(archive_root.is_relative_to(work_git_root))
        self.assertTrue(archive_root.is_relative_to(hygiene.WORK_GIT_BACKUP_ROOT.resolve()))

    def test_external_backup_root_is_scanned_for_manifests(self) -> None:
        self.assertIn(hygiene.WORK_GIT_BACKUP_ROOT, hygiene.PLANNED_BACKUP_ROOTS)

    def test_validate_fails_when_a_manifest_is_invalid(self) -> None:
        snapshot = {
            "ok": True,
            "summary": {"backup_count": 1, "manifest_count": 1, "manifest_failure_count": 1},
            "files": [],
        }

        result = hygiene.validate(snapshot)

        self.assertFalse(result["ok"])
        self.assertEqual(result["manifest_failure_count"], 1)

    def test_manifest_scan_excludes_manifest_copied_inside_backup_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_set = root / "set-1"
            payload_root = backup_set / "payload"
            nested_manifest = payload_root / "nested" / "manifest.json"
            nested_manifest.parent.mkdir(parents=True)
            nested_manifest.write_text('{"schema":"backup_router.manifest.v2","items":[]}', encoding="utf-8")
            control_manifest = backup_set / "manifest.json"
            control_manifest.write_text(
                json.dumps(
                    {
                        "schema": "backup_router.manifest.v2",
                        "items": [
                            {
                                "backup_mode": "external_copy",
                                "backup_set_dir": str(backup_set),
                                "backup_path": str(payload_root),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(hygiene, "PLANNED_BACKUP_ROOTS", (root,)):
                paths = hygiene.manifest_paths()

        self.assertEqual([control_manifest], paths)

    def test_manifest_scan_does_not_walk_arbitrary_payload_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_manifest = root / "202607" / "governance" / "set-1" / "manifest.json"
            control_manifest.parent.mkdir(parents=True)
            control_manifest.write_text('{"schema":"backup_router.manifest.v2","items":[]}', encoding="utf-8")
            deep_payload_manifest = root / "payload" / "a" / "b" / "c" / "d" / "e" / "manifest.json"
            deep_payload_manifest.parent.mkdir(parents=True)
            deep_payload_manifest.write_text('{"schema":"backup_router.manifest.v2","items":[]}', encoding="utf-8")

            with (
                patch.object(hygiene, "PLANNED_BACKUP_ROOTS", (root,)),
                patch.object(Path, "rglob", side_effect=AssertionError("manifest discovery must be depth-bounded")),
            ):
                paths = hygiene.manifest_paths()

        self.assertEqual([control_manifest], paths)


if __name__ == "__main__":
    unittest.main()
