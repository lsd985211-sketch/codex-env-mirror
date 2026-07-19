from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
