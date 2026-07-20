#!/usr/bin/env python3

import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import codex_environment_mirror as mirror


class CodexEnvironmentMirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release_gate = patch.object(
            mirror,
            "work_git_release_gate",
            return_value={
                "schema": "codex_environment_mirror.work_git_release_gate.v1",
                "ok": True,
                "source_mode": "work_git_primary",
                "work_git": {"release_ready": True, "worktree_head": "abc", "bare_head": "abc"},
                "issues": [],
            },
        )
        self.release_gate.start()
        self.addCleanup(self.release_gate.stop)

    @staticmethod
    def write_latest(root: Path, snapshot_id: str) -> bytes:
        payload = (json.dumps({"schema": "codex_mirror.latest.v1", "snapshot_id": snapshot_id}) + "\n").encode("utf-8")
        latest = root / "snapshots" / "latest.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_bytes(payload)
        return payload

    def test_refresh_requires_explicit_confirmation(self) -> None:
        payload = mirror.refresh("")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_confirmation"], mirror.REFRESH_CONFIRMATION)

    def test_refresh_blocks_when_work_git_release_is_not_ready(self) -> None:
        blocked = {
            "schema": "codex_environment_mirror.work_git_release_gate.v1",
            "ok": False,
            "issues": [{"code": "work_git_release_not_ready"}],
        }
        with patch.object(mirror, "work_git_release_gate", return_value=blocked), patch.object(mirror, "run_mirror") as owner:
            payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "work_git_release_gate")
        self.assertEqual(payload["source_authority"], blocked)
        owner.assert_not_called()

    def test_work_git_release_gate_requires_matching_primary_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured_worktree = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"
            owner_worktree = "/home/codexlab/work/codex-workspace"
            manifest = root / "manifests" / "source-authorities.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({
                    "workspace_authority": {
                        "mode": "work_git_primary",
                        "native_workspace_role": "transition_source_only",
                        "mirror_reverse_overwrite": False,
                    },
                    "variables": {
                        "WORK_GIT_ROOT": configured_worktree,
                        "WORKSPACE_ROOT": r"${WORK_GIT_ROOT}\workspace",
                    },
                    "generated_sources": [{"id": mirror.WORK_GIT_RELEASE_SOURCE_ID}],
                }),
                encoding="utf-8",
            )
            receipt = {
                "ok": True,
                "schema": "wsl_workspace_owner.v1.mirror_export.work_git_release.v1",
                "work_git": {
                    "release_ready": True,
                    "worktree": owner_worktree,
                    "bare_repo": r"C:\WSL\Codex-Wsl-Lab\git\codex-workspace.git",
                    "branch": "main",
                    "worktree_head": "abc",
                    "bare_head": "abc",
                    "wsl_user": "codexlab",
                    "issues": [],
                },
            }
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), patch.object(mirror, "run_json", return_value=receipt):
                payload = mirror.work_git_release_gate()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["work_git"]["worktree_head"], "abc")

    def test_normalized_path_accepts_both_wsl_unc_forms(self) -> None:
        linux = "/home/codexlab/work/codex-workspace/workspace"
        self.assertEqual(
            mirror._normalized_path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace"),
            mirror._normalized_path(linux),
        )
        self.assertEqual(
            mirror._normalized_path(r"\\wsl$\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace"),
            mirror._normalized_path(linux),
        )
        self.assertNotEqual(
            mirror._normalized_path(r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\other"),
            mirror._normalized_path(linux),
        )

    def test_affected_source_plan_translates_work_git_wsl_path_for_windows_owner(self) -> None:
        work_git_path = str(mirror.WORK_GIT_ROOT / "workspace" / "_bridge" / "codex_environment_mirror.py")
        expected = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace\_bridge\codex_environment_mirror.py"
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), \
                patch.object(mirror, "source_authority_variables", return_value={"WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"}), \
                patch.object(mirror, "run_mirror", return_value={"ok": True}) as run:
            payload = mirror.affected_source_plan([work_git_path])

        self.assertTrue(payload["ok"])
        run.assert_called_once_with(["affected-source-plan", "--changed", expected], timeout=180)

    def test_affected_source_plan_translates_work_git_relative_path_for_windows_owner(self) -> None:
        expected = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace\_bridge\codex_environment_mirror.py"
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), \
                patch.object(mirror, "source_authority_variables", return_value={"WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"}), \
                patch.object(mirror, "run_mirror", return_value={"ok": True}) as run:
            payload = mirror.affected_source_plan(["workspace/_bridge/codex_environment_mirror.py"])

        self.assertTrue(payload["ok"])
        run.assert_called_once_with(["affected-source-plan", "--changed", expected], timeout=180)

    def test_refresh_normalizes_explicit_work_git_relative_paths_for_windows_owner(self) -> None:
        expected = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace\_bridge\codex_environment_mirror.py"
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), \
                patch.object(mirror, "source_authority_variables", return_value={"WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"}), \
                patch.object(mirror, "_refresh_unlocked", return_value={"ok": False}) as refresh:
            payload = mirror.refresh("", ["workspace/_bridge/codex_environment_mirror.py"])

        self.assertFalse(payload["ok"])
        refresh.assert_called_once_with("", [expected])

    def test_commit_pathspecs_are_limited_to_current_snapshot_and_retention(self) -> None:
        capture = mirror.capture_commit_pathspecs("new-snapshot")
        self.assertIn("snapshots/new-snapshot", capture)
        self.assertIn("snapshots/latest.json", capture)
        self.assertNotIn("snapshots", capture)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            quarantine = root / ".mirror-retention" / "run"
            with patch.object(mirror, "mirror_root", return_value=root):
                retention = mirror.retention_commit_pathspecs(["old-snapshot"], quarantine)

        self.assertEqual(retention, ["snapshots/old-snapshot", ".mirror-retention/run/old-snapshot"])

    def test_expand_manifest_value_resolves_indirect_variables(self) -> None:
        variables = {
            "ROOT": r"C:\work",
            "NESTED": r"${ROOT}\workspace",
        }
        expanded = {
            key: mirror._expand_manifest_value(value, variables)
            for key, value in variables.items()
        }
        self.assertEqual(
            mirror._expand_manifest_value(r"${NESTED}\_bridge", expanded),
            r"C:\work\workspace\_bridge",
        )

    def test_publish_requires_explicit_confirmation(self) -> None:
        payload = mirror.publish("")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_confirmation"], mirror.PUBLISH_CONFIRMATION)

    def test_release_requires_explicit_confirmation(self) -> None:
        payload = mirror.release("", tag="seed-v2.2.0")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_confirmation"], mirror.RELEASE_CONFIRMATION)

    def test_release_reuses_current_remote_milestone_before_live_validation(self) -> None:
        existing = {
            "schema": "codex_environment_mirror.release.v1",
            "ok": True,
            "phase": "already_released",
            "reason": "existing_release_matches_current_state",
            "tag": "seed-v2.2.0",
            "snapshot_id": "snapshot-1",
            "reused": True,
            "resumed": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ""}), \
                patch.object(mirror, "existing_release_for_current_state", return_value=existing) as current, \
                patch.object(mirror, "release_plan") as plan, \
                patch.object(mirror, "contract_review_plan") as review, \
                patch.object(mirror, "run_mirror") as validate, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.release(mirror.RELEASE_CONFIRMATION, tag="seed-v2.2.0")

        self.assertEqual(payload, existing)
        current.assert_called_once_with("seed-v2.2.0", remote="", branch="")
        plan.assert_not_called()
        review.assert_not_called()
        validate.assert_not_called()

    def test_existing_release_rejects_missing_manifest_attachment(self) -> None:
        tag = "seed-v2.2.0"
        head = "a" * 40

        def git(args: list[str], **_kwargs: object) -> dict[str, object]:
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": head}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://github.com/example/mirror.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"]:
                return {"ok": True, "stdout": head}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                return {"ok": True, "stdout": f"{head}\trefs/heads/main"}
            if args == ["ls-remote", "--tags", "origin", f"refs/tags/{tag}^{{}}"]:
                return {"ok": True, "stdout": f"{head}\trefs/tags/{tag}^{{}}"}
            self.fail(f"unexpected git command: {args}")

        validation = {
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {"mirror_valid": True, "capability_restore_ready": True},
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
        }
        with patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), \
                patch.object(mirror, "control_plane_status", return_value={"ok": True, "latest_milestone_tag": tag}), \
                patch.object(mirror, "control_plane_validation_receipt", return_value=(validation, 1.0)), \
                patch.object(mirror, "git_result", side_effect=git), \
                patch.object(mirror, "git_network_env_for_remote", return_value=({}, {"ok": True})), \
                patch.object(mirror, "gh_result", return_value={"ok": True, "stdout": json.dumps({"tagName": tag, "isDraft": False, "assets": []})}):
            payload = mirror.existing_release_for_current_state(tag)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "existing_release_not_current")

    def test_refresh_lock_rejects_concurrent_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock = root / "runtime" / "locks" / "refresh.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"pid": os.getpid(), "operation": "refresh", "token": "active"}), encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), patch.object(mirror, "_refresh_unlocked") as unlocked:
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["reason"], "mirror_operation_busy")
            unlocked.assert_not_called()

    def test_superseded_snapshot_quarantine_stays_inside_mirror_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "keep").mkdir(parents=True)
            (root / "snapshots" / "old").mkdir(parents=True)
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                removed, quarantine = mirror.quarantine_superseded_snapshots("keep")
            self.assertEqual(removed, ["old"])
            self.assertIsNotNone(quarantine)
            assert quarantine is not None
            self.assertEqual(quarantine.parent, root / ".mirror-retention")
            self.assertTrue((quarantine / "old").is_dir())

    def test_retention_commit_stages_snapshot_deletions_without_quarantine(self) -> None:
        calls: list[list[str]] = []

        def git_result(args: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(args)
            if args == ["diff", "--cached", "--quiet"]:
                return {"ok": True, "returncode": 0}
            return {"ok": True, "stdout": "abc123"}

        with patch.object(mirror, "git_result", side_effect=git_result):
            payload = mirror.commit_refresh("current", phase="retention")

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["committed"])
        self.assertEqual(calls[0], ["add", "-A", "--", "snapshots"])
        self.assertNotIn(["add", "-A"], calls)

    def test_retention_cleanup_removes_stale_root_and_commits_tracked_deletions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stale = root / ".mirror-retention" / "old" / "snapshot-manifest.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
                    patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ".mirror-retention/old/snapshot-manifest.json\n"}), \
                    patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": True, "head": "def456"}) as commit:
                payload = mirror.commit_retention_cleanup("current")

            self.assertTrue(payload["ok"])
            self.assertFalse((root / ".mirror-retention").exists())
            commit.assert_called_once_with("current", phase="retention-cleanup")

    def test_retention_cleanup_stages_only_retention_deletions(self) -> None:
        calls: list[list[str]] = []

        def git_result(args: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(args)
            if args == ["diff", "--cached", "--quiet"]:
                return {"ok": True, "returncode": 0}
            return {"ok": True, "stdout": "abc123"}

        with patch.object(mirror, "git_result", side_effect=git_result):
            payload = mirror.commit_refresh("current", phase="retention-cleanup")

        self.assertTrue(payload["ok"])
        self.assertEqual(calls[0], ["add", "-A", "--", ".mirror-retention"])

    def test_refresh_reuses_committed_snapshot_when_live_sources_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "current").mkdir(parents=True)
            current = self.write_latest(root, "current")
            validation = {
                "ok": True,
                "snapshot_id": "current",
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
                "source_freshness_checked": True,
                "source_freshness_ok": True,
                "issues": [],
                "advisories": {"required_archive_gaps": ["runtime-state"]},
            }
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(current, "current", [])), \
                    patch.object(mirror, "committed_latest_pointer", return_value=current), \
                    patch.object(mirror, "run_mirror", side_effect=[{"ok": True}, validation, validation]) as owner, \
                    patch.object(mirror, "write_control_plane_state", return_value={"ok": True, "changed": False}), \
                    patch.object(mirror, "git_result", return_value={"ok": True, "stdout": "abc123"}), \
                    patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False, "head": "abc123"}) as commit, \
                    patch.object(mirror, "commit_retention_cleanup", return_value={"ok": True, "committed": False, "head": "abc123"}) as cleanup:
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["reused"])
            self.assertEqual(payload["snapshot_id"], "current")
            self.assertFalse(payload["commit"]["committed"])
            self.assertTrue(mirror.reusable_validation_receipt(payload["validation"], "current"))
            self.assertEqual(owner.call_args_list[1].args[0], ["validate", "--live-sources", "--snapshot", "current", "--skip-control-plane"])
            self.assertEqual(owner.call_args_list[2].args[0], ["control-plane-validate", "--snapshot", "current"])
            commit.assert_called_once_with("current", phase="control-plane")
            cleanup.assert_called_once_with("current")

    def test_publish_reuses_refresh_validation_inside_publish_lock(self) -> None:
        validation = {
            "schema": "codex_environment_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
            },
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
            "advisories": {},
            "summary": {},
        }
        refresh_payload = {
            "ok": True,
            "snapshot_id": "snapshot-1",
            "validation": validation,
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "refresh", return_value=refresh_payload), \
                patch.object(mirror, "run_mirror") as validate, \
                patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False}), \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin"}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["validation_reused_from_refresh"])
        validate.assert_not_called()

    def test_publish_resumes_committed_snapshot_without_refresh(self) -> None:
        validation = {
            "schema": "codex_environment_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
            },
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
            "advisories": {},
        }
        committed = {"ok": True, "snapshot_id": "snapshot-1", "validation": validation}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value=committed), \
                patch.object(mirror, "refresh") as refresh_call, \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin", "head": "a" * 40}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["reason"], "committed_snapshot_reused_for_push")
        refresh_call.assert_not_called()

    def test_publish_reuses_current_committed_snapshot_even_with_changed_paths(self) -> None:
        validation = {
            "schema": "codex_environment_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {"mirror_valid": True, "capability_restore_ready": True},
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
            "advisories": {},
        }
        committed = {"ok": True, "snapshot_id": "snapshot-1", "validation": validation}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "publish_refresh_scope", return_value={"ok": True, "mode": "explicit_changed_paths", "changed_paths": ["workspace/_bridge/a.py"]}), \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value=committed), \
                patch.object(mirror, "refresh") as refresh_call, \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin", "head": "a" * 40}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(
                mirror.PUBLISH_CONFIRMATION,
                changed_paths=["workspace/_bridge/a.py"],
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["reason"], "committed_snapshot_reused_after_explicit_refresh")
        refresh_call.assert_not_called()

    def test_committed_snapshot_reuse_requires_current_work_git_head(self) -> None:
        validation = {
            "schema": "codex_environment_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
            },
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
        }
        source_authority = {"work_git": {"worktree_head": "new"}}
        with patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), \
                patch.object(mirror, "control_plane_validation_receipt", return_value=(validation, 1.0)), \
                patch.object(mirror, "snapshot_json_asset", return_value={"work_git": {"worktree_head": "old"}}), \
                patch.object(mirror, "git_result") as git:
            payload = mirror.reusable_committed_snapshot_for_publish(source_authority)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "snapshot_work_git_head_stale")
        git.assert_not_called()

    def test_work_git_changed_paths_derives_absolute_paths_from_snapshot_head(self) -> None:
        source_authority = {
            "work_git": {
                "worktree": "/work/codex-workspace",
                "worktree_head": "b" * 40,
            }
        }

        def git_at(root: str, args: list[str], *, timeout: int = 120) -> dict:
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return {"ok": True, "returncode": 0, "stdout": ""}
            if "diff" in args:
                return {"ok": True, "returncode": 0, "stdout": "workspace/_bridge/codex_environment_mirror.py\ncodex-home/skills/a/SKILL.md"}
            return {"ok": False, "returncode": 1, "stdout": ""}

        with patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), \
                patch.object(mirror, "snapshot_json_asset", return_value={"work_git": {"worktree_head": "a" * 40}}), \
                patch.object(mirror, "source_authority_variables", return_value={"WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"}), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(mirror, "git_result_at", side_effect=git_at):
            payload = mirror.work_git_changed_paths_since_latest(source_authority)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["changed_path_count"], 2)
        self.assertEqual(
            payload["changed_paths"][0],
            r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace\_bridge\codex_environment_mirror.py",
        )

    def test_publish_auto_changed_paths_uses_directed_refresh_when_plan_is_safe(self) -> None:
        source_authority = {
            "ok": True,
            "work_git": {
                "worktree": "/work/codex-workspace",
                "worktree_head": "b" * 40,
            },
        }
        refresh_payload = {
            "ok": True,
            "snapshot_id": "snapshot-2",
            "validation": {
                "ok": True,
                "snapshot_id": "snapshot-2",
                "readiness": {"mirror_valid": True, "capability_restore_ready": True},
                "source_freshness": {"checked": True, "ok": True},
                "issues": [],
            },
        }
        changed = ["/work/codex-workspace/workspace/_bridge/codex_environment_mirror.py"]
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "work_git_release_gate", return_value=source_authority), \
                patch.object(mirror, "work_git_changed_paths_since_latest", return_value={"ok": True, "changed_paths": changed}), \
                patch.object(mirror, "affected_source_plan", return_value={"ok": True, "full_rebuild_required": False}), \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "refresh", return_value=refresh_payload) as refresh_call, \
                patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False}), \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin"}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["refresh_scope"]["mode"], "auto_changed_paths")
        refresh_call.assert_called_once_with(mirror.REFRESH_CONFIRMATION, changed)

    def test_publish_auto_changed_paths_falls_back_to_full_when_plan_requires_it(self) -> None:
        source_authority = {
            "ok": True,
            "work_git": {
                "worktree": "/work/codex-workspace",
                "worktree_head": "b" * 40,
            },
        }
        refresh_payload = {
            "ok": True,
            "snapshot_id": "snapshot-2",
            "validation": {
                "ok": True,
                "snapshot_id": "snapshot-2",
                "readiness": {"mirror_valid": True, "capability_restore_ready": True},
                "source_freshness": {"checked": True, "ok": True},
                "issues": [],
            },
        }
        changed = ["/work/codex-workspace/workspace/AGENTS.md"]
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "work_git_release_gate", return_value=source_authority), \
                patch.object(mirror, "work_git_changed_paths_since_latest", return_value={"ok": True, "changed_paths": changed}), \
                patch.object(mirror, "affected_source_plan", return_value={"ok": False, "full_rebuild_required": True, "reasons": ["membership_scope_changed"]}), \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "refresh", return_value=refresh_payload) as refresh_call, \
                patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False}), \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin"}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["refresh_scope"]["mode"], "full")
        self.assertEqual(payload["refresh_scope"]["fallback_reason"], "affected_source_plan_requires_full_rebuild")
        refresh_call.assert_called_once_with(mirror.REFRESH_CONFIRMATION, [])

    def test_execute_publish_returns_bounded_receipt_with_artifact(self) -> None:
        full_payload = {
            "schema": "codex_environment_mirror.publish.v1",
            "ok": True,
            "generated_at": "2026-07-19T00:00:00+00:00",
            "snapshot_id": "snapshot-1",
            "readiness": {"mirror_valid": True, "capability_restore_ready": True},
            "source_freshness": {"checked": True, "ok": True},
            "refresh": {"large": ["x"] * 100},
            "push": {"ok": True, "remote": "origin", "head": "a" * 40},
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "publish", return_value=full_payload), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.execute("publish", confirm=mirror.PUBLISH_CONFIRMATION)
            self.assertTrue(Path(payload["receipt_artifact"]).is_file())
        self.assertEqual(payload["schema"], "codex_environment_mirror.publish.summary.v1")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertIn("receipt_artifact", payload)
        self.assertNotIn("refresh", payload)

    def test_publish_refreshes_validates_commits_metadata_and_pushes(self) -> None:
        refresh_payload = {
            "ok": True,
            "snapshot_id": "snapshot-1",
            "readiness": {"mirror_valid": True, "capability_restore_ready": True},
        }
        validation_payload = {
            "schema": "codex_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "mirror_valid": True,
            "capability_restore_ready": True,
            "full_state_restore_ready": False,
            "source_freshness_checked": True,
            "source_freshness_ok": True,
            "issues": [],
            "advisories": {"required_archive_gaps": ["runtime-state"]},
            "summary": {"capture_mode": "full"},
        }
        calls: list[str] = []

        def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None) -> dict:
            calls.append("git " + " ".join(args))
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://token@example.com/owner/repo.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": "a" * 40}
            if args == ["push", "origin", "HEAD:main"]:
                return {"ok": True, "returncode": 0, "stdout": "", "stderr_tail": "pushed"}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                return {"ok": True, "stdout": f"{'a' * 40}\trefs/heads/main"}
            return {"ok": False, "stdout": "", "stderr_tail": str(args)}

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "refresh", side_effect=lambda *args, **kwargs: calls.append("refresh") or refresh_payload), \
                patch.object(mirror, "run_mirror", side_effect=lambda *args, **kwargs: calls.append("validate") or validation_payload), \
                patch.object(mirror, "git_network_env_for_remote", return_value=({"HTTPS_PROXY": "http://127.0.0.1:7897"}, {"ok": True, "used": True, "route_mode": "probe_selected_proxy"})), \
                patch.object(mirror, "commit_refresh", side_effect=lambda *args, **kwargs: calls.append("metadata_commit") or {"ok": True, "committed": False, "head": "local"}), \
                patch.object(mirror, "git_result", side_effect=git), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["push"]["remote"], "origin")
        self.assertNotIn("token", payload["push"]["remote_url"])
        self.assertLess(calls.index("refresh"), calls.index("validate"))
        self.assertLess(calls.index("validate"), calls.index("metadata_commit"))
        self.assertLess(calls.index("metadata_commit"), calls.index("git push origin HEAD:main"))

    def test_push_blocks_dirty_worktree_before_remote_write(self) -> None:
        with patch.object(mirror, "git_result", return_value={"ok": True, "stdout": " M README.md"}) as git:
            payload = mirror.push_receipt()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "git_worktree_not_clean_before_push")
        git.assert_called_once_with(["status", "--short"])

    def test_stage_requires_target_and_confirmation(self) -> None:
        self.assertEqual(mirror.execute("stage")["reason"], "target_root_required")
        payload = mirror.execute("stage", target_root=r"C:\Restore")
        self.assertEqual(payload["reason"], "confirmation_required")

    def test_prune_keeps_only_selected_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshots = root / "snapshots"
            (snapshots / "old").mkdir(parents=True)
            (snapshots / "keep").mkdir()
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                removed = mirror.prune_superseded_snapshots("keep")
            self.assertEqual(removed, ["old"])
            self.assertTrue((snapshots / "keep").is_dir())
            self.assertFalse((snapshots / "old").exists())

    def test_refresh_retries_transient_source_drift_and_removes_failed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "previous").mkdir(parents=True)
            previous = self.write_latest(root, "previous")
            candidates = iter(("candidate-1", "candidate-2"))

            def owner(args: list[str], *, timeout: int = 300) -> dict:
                if args == ["plan"]:
                    return {"ok": True}
                if args == ["snapshot", "--apply"]:
                    candidate = next(candidates)
                    (root / "snapshots" / candidate).mkdir()
                    self.write_latest(root, candidate)
                    return {"ok": True, "snapshot_id": candidate}
                if "candidate-1" in args:
                    return {"ok": False, "issues": [{"code": "source_assets_changed"}]}
                if "--live-sources" in args:
                    return {
                        "ok": True,
                        "mirror_valid": True,
                        "capability_restore_ready": True,
                        "full_state_restore_ready": False,
                        "source_freshness_checked": True,
                        "source_freshness_ok": True,
                        "issues": [],
                    }
                return {"ok": True, "mirror_valid": True, "capability_restore_ready": True, "full_state_restore_ready": False, "issues": []}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(previous, "previous", [])), \
                    patch.object(mirror, "run_mirror", side_effect=owner), \
                    patch.object(mirror, "write_control_plane_state", return_value={"ok": True, "changed": True}), \
                    patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": True, "head": "abc"}), \
                    patch.object(mirror, "commit_retention_cleanup", return_value={"ok": True, "committed": False, "head": "abc"}), \
                    patch.object(mirror.time, "sleep"):
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["snapshot_id"], "candidate-2")
            self.assertFalse((root / "snapshots" / "candidate-1").exists())
            self.assertFalse((root / "snapshots" / "previous").exists())
            self.assertEqual(mirror.pointer_snapshot_id((root / "snapshots" / "latest.json").read_bytes()), "candidate-2")
            self.assertEqual(len(payload["attempts"]), 2)

    def test_refresh_retry_exhaustion_restores_previous_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "previous").mkdir(parents=True)
            previous = self.write_latest(root, "previous")
            counter = {"value": 0}

            def owner(args: list[str], *, timeout: int = 300) -> dict:
                if args == ["plan"]:
                    return {"ok": True}
                if args == ["snapshot", "--apply"]:
                    counter["value"] += 1
                    candidate = f"candidate-{counter['value']}"
                    (root / "snapshots" / candidate).mkdir()
                    self.write_latest(root, candidate)
                    return {"ok": True, "snapshot_id": candidate}
                return {"ok": False, "issues": [{"code": "generated_source_changed"}]}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(previous, "previous", [])), \
                    patch.object(mirror, "run_mirror", side_effect=owner), \
                    patch.object(mirror.time, "sleep"):
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertFalse(payload["ok"])
            self.assertEqual(counter["value"], mirror.REFRESH_MAX_ATTEMPTS)
            self.assertEqual(mirror.pointer_snapshot_id((root / "snapshots" / "latest.json").read_bytes()), "previous")
            self.assertEqual([path.name for path in (root / "snapshots").iterdir() if path.is_dir()], ["previous"])

    def test_refresh_nonretryable_failure_stops_after_one_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "previous").mkdir(parents=True)
            previous = self.write_latest(root, "previous")
            calls = {"snapshots": 0}

            def owner(args: list[str], *, timeout: int = 300) -> dict:
                if args == ["plan"]:
                    return {"ok": True}
                if args == ["snapshot", "--apply"]:
                    calls["snapshots"] += 1
                    (root / "snapshots" / "candidate").mkdir()
                    self.write_latest(root, "candidate")
                    return {"ok": True, "snapshot_id": "candidate"}
                return {"ok": False, "issues": [{"code": "secret_scan_failed"}]}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(previous, "previous", [])), \
                    patch.object(mirror, "run_mirror", side_effect=owner):
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertFalse(payload["ok"])
            self.assertEqual(calls["snapshots"], 1)
            self.assertEqual(payload["attempts"][0]["issue_codes"], ["secret_scan_failed"])
            self.assertFalse((root / "snapshots" / "candidate").exists())

    def test_status_reports_missing_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots").mkdir()
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                payload = mirror.status()
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["failure"]["reason"], "mirror_cli_missing")
            self.assertTrue(payload["failures"])
            self.assertTrue(payload["issues"])

    def test_mirror_root_discovers_windows_owner_when_wsl_default_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {}, clear=False):
            home = Path(temp_dir) / "home"
            windows_root = Path(temp_dir) / "windows-mirror"
            (windows_root / "scripts").mkdir(parents=True)
            (windows_root / "scripts" / "mirror_cli.py").write_text("# owner\n", encoding="utf-8")
            with patch.object(mirror.Path, "home", return_value=home), patch.object(
                mirror, "WINDOWS_MIRROR_ROOT", windows_root
            ), patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": ""}):
                self.assertEqual(windows_root, mirror.mirror_root())

    def test_windows_owner_command_includes_bundled_python_and_git_paths(self) -> None:
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), patch.object(
            mirror.shutil, "which", return_value="/usr/bin/powershell.exe"
        ):
            command = mirror._windows_owner_command(
                Path("/mnt/c/Users/45543/codex-env-mirror/scripts/mirror_cli.py"), ["validate"]
            )
        self.assertIsNotNone(command)
        argv, environment = command
        self.assertIn("python.exe", argv[0])
        self.assertIn(r"C:\Program Files\Git", environment["PATH"])
        self.assertIn(";", environment["PATH"])
        self.assertEqual(environment["CODEX_MIRROR_SOURCE_READ_ONLY"], "1")
        self.assertEqual(environment["CODEX_MIRROR_REVERSE_OVERWRITE_BLOCKED"], "1")

    def test_git_result_uses_windows_git_for_windows_mirror_root(self) -> None:
        observed: list[str] = []

        def fake_run(command: list[str], **kwargs: object):
            observed.extend(command)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), \
                patch.object(Path, "is_file", return_value=True), \
                patch("codex_environment_mirror.subprocess.run", side_effect=fake_run):
            payload = mirror.git_result(["status", "--short"])
        self.assertTrue(payload["ok"])
        self.assertEqual(observed[0], "/mnt/c/Program Files/Git/cmd/git.exe")
        self.assertEqual(observed[2], r"C:\Users\45543\codex-env-mirror")

    def test_run_mirror_marks_local_owner_source_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cli = root / "scripts" / "mirror_cli.py"
            cli.parent.mkdir(parents=True)
            cli.write_text("# owner\n", encoding="utf-8")
            observed: dict[str, str] = {}

            def fake_run(command: list[str], **kwargs: object):
                observed.update(kwargs.get("env") or {})

                class Result:
                    returncode = 0
                    stdout = '{"ok": true}'
                    stderr = ""

                return Result()

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
                    patch("codex_environment_mirror.subprocess.run", side_effect=fake_run):
                payload = mirror.run_mirror(["validate"])
        self.assertTrue(payload["ok"])
        self.assertEqual(observed["CODEX_MIRROR_SOURCE_READ_ONLY"], "1")
        self.assertEqual(observed["CODEX_MIRROR_REVERSE_OVERWRITE_BLOCKED"], "1")

    def test_status_preserves_owner_failure_reason_and_artifact_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "snapshots").mkdir()
            failure = {
                "ok": False,
                "schema": "codex_mirror.validate.v1",
                "phase": "validate",
                "reason": "source_assets_changed",
                "issues": [],
                "owner_result_artifact": str(root / "runtime" / "validate-failure.json"),
            }

            def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None) -> dict:
                if args == ["status", "--short"]:
                    return {"ok": True, "stdout": ""}
                if args == ["rev-parse", "--short", "HEAD"]:
                    return {"ok": True, "stdout": "abc123"}
                if args == ["remote"]:
                    return {"ok": True, "stdout": "origin"}
                return {"ok": False, "stdout": ""}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "run_mirror", return_value=failure), \
                    patch.object(mirror, "git_result", side_effect=git), \
                    patch.object(mirror, "control_plane_status", return_value={"ok": True, "snapshot_id": ""}):
                payload = mirror.status(force_fresh=True)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["failure"]["reason"], "source_assets_changed")
            self.assertEqual(payload["failure"]["phase"], "validate")
            self.assertEqual(payload["failure"]["artifact_ref"], str(root / "runtime" / "validate-failure.json"))

    def test_status_redacts_sensitive_failure_details(self) -> None:
        failure = {
            "ok": False,
            "reason": "owner_failed",
            "detail": "token=secret-value password=hunter2",
            "issues": [{"code": "bad", "token": "secret-value"}],
        }
        projected = mirror.failure_diagnostic(failure, action="validate", source="validation")
        self.assertNotIn("secret-value", json.dumps(projected))
        self.assertNotIn("hunter2", json.dumps(projected))


    def test_control_plane_snapshot_mismatch_has_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "manifests"
            manifests.mkdir(parents=True)
            (root / "snapshots").mkdir()
            self.write_latest(root, "expected")
            (manifests / "control-plane-state.json").write_text(
                json.dumps({"snapshot": {"snapshot_id": "observed"}, "milestone": {}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                payload = mirror.control_plane_status()
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["reason"], "control_plane_snapshot_mismatch")
            self.assertEqual(payload["expected_snapshot_id"], "expected")
            self.assertEqual(payload["observed_snapshot_id"], "observed")

    def test_doctor_preserves_status_failure(self) -> None:
        failure = {"ok": False, "reason": "source_assets_changed", "issues": []}
        with patch.object(mirror, "status", return_value={
            "ok": False,
            "failure": mirror.failure_diagnostic(failure, action="validate", source="validation"),
            "issues": [],
        }), patch("codex_environment_mirror.subprocess.run", return_value=None):
            payload = mirror.doctor()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failure"]["reason"], "source_assets_changed")
        self.assertTrue(payload["issues"])

    def test_status_reuses_recent_successful_live_source_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "snapshots" / "snapshot-1").mkdir(parents=True)
            self.write_latest(root, "snapshot-1")
            validation = {
                "schema": "codex_mirror.validate.v1",
                "ok": True,
                "snapshot_id": "snapshot-1",
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
                "source_freshness_checked": True,
                "source_freshness_ok": True,
                "issues": [],
                "advisories": {},
            }

            def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None) -> dict:
                if args == ["status", "--short"]:
                    return {"ok": True, "stdout": ""}
                if args == ["rev-parse", "--short", "HEAD"]:
                    return {"ok": True, "stdout": "abc123"}
                if args == ["remote"]:
                    return {"ok": True, "stdout": "origin"}
                return {"ok": False, "stdout": ""}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), patch.object(
                mirror, "run_mirror", return_value=validation
            ) as validate, patch.object(mirror, "git_result", side_effect=git), patch.object(
                mirror, "control_plane_status", return_value={"ok": True, "snapshot_id": "snapshot-1"}
            ):
                first = mirror.status()
                second = mirror.status()
            self.assertTrue(first["ok"])
            self.assertEqual(first["validation"]["state"], "fresh")
            self.assertEqual(second["validation"]["state"], "cached")
            self.assertEqual(validate.call_count, 1)

    def test_release_plan_recommends_minor_for_control_plane_change(self) -> None:
        def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None) -> dict:
            if args[:2] == ["tag", "--list"]:
                return {"ok": True, "stdout": "seed-v2.1.2"}
            if "diff" in args and "seed-v2.1.2..HEAD" in args:
                return {"ok": True, "stdout": "scripts/mirror_cli.py\nmanifests/control-plane-contract.json"}
            if "diff" in args or "ls-files" in args:
                return {"ok": True, "stdout": ""}
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            return {"ok": False, "stdout": ""}
        with patch.object(mirror, "git_result", side_effect=git):
            payload = mirror.release_plan()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["recommended_bump"], "minor")
        self.assertEqual(payload["recommended_tag"], "seed-v2.2.0")

    def test_contract_review_plan_requires_codex_review_for_capability_change(self) -> None:
        milestone = {
            "ok": True,
            "current_tag": "seed-v2.1.2",
            "recommended_tag": "seed-v2.2.0",
            "non_snapshot_changes": ["scripts/mirror_cli.py"],
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": temp_dir}), \
                patch.object(mirror, "release_plan", return_value=milestone), \
                patch.object(mirror, "control_plane_fingerprint", return_value="a" * 64):
            payload = mirror.contract_review_plan()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["review_current"])
        self.assertEqual(
            payload["required_review_files"],
            ["AGENTS.md", "README.md", "MIRROR_POLICY.md", "BOOTSTRAP.md", "RESTORE.md", "SECURITY.md"],
        )

    def test_restore_plan_is_bounded_and_writes_complete_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            actions = [
                {
                    "asset_id": f"asset-{index}",
                    "source": f"source-{index}",
                    "stage_target": f"target-{index}",
                    "expected_sha256": "a" * 64,
                }
                for index in range(20)
            ]
            owner = {
                "schema": "codex_mirror.restore_plan.v1",
                "ok": True,
                "snapshot_id": "snapshot-1",
                "target_root": r"C:\Restore",
                "action_count": len(actions),
                "actions": actions,
                "external_archive_gaps": ["runtime-state"],
                "rule": "stage only",
            }
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), patch.object(mirror, "run_mirror", return_value=owner):
                payload = mirror.execute("restore-plan", target_root=r"C:\Restore")
            self.assertEqual(payload["schema"], "codex_environment_mirror.restore_plan.v1")
            self.assertEqual(payload["action_count"], 20)
            self.assertEqual(len(payload["action_sample"]), mirror.INLINE_SAMPLE_LIMIT)
            self.assertNotIn("actions", payload)
            artifact = Path(payload["full_plan_artifact"])
            self.assertTrue(artifact.is_file())
            artifact_payload = mirror.json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(len(artifact_payload["actions"]), 20)

    def test_plan_and_validate_keep_adapter_schema(self) -> None:
        plan_owner = {
            "schema": "codex_mirror.plan.v1",
            "ok": True,
            "sources": [],
            "generated_sources": [],
            "summary": {"candidate_files": 0},
        }
        validate_owner = {
            "schema": "codex_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "mirror_valid": True,
            "capability_restore_ready": True,
            "full_state_restore_ready": False,
            "issues": [],
            "advisories": {},
            "summary": {},
        }
        with patch.object(mirror, "run_mirror", side_effect=[plan_owner, validate_owner]) as owner_call:
            plan = mirror.execute("plan")
            validation = mirror.execute("validate")
        self.assertEqual(plan["schema"], "codex_environment_mirror.plan.v1")
        self.assertEqual(plan["owner_schema"], "codex_mirror.plan.v1")
        self.assertEqual(validation["schema"], "codex_environment_mirror.validate.v1")
        self.assertTrue(validation["readiness"]["mirror_valid"])
        self.assertEqual(owner_call.call_args_list[1].args[0], ["validate", "--live-sources"])

    def test_stage_receipt_is_bounded_and_preserves_no_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = [{"asset_id": f"asset-{index}", "hash_verified": True} for index in range(12)]
            full_receipt = {
                "schema": "codex_mirror.stage_receipt.v1",
                "ok": True,
                "snapshot_id": "snapshot-1",
                "target_root": temp_dir,
                "asset_count": len(assets),
                "assets": assets,
                "hashes_verified": True,
                "external_archive_gaps": ["runtime-state"],
                "membership_guard": {
                    "source_owner_verified": True,
                    "membership_export_sanitized": True,
                    "excluded_asset_count": 2,
                    "sanitized_asset_count": 1,
                    "registration_conflict_count": 0,
                },
                "activation_performed": False,
            }
            receipt_path = Path(temp_dir) / "stage-receipt.json"
            receipt_path.write_text(mirror.json.dumps(full_receipt), encoding="utf-8")
            owner = {
                "schema": "codex_mirror.stage.v1",
                "ok": True,
                "receipt": str(receipt_path),
                "summary": {"asset_count": len(assets), "target_root": temp_dir},
            }
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                    patch.object(mirror, "run_mirror", return_value=owner), \
                    patch.object(mirror, "mcp_bundle_readiness", return_value={"ok": True, "capability_restore_ready": True}):
                payload = mirror.execute("stage", target_root=temp_dir, confirm=mirror.STAGE_CONFIRMATION)
            self.assertEqual(payload["schema"], "codex_environment_mirror.stage.v1")
            self.assertEqual(payload["receipt_schema"], "codex_mirror.stage_receipt.v1")
            self.assertEqual(payload["asset_count"], 12)
            self.assertEqual(len(payload["asset_sample"]), mirror.INLINE_SAMPLE_LIMIT)
            self.assertTrue(payload["hashes_verified"])
            self.assertTrue(payload["membership_guard"]["membership_export_sanitized"])
            self.assertFalse(payload["activation_performed"])
            self.assertTrue(Path(payload["full_receipt_artifact"]).is_file())

    def test_stage_receipt_blocks_when_mcp_implementation_restore_is_incomplete(self) -> None:
        owner = {"schema": "codex_mirror.stage.v1", "ok": True, "snapshot_id": "snapshot-1", "assets": [], "hashes_verified": True, "activation_performed": False}
        bundles = {"ok": True, "capability_restore_ready": False, "blocked_missing_bundle": ["gitnexus-linux-x64"], "next_action": "build required bundle"}
        with patch.object(mirror, "mcp_bundle_readiness", return_value=bundles):
            payload = mirror.stage_receipt(owner)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "mcp_bundle_restore_not_ready")
        self.assertEqual(payload["owner_result"]["mcp_bundle_readiness"], bundles)


if __name__ == "__main__":
    unittest.main()
