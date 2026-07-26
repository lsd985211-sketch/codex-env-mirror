from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import work_git_change_owner as owner


class WorkGitChangeOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bare = root / "store.git"
        self.main = root / "main"
        self.tasks = root / "tasks"
        self.receipts = root / "receipts"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", str(self.main)], check=True)
        self._git(self.main, "config", "user.email", "tests@example.invalid")
        self._git(self.main, "config", "user.name", "Work Git Tests")
        (self.main / "owned.txt").write_text("base\n", encoding="utf-8")
        (self.main / "foreign.txt").write_text("base\n", encoding="utf-8")
        self._git(self.main, "add", ".")
        self._git(self.main, "commit", "-q", "-m", "baseline")
        self._git(self.main, "branch", "-M", "main")
        self._git(self.main, "remote", "add", "origin", str(self.bare))
        self._git(self.main, "push", "-q", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def test_start_creates_clean_isolated_task_worktree(self) -> None:
        (self.main / "foreign.txt").write_text("dirty-main\n", encoding="utf-8")

        result = owner.start_task(
            "feature-123",
            confirm=owner.START_CONFIRM,
            root=self.main,
            task_root=self.tasks,
            receipt_root=self.receipts,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("codex/task/feature-123", result["after"]["branch"])
        self.assertTrue(result["after"]["clean"])
        self.assertEqual("dirty-main\n", (self.main / "foreign.txt").read_text(encoding="utf-8"))

    def test_commit_is_path_scoped_and_preserves_foreign_unstaged_changes(self) -> None:
        (self.main / "owned.txt").write_text("owned-change\n", encoding="utf-8")
        (self.main / "foreign.txt").write_text("foreign-change\n", encoding="utf-8")

        result = owner.commit_change_set(
            "feature-123",
            ["owned.txt"],
            message="Commit owned change",
            confirm=owner.COMMIT_CONFIRM,
            root=self.main,
            receipt_root=self.receipts,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("owned-change", self._git(self.main, "show", "HEAD:owned.txt"))
        self.assertEqual("base", self._git(self.main, "show", "HEAD:foreign.txt"))
        self.assertIn("foreign.txt", result["foreign_changes_preserved"])
        self.assertIn("foreign.txt", self._git(self.main, "status", "--short"))

    def test_commit_refuses_foreign_staged_changes(self) -> None:
        (self.main / "owned.txt").write_text("owned-change\n", encoding="utf-8")
        (self.main / "foreign.txt").write_text("foreign-change\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")

        result = owner.commit_plan("feature-123", ["owned.txt"], root=self.main, message="Commit owned")

        self.assertFalse(result["ok"])
        self.assertIn("foreign.txt", result["foreign_staged_paths"])
        self.assertIn("foreign_staged_changes", {item["code"] for item in result["blockers"]})

    def test_snapshot_collapses_untracked_directories_for_bounded_status(self) -> None:
        generated = self.main / "generated"
        generated.mkdir()
        for index in range(75):
            (generated / f"item-{index}.txt").write_text("generated\n", encoding="utf-8")

        result = owner.snapshot(self.main)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["change_count"])
        self.assertEqual("generated/", result["change_sample"][0]["path"])

    def test_integrate_fast_forwards_and_preserves_nonoverlapping_main_change(self) -> None:
        started = owner.start_task(
            "feature-123",
            confirm=owner.START_CONFIRM,
            root=self.main,
            task_root=self.tasks,
            receipt_root=self.receipts,
        )
        task = Path(started["plan"]["destination"])
        self._git(task, "config", "user.email", "tests@example.invalid")
        self._git(task, "config", "user.name", "Work Git Tests")
        (task / "owned.txt").write_text("task-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "feature-123",
            ["owned.txt"],
            message="Task change",
            confirm=owner.COMMIT_CONFIRM,
            root=task,
            receipt_root=self.receipts,
        )
        self.assertTrue(committed["ok"], committed)
        (self.main / "foreign.txt").write_text("main-dirty\n", encoding="utf-8")

        result = owner.integrate_task(
            "codex/task/feature-123",
            confirm=owner.INTEGRATE_CONFIRM,
            root=task,
            receipt_root=self.receipts,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("task-change\n", (self.main / "owned.txt").read_text(encoding="utf-8"))
        self.assertEqual("main-dirty\n", (self.main / "foreign.txt").read_text(encoding="utf-8"))
        self.assertEqual(self._git(self.main, "rev-parse", "HEAD"), self._git(self.bare, "rev-parse", "main"))

    def test_config_apply_sets_safe_repository_and_bare_guards(self) -> None:
        with patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}):
            result = owner.apply_config(
                confirm=owner.CONFIG_CONFIRM,
                root=self.main,
                receipt_root=self.receipts,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual("only", self._git(self.main, "config", "--local", "--get", "pull.ff"))
        self.assertEqual("true", self._git(self.bare, "config", "--local", "--get", "receive.denyNonFastForwards"))
        self.assertEqual("true", self._git(self.main, "config", "--local", "--get", "maintenance.commit-graph.enabled"))
        self.assertFalse(result["after"]["fsmonitor_enabled"])

    def test_maintenance_plan_uses_only_safe_local_git_tasks(self) -> None:
        with patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}):
            configured = owner.apply_config(confirm=owner.CONFIG_CONFIRM, root=self.main, receipt_root=self.receipts)
        self.assertTrue(configured["ok"], configured)
        plan = owner.maintenance_plan(self.main)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(["commit-graph", "loose-objects", "incremental-repack"], plan["tasks"])
        self.assertIn("no fetch", plan["scope"])


if __name__ == "__main__":
    unittest.main()
