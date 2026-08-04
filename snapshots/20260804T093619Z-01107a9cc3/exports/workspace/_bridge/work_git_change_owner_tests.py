from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import work_git_change_owner as owner
import work_git_change_owner_replay as replay
import work_git_change_owner_process as process


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

    def _validation_receipt(self, name: str, payload: dict[str, object]) -> str:
        path = Path(self.temp.name) / f"{name}.json"
        path.write_text(
            json.dumps({"schema": "validation_owner.receipt.v1", "readback_ok": True, **payload}),
            encoding="utf-8",
        )
        return str(path)

    def test_lifecycle_gate_accepts_exact_one_time_challenge_permit(self) -> None:
        scope = process.lifecycle_scope(
            thread_id="thread-1", repository_id="repo-1", task_id="feature-123",
            branch="codex/task/feature-123", base_head="base-1", declared_paths=["owned.txt"],
        )
        snapshot = {
            "ok": True, "intent_type": "one_time", "scope": scope,
            "operation_id": "op-1", "workflow_semantic_hash": "workflow-1",
            "environment_snapshot": {"schema": "environment.v1"},
        }
        with patch.object(process.authorization, "permit_snapshot", return_value=snapshot), patch.object(
            process.authorization, "consume_permit", return_value={"ok": True, "reused": True}
        ), patch.object(
            process.authorization, "record_effect", return_value={"ok": True}
        ), patch.object(
            process.authorization, "operation_snapshot", return_value={"status": "effect_started", "details": {}}
        ):
            result = process.authorize_step(
                permit_ref="permit-1", operation_id="op-1", step="start", thread_id="thread-1",
                repository_id="repo-1", task_id="feature-123", branch="codex/task/feature-123",
                base_head="base-1", declared_paths=["owned.txt"], workflow_semantic_hash="workflow-1",
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual("start", result["step"])

    def test_lifecycle_gate_rejects_non_exact_intent_type(self) -> None:
        with patch.object(process.authorization, "permit_snapshot", return_value={"ok": True, "intent_type": "permanent_default"}):
            result = process.authorize_step(
                permit_ref="permit-1", operation_id="op-1", step="start", thread_id="thread-1",
                repository_id="repo-1", task_id="feature-123", branch="codex/task/feature-123",
                base_head="base-1", declared_paths=["owned.txt"], workflow_semantic_hash="workflow-1",
            )

        self.assertFalse(result["ok"])
        self.assertEqual("work_git_lifecycle_exact_intent_required", result["reason"])

    def test_lifecycle_gate_allows_first_commit_on_main_as_an_implicit_start(self) -> None:
        scope = process.lifecycle_scope(
            thread_id="thread-1", repository_id="repo-1", task_id="feature-123",
            branch="main", base_head="base-1", declared_paths=["owned.txt"],
        )
        snapshot = {
            "ok": True, "intent_type": "one_time", "scope": scope,
            "operation_id": "op-1", "workflow_semantic_hash": "workflow-1",
            "environment_snapshot": {"schema": "environment.v1"},
        }
        with patch.object(process.authorization, "permit_snapshot", return_value=snapshot), patch.object(
            process.authorization, "consume_permit", return_value={"ok": True, "reused": True}
        ), patch.object(
            process.authorization, "record_effect", return_value={"ok": True}
        ) as record_effect, patch.object(
            process.authorization, "operation_snapshot", return_value={"status": "effect_started", "details": {}}
        ):
            result = process.authorize_step(
                permit_ref="permit-1", operation_id="op-1", step="commit", thread_id="thread-1",
                repository_id="repo-1", task_id="feature-123", branch="main",
                base_head="base-1", declared_paths=["owned.txt"], workflow_semantic_hash="workflow-1",
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual("commit", result["step"])
        self.assertEqual("direct_main_commit", record_effect.call_args.kwargs["details"]["implicit_start"])

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

    def test_integrate_fast_forwards_when_dirty_overlap_is_byte_equivalent(self) -> None:
        started = owner.start_task(
            "feature-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        task = Path(started["plan"]["destination"])
        self._git(task, "config", "user.email", "tests@example.invalid")
        self._git(task, "config", "user.name", "Work Git Tests")
        (task / "owned.txt").write_text("task-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "feature-123", ["owned.txt"], message="Task change",
            confirm=owner.COMMIT_CONFIRM, root=task, receipt_root=self.receipts,
        )
        self.assertTrue(committed["ok"], committed)
        (self.main / "owned.txt").write_text("task-change\n", encoding="utf-8")

        plan = owner.integrate_plan("codex/task/feature-123", root=self.main)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(["owned.txt"], plan["equivalent_dirty_overlap"])
        result = owner.integrate_task(
            "codex/task/feature-123", confirm=owner.INTEGRATE_CONFIRM,
            root=task, receipt_root=self.receipts,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual("", self._git(self.main, "status", "--short"))

    def test_integrate_rejects_dirty_overlap_when_bytes_differ(self) -> None:
        started = owner.start_task(
            "feature-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        task = Path(started["plan"]["destination"])
        self._git(task, "config", "user.email", "tests@example.invalid")
        self._git(task, "config", "user.name", "Work Git Tests")
        (task / "owned.txt").write_text("task-change\n", encoding="utf-8")
        self.assertTrue(owner.commit_change_set(
            "feature-123", ["owned.txt"], message="Task change",
            confirm=owner.COMMIT_CONFIRM, root=task, receipt_root=self.receipts,
        )["ok"])
        (self.main / "owned.txt").write_text("different-main-change\n", encoding="utf-8")

        plan = owner.integrate_plan("codex/task/feature-123", root=self.main)
        self.assertFalse(plan["ok"], plan)
        self.assertEqual(["owned.txt"], plan["conflicting_dirty_overlap"])
        self.assertIn("task_changes_overlap_dirty_main", {row["code"] for row in plan["blockers"]})

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

    def test_successor_plan_accepts_nonoverlapping_main_advance_without_writes(self) -> None:
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
        (self.main / "foreign.txt").write_text("main-change\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent change")

        legacy = owner.integrate_plan("codex/task/feature-123", root=self.main)
        before_head = self._git(self.main, "rev-parse", "HEAD")
        before_receipts = sorted(path.name for path in self.receipts.glob("*"))
        first = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)
        second = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)

        self.assertFalse(legacy["ok"])
        self.assertIn("task_branch_rebase_required", {item["code"] for item in legacy["blockers"]})
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["eligible"])
        self.assertEqual(before_head, self._git(self.main, "rev-parse", "HEAD"))
        self.assertEqual(["owned.txt"], first["declared_paths"])
        self.assertEqual(["foreign.txt"], first["current_main_changed_paths"])
        self.assertEqual(["owned.txt"], first["predecessor_changed_paths"])
        self.assertEqual("request_current_head_r2_scope", first["next_action"])
        self.assertEqual("validation_owner_selection", first["required_validations"][0]["validator_id"])
        self.assertEqual(first["successor_signature"], second["successor_signature"])
        self.assertEqual(before_receipts, sorted(path.name for path in self.receipts.glob("*")))

        cli = subprocess.run(
            [
                "python3",
                str(Path(owner.__file__).resolve()),
                "successor-plan",
                "--predecessor",
                "codex/task/feature-123",
                "--declared",
                "owned.txt",
                "--root",
                str(self.main),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        cli_result = json.loads(cli.stdout)
        self.assertTrue(cli_result["ok"], cli_result)
        self.assertEqual(first["successor_signature"], cli_result["successor_signature"])

    def test_successor_replay_applies_exact_assessed_changeset_and_reads_back_index(self) -> None:
        predecessor = owner.start_task(
            "predecessor-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        predecessor_root = Path(predecessor["plan"]["destination"])
        self._git(predecessor_root, "config", "user.email", "tests@example.invalid")
        self._git(predecessor_root, "config", "user.name", "Work Git Tests")
        (predecessor_root / "owned.txt").write_text("validated-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "predecessor-123", ["owned.txt"], message="Validated predecessor",
            confirm=owner.COMMIT_CONFIRM, root=predecessor_root, receipt_root=self.receipts,
        )
        (self.main / "foreign.txt").write_text("concurrent-main\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent main")
        plan = owner.successor_plan(
            "codex/task/predecessor-123", ["owned.txt"], root=self.main,
        )
        successor = owner.start_task(
            "successor-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        successor_root = Path(successor["plan"]["destination"])

        result = replay.replay_changeset(
            task_root=successor_root, repository_root=self.main,
            expected_branch="codex/task/successor-123",
            expected_base_head=plan["current_main_head"],
            predecessor_commit=committed["commit"], old_base_head=plan["old_base_head"],
            declared_paths=["owned.txt"],
            expected_changeset_digest=plan["changeset_digest"],
            successor_signature=plan["successor_signature"],
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["readback_ok"])
        self.assertEqual(["owned.txt"], result["staged_paths"])
        self.assertEqual("validated-change", self._git(successor_root, "show", ":owned.txt"))
        self.assertEqual("base", self._git(successor_root, "show", "HEAD:owned.txt"))
        self.assertEqual("concurrent-main", self._git(successor_root, "show", "HEAD:foreign.txt"))

    def test_successor_replay_rejects_digest_or_head_drift_before_mutation(self) -> None:
        predecessor = owner.start_task(
            "predecessor-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        predecessor_root = Path(predecessor["plan"]["destination"])
        self._git(predecessor_root, "config", "user.email", "tests@example.invalid")
        self._git(predecessor_root, "config", "user.name", "Work Git Tests")
        (predecessor_root / "owned.txt").write_text("validated-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "predecessor-123", ["owned.txt"], message="Validated predecessor",
            confirm=owner.COMMIT_CONFIRM, root=predecessor_root, receipt_root=self.receipts,
        )
        (self.main / "foreign.txt").write_text("concurrent-main\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent main")
        plan = owner.successor_plan(
            "codex/task/predecessor-123", ["owned.txt"], root=self.main,
        )
        successor = owner.start_task(
            "successor-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        successor_root = Path(successor["plan"]["destination"])

        digest_drift = replay.replay_changeset(
            task_root=successor_root, repository_root=self.main,
            expected_branch="codex/task/successor-123",
            expected_base_head=plan["current_main_head"],
            predecessor_commit=committed["commit"], old_base_head=plan["old_base_head"],
            declared_paths=["owned.txt"], expected_changeset_digest="0" * 64,
            successor_signature=plan["successor_signature"],
        )
        head_drift = replay.replay_changeset(
            task_root=successor_root, repository_root=self.main,
            expected_branch="codex/task/successor-123", expected_base_head="f" * 40,
            predecessor_commit=committed["commit"], old_base_head=plan["old_base_head"],
            declared_paths=["owned.txt"], expected_changeset_digest=plan["changeset_digest"],
            successor_signature=plan["successor_signature"],
        )

        self.assertEqual("successor_replay_changeset_digest_changed", digest_drift["reason"])
        self.assertEqual("successor_replay_base_head_changed", head_drift["reason"])
        self.assertEqual("", self._git(successor_root, "status", "--short"))

    def test_successor_plan_rejects_declared_path_overlap(self) -> None:
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
        (self.main / "owned.txt").write_text("main-change\n", encoding="utf-8")
        self._git(self.main, "add", "owned.txt")
        self._git(self.main, "commit", "-q", "-m", "Conflicting change")

        result = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)

        self.assertFalse(result["ok"])
        self.assertFalse(result["eligible"])
        self.assertEqual("path_overlap", result["reason"])
        self.assertEqual(["owned.txt"], result["overlap_paths"])

    def test_successor_plan_rejects_dirty_or_incomplete_changeset(self) -> None:
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
        (task / "foreign.txt").write_text("task-extra\n", encoding="utf-8")
        self._git(task, "add", "owned.txt", "foreign.txt")
        self._git(task, "commit", "-q", "-m", "Task changes")

        incomplete = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)
        (self.main / "owned.txt").write_text("dirty-main\n", encoding="utf-8")
        dirty = owner.successor_plan("codex/task/feature-123", ["owned.txt", "foreign.txt"], root=self.main)

        self.assertFalse(incomplete["ok"])
        self.assertEqual("declared_changeset_mismatch", incomplete["reason"])
        self.assertFalse(dirty["ok"])
        self.assertEqual("dirty_state", dirty["reason"])

    def test_successor_plan_rejects_already_integrated_or_non_stale_predecessor(self) -> None:
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

        non_stale = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)
        self.assertFalse(non_stale["ok"])
        self.assertEqual("predecessor_not_stale", non_stale["reason"])

        self._git(self.main, "merge", "--ff-only", "codex/task/feature-123")
        integrated = owner.successor_plan("codex/task/feature-123", ["owned.txt"], root=self.main)
        self.assertFalse(integrated["ok"])
        self.assertEqual("predecessor_already_integrated", integrated["reason"])

    def test_successor_plan_rejects_nonoverlapping_directory_file_conflict_without_writes(self) -> None:
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
        (task / "generated").mkdir()
        (task / "generated" / "result.txt").write_text("task-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "feature-123",
            ["generated/result.txt"],
            message="Task nested change",
            confirm=owner.COMMIT_CONFIRM,
            root=task,
            receipt_root=self.receipts,
        )
        self.assertTrue(committed["ok"], committed)
        (self.main / "generated").write_text("main-file\n", encoding="utf-8")
        self._git(self.main, "add", "generated")
        self._git(self.main, "commit", "-q", "-m", "Directory file conflict")
        before_head = self._git(self.main, "rev-parse", "HEAD")
        before_status = self._git(self.main, "status", "--porcelain")

        result = owner.successor_plan("codex/task/feature-123", ["generated/result.txt"], root=self.main)

        self.assertFalse(result["ok"])
        self.assertEqual([], result["overlap_paths"])
        self.assertEqual("tree_shape_conflict", result["reason"])
        self.assertIn("tree_shape_conflict", {item["code"] for item in result["blockers"]})
        self.assertEqual(before_head, self._git(self.main, "rev-parse", "HEAD"))
        self.assertEqual(before_status, self._git(self.main, "status", "--porcelain"))

    def test_successor_plan_reuses_only_current_owner_accepted_validation_receipts(self) -> None:
        started = owner.start_task(
            "feature-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        task = Path(started["plan"]["destination"])
        self._git(task, "config", "user.email", "tests@example.invalid")
        self._git(task, "config", "user.name", "Work Git Tests")
        (task / "owned.txt").write_text("task-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "feature-123", ["owned.txt"], message="Task change",
            confirm=owner.COMMIT_CONFIRM, root=task, receipt_root=self.receipts,
        )
        predecessor_head = committed["commit"]
        (self.main / "foreign.txt").write_text("main-change\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent change")
        receipts = [
            self._validation_receipt("source-test", {
                "validator_id": "source-test", "owner_contract_version": "tests.v1",
                "input_signature": "source-sig", "accepted": True,
                "current": True, "validated_head": predecessor_head,
                "source_dependencies": ["owned.txt"], "depends_on": [],
            }),
            self._validation_receipt("policy-test", {
                "validator_id": "policy-test", "owner_contract_version": "policy.v1",
                "input_signature": "policy-sig", "accepted": True,
                "current": True, "validated_head": predecessor_head,
                "source_dependencies": ["policy/rules.json"], "depends_on": [],
            }),
        ]

        first = owner.successor_plan(
            "codex/task/feature-123", ["owned.txt"], validation_receipts=receipts, root=self.main
        )
        second = owner.successor_plan(
            "codex/task/feature-123", ["owned.txt"], validation_receipts=list(reversed(receipts)), root=self.main
        )

        self.assertTrue(first["ok"], first)
        self.assertEqual([], first["required_validations"])
        self.assertEqual(sorted(receipts), first["reusable_receipt_refs"])
        self.assertEqual(first["skipped_validations"], second["skipped_validations"])
        self.assertEqual(first["successor_signature"], second["successor_signature"])
        self.assertNotIn("validation_owner_selection", {row["validator_id"] for row in first["required_validations"]})

        cli = subprocess.run(
            [
                "python3", str(Path(owner.__file__).resolve()), "successor-plan",
                "--predecessor", "codex/task/feature-123", "--declared", "owned.txt",
                "--validation-receipt", receipts[0], "--validation-receipt", receipts[1],
                "--root", str(self.main),
            ],
            capture_output=True, text=True, check=True,
        )
        cli_result = json.loads(cli.stdout)
        self.assertEqual(first["successor_signature"], cli_result["successor_signature"])
        self.assertEqual(sorted(receipts), cli_result["reusable_receipt_refs"])

        changed_receipt = json.loads(Path(receipts[1]).read_text(encoding="utf-8"))
        changed_receipt["input_signature"] = "policy-sig-changed"
        Path(receipts[1]).write_text(json.dumps(changed_receipt), encoding="utf-8")
        changed = owner.successor_plan(
            "codex/task/feature-123", ["owned.txt"], validation_receipts=receipts, root=self.main
        )
        self.assertNotEqual(first["successor_signature"], changed["successor_signature"])

    def test_successor_plan_invalidates_changed_validation_dependency_and_downstream_only(self) -> None:
        started = owner.start_task(
            "feature-123", confirm=owner.START_CONFIRM, root=self.main,
            task_root=self.tasks, receipt_root=self.receipts,
        )
        task = Path(started["plan"]["destination"])
        self._git(task, "config", "user.email", "tests@example.invalid")
        self._git(task, "config", "user.name", "Work Git Tests")
        (task / "owned.txt").write_text("task-change\n", encoding="utf-8")
        committed = owner.commit_change_set(
            "feature-123", ["owned.txt"], message="Task change",
            confirm=owner.COMMIT_CONFIRM, root=task, receipt_root=self.receipts,
        )
        predecessor_head = committed["commit"]
        (self.main / "foreign.txt").write_text("main-change\n", encoding="utf-8")
        self._git(self.main, "add", "foreign.txt")
        self._git(self.main, "commit", "-q", "-m", "Concurrent change")

        def receipt(validator_id: str, source: str, *, depends_on: list[str] | None = None) -> str:
            return self._validation_receipt(validator_id, {
                "validator_id": validator_id, "owner_contract_version": "validator.v1",
                "input_signature": f"{validator_id}-sig", "accepted": True,
                "current": True, "validated_head": predecessor_head,
                "source_dependencies": [source], "depends_on": depends_on or [],
            })

        result = owner.successor_plan(
            "codex/task/feature-123", ["owned.txt"], root=self.main,
            validation_receipts=[
                receipt("foreign-validator", "foreign.txt"),
                receipt("downstream-validator", "owned.txt", depends_on=["foreign-validator"]),
                receipt("independent-validator", "owned.txt"),
            ],
        )

        reasons = {row["validator_id"]: row["reason"] for row in result["required_validations"]}
        self.assertEqual("validation_source_dependency_changed", reasons["foreign-validator"])
        self.assertEqual("validation_upstream_dependency_invalidated", reasons["downstream-validator"])
        self.assertNotIn("independent-validator", reasons)
        self.assertEqual(
            [str(Path(self.temp.name) / "independent-validator.json")],
            result["reusable_receipt_refs"],
        )

    def test_successor_plan_fails_closed_for_unverifiable_validation_projection(self) -> None:
        wrong_schema = self._validation_receipt("wrong-schema", {
            "schema": "validation_owner.receipt.v0",
            "validator_id": "wrong-schema", "owner_contract_version": "v1",
            "input_signature": "sig", "accepted": True,
            "current": True, "validated_head": "predecessor",
            "source_dependencies": ["owned.txt"],
        })
        missing_signature = self._validation_receipt(
            "missing-signature", {"validator_id": "missing-signature", "accepted": True, "current": True}
        )
        not_current = self._validation_receipt("not-current", {
                    "validator_id": "not-current", "owner_contract_version": "v1",
                    "input_signature": "sig", "accepted": True,
                    "current": False, "validated_head": "predecessor",
                    "source_dependencies": ["owned.txt"],
                })
        unaccepted = self._validation_receipt("unaccepted", {
                    "validator_id": "unaccepted", "owner_contract_version": "v1",
                    "input_signature": "sig", "accepted": False,
                    "current": True, "validated_head": "predecessor",
                    "source_dependencies": ["owned.txt"],
                })
        selection = owner._validation_receipt_selection(
            [wrong_schema, missing_signature, not_current, unaccepted, {"ref": "/missing/forged.json", "accepted": True}],
            predecessor_commit="predecessor", current_main_changed_paths=[],
        )

        reasons = {row["validator_id"]: row["reason"] for row in selection["required_validations"]}
        self.assertEqual("validation_receipt_schema_unverified", reasons["wrong-schema"])
        self.assertEqual("validation_owner_contract_version_missing", reasons["missing-signature"])
        self.assertEqual("validation_owner_readback_not_current", reasons["not-current"])
        self.assertEqual("validation_owner_not_accepted", reasons["unaccepted"])
        self.assertIn("validation_receipt_readback_unverified", set(reasons.values()))
        self.assertEqual([], selection["reusable_receipt_refs"])


if __name__ == "__main__":
    unittest.main()
