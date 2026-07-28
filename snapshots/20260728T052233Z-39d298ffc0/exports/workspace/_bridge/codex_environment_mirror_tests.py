#!/usr/bin/env python3

import os
import tempfile
import unittest
import json
import hashlib
from pathlib import Path
from unittest.mock import patch

import codex_environment_mirror as mirror
import codex_network_gateway as network_gateway


class CodexEnvironmentMirrorTests(unittest.TestCase):
    def test_finalize_and_publish_reuses_matching_terminal_receipt(self) -> None:
        source = {"ok": True, "work_git": {"worktree_head": "a", "bare_head": "a"}}
        result = {
            "ok": True,
            "source_authority": source,
            "readiness": {"mirror_valid": True, "capability_restore_ready": True},
            "source_freshness": {"ok": True},
            "push": {"remote_verification": {"ok": True, "remote_head": "b"}},
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                patch.object(
                    mirror,
                    "cached_publication_source_authority",
                    side_effect=[{"ok": False}, source],
                ), \
                patch.object(mirror, "work_git_release_gate") as release_gate, \
                patch.object(mirror, "publish", return_value=result) as publish:
            first = mirror.finalize_and_publish(mirror.PUBLISH_CONFIRMATION, changed_paths=["a.py"])
            second = mirror.finalize_and_publish(mirror.PUBLISH_CONFIRMATION, changed_paths=["a.py"])
        self.assertTrue(first["ok"], first)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["reused"])
        self.assertTrue(second["cache_hit"])
        publish.assert_called_once()
        release_gate.assert_not_called()

    def test_finalize_and_publish_rejects_incomplete_remote_acceptance(self) -> None:
        source = {"ok": True, "work_git": {"worktree_head": "a", "bare_head": "a"}}
        result = {"ok": True, "source_authority": source, "readiness": {"mirror_valid": True, "capability_restore_ready": True}, "source_freshness": {"ok": True}, "push": {}}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                patch.object(mirror, "cached_publication_source_authority", return_value={"ok": False}), \
                patch.object(mirror, "publish", return_value=result):
            payload = mirror.finalize_and_publish(mirror.PUBLISH_CONFIRMATION)
        self.assertFalse(payload["ok"])
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
        self.drift_gate = patch.object(
            mirror,
            "refresh_drift_gate",
            return_value={
                "schema": "codex_environment_mirror.refresh_drift_gate.v1",
                "ok": True,
                "refresh_allowed": True,
                "blockers": [],
            },
        )
        self.drift_gate.start()
        self.addCleanup(self.drift_gate.stop)
        self.state_write_gate = patch.object(
            mirror.state_write_authority,
            "pre_publish_gate",
            return_value={
                "schema": "state_write_authority.pre_publish_gate.v1",
                "ok": True,
                "stable": True,
                "work_git_bare_match": True,
            },
        )
        self.state_write_gate.start()
        self.addCleanup(self.state_write_gate.stop)
        self.publication_barrier = patch.object(
            mirror.state_write_authority,
            "acquire_publication_barrier",
            return_value=unittest.mock.Mock(writer_id="codex_environment_mirror", generation=1),
        )
        self.publication_barrier.start()
        self.addCleanup(self.publication_barrier.stop)

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

    def test_refresh_blocks_before_drift_or_capture_when_state_writers_are_unstable(self) -> None:
        blocked = {
            "schema": "state_write_authority.pre_publish_gate.v1",
            "ok": False,
            "reason": "state_changed_during_publish_preflight",
        }
        with patch.object(mirror.state_write_authority, "pre_publish_gate", return_value=blocked), patch.object(mirror, "run_mirror") as owner:
            payload = mirror._refresh_unlocked(mirror.REFRESH_CONFIRMATION)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "state_write_authority_gate")
        self.assertEqual(payload["state_write_gate"], blocked)
        owner.assert_not_called()

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

    def test_refresh_reports_unstable_sources_without_touching_current_snapshot(self) -> None:
        source_authority = {"ok": True, "work_git": {"worktree_head": "current"}}
        unstable = {
            "ok": False, "reason": "source_capture_not_quiescent",
            "candidate_created": False, "snapshot_id": "none",
        }
        with patch.object(mirror, "work_git_release_gate", return_value=source_authority), \
                patch.object(mirror, "stable_previous_pointer", return_value=({}, "current", [])), \
                patch.object(mirror, "read_control_plane_files", return_value={}), \
                patch.object(mirror, "run_mirror", side_effect=[{"ok": True}, unstable]), \
                patch.object(mirror, "remove_snapshot_candidate") as remove:
            payload = mirror._refresh_unlocked(mirror.REFRESH_CONFIRMATION, ["workspace/_bridge/codex_environment_mirror.py"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "source_quiescence")
        self.assertFalse(payload["candidate_created"])
        remove.assert_not_called()

    def test_capture_lease_is_removed_after_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": temp_dir}):
            path = Path(temp_dir) / "runtime" / mirror.CAPTURE_LEASE_NAME
            with mirror.mirror_capture_lease() as lease:
                self.assertTrue(path.is_file())
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], lease["token"])
            self.assertFalse(path.exists())

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

    def test_drift_plan_runs_live_validation_and_returns_owner_rows(self) -> None:
        validation = {
            "ok": False,
            "schema": "codex_mirror.validate.v1",
            "issues": [{"code": "source_assets_changed", "source_id": "codex-global-agents", "sample": ["codex-global-agents"]}],
        }
        routed = {
            "schema": "codex_environment_mirror.drift_plan.v1",
            "ok": True,
            "refresh_allowed": False,
            "rows": [{"item_id": "codex-global-agents"}],
            "blockers": [{"item_id": "codex-global-agents"}],
        }
        with patch.object(mirror, "run_mirror", return_value=validation) as owner, patch.object(
            mirror, "mirror_drift_evidence", return_value={"codex-global-agents": {}}
        ), patch.object(
            mirror, "build_drift_plan", return_value=routed
        ) as build, patch.object(
            mirror, "affected_source_plan", return_value={"ok": True}
        ):
            payload = mirror.drift_plan()

        self.assertEqual(payload["rows"], routed["rows"])
        self.assertIn("validation", payload)
        owner.assert_called_once_with(["validate", "--live-sources"], timeout=300)
        build.assert_called_once()

    def test_drift_evidence_recognizes_declared_work_git_root_across_linked_worktree(self) -> None:
        manifest = {
            "assets": [{
                "asset_id": "codex-global-agents",
                "source_path": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\codex-home\AGENTS.md",
                "restore_template": r"${CODEX_HOME}\AGENTS.md",
                "sha256": "b" * 64,
            }]
        }
        with patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), patch.object(
            mirror, "snapshot_json_asset", return_value=manifest
        ), patch.object(
            mirror, "source_authority_variables", return_value={
                "WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace",
                "CODEX_HOME": r"C:\Users\45543\.codex",
            }
        ), patch.object(
            mirror, "_optional_sha256", side_effect=["a" * 64, "c" * 64]
        ):
            evidence = mirror.mirror_drift_evidence([{
                "code": "source_assets_changed",
                "source_id": "codex-global-agents",
                "sample": ["codex-global-agents"],
            }])

        self.assertEqual(evidence["codex-global-agents"]["work_git_digest"], "a" * 64)
        self.assertEqual(evidence["codex-global-agents"]["current_projection_digest"], "c" * 64)

    def test_validation_source_signature_uses_declared_primary_work_git_root(self) -> None:
        declared_root = Path("/home/codexlab/work/codex-workspace")
        with patch.object(mirror, "mirror_root", return_value=Path("/mirror")), patch.object(
            mirror, "source_authority_variables", return_value={
                "WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"
            }
        ), patch.object(
            mirror, "_local_mirror_source_path", return_value=declared_root
        ), patch.object(
            Path, "is_file", return_value=False
        ), patch.object(
            mirror, "control_plane_fingerprint", return_value=""
        ), patch.object(
            mirror, "git_result_at", return_value={"ok": True, "stdout": "main-head"}
        ) as git:
            signature = mirror.validation_source_signature("snapshot-1")

        self.assertTrue(signature)
        git.assert_called_once_with(str(declared_root), ["rev-parse", "HEAD"])

    def test_refresh_blocks_unresolved_drift_before_owner_plan(self) -> None:
        blocked = {
            "schema": "codex_environment_mirror.refresh_drift_gate.v1",
            "ok": False,
            "refresh_allowed": False,
            "blockers": [{"item_id": "codex-native-memory-files:MEMORY.md"}],
            "plan_digest": "digest",
        }
        with patch.object(mirror, "refresh_drift_gate", return_value=blocked), patch.object(
            mirror, "run_mirror"
        ) as owner:
            payload = mirror._refresh_unlocked(mirror.REFRESH_CONFIRMATION)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "ambient_drift_gate")
        self.assertEqual(payload["drift_plan_ref"], "digest")
        owner.assert_not_called()

    def test_refresh_reuses_matching_transaction_preflight_without_duplicate_gate_or_plan(self) -> None:
        changed = ["workspace/_bridge/codex_environment_mirror.py"]
        source_authority = {
            "ok": True,
            "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40},
        }
        preflight = mirror._refresh_preflight_receipt(
            changed,
            scope={"ok": True, "full_rebuild_required": False},
            drift_plan_result={"ok": True, "refresh_allowed": True, "rows": []},
            source_authority=source_authority,
        )
        unstable = {
            "ok": False,
            "reason": "source_capture_not_quiescent",
            "candidate_created": False,
            "snapshot_id": "",
        }
        with patch.object(mirror, "refresh_drift_gate") as drift_gate, patch.object(
            mirror, "run_mirror"
        ) as owner, patch.object(
            mirror, "_current_work_git_head", return_value="a" * 40
        ), patch.object(
            mirror, "stable_previous_pointer", return_value=({}, "snapshot-1", [])
        ), patch.object(
            mirror, "read_control_plane_files", return_value={}
        ), patch.object(
            mirror, "committed_latest_pointer", return_value=None
        ), patch.object(
            mirror, "capture_snapshot_and_live_validate",
            return_value=(unstable, "", {}, {}),
        ):
            payload = mirror._refresh_unlocked(
                mirror.REFRESH_CONFIRMATION,
                changed,
                preflight=preflight,
                source_authority=source_authority,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "source_quiescence")
        drift_gate.assert_not_called()
        owner.assert_not_called()

    def test_refresh_recomputes_gate_when_preflight_head_is_stale(self) -> None:
        changed = ["workspace/_bridge/codex_environment_mirror.py"]
        source_authority = {
            "ok": True,
            "work_git": {"worktree_head": "b" * 40, "bare_head": "b" * 40},
        }
        stale_authority = {
            "ok": True,
            "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40},
        }
        preflight = mirror._refresh_preflight_receipt(
            changed,
            scope={"ok": True, "full_rebuild_required": False},
            drift_plan_result={"ok": True, "refresh_allowed": True},
            source_authority=stale_authority,
        )
        blocked = {
            "ok": False,
            "refresh_allowed": False,
            "plan_digest": "fresh-digest",
            "blockers": [{"item_id": "new-drift"}],
        }
        with patch.object(
            mirror, "refresh_drift_gate", return_value=blocked
        ) as drift_gate, patch.object(
            mirror, "_current_work_git_head", return_value="b" * 40
        ), patch.object(mirror, "run_mirror") as owner:
            payload = mirror._refresh_unlocked(
                mirror.REFRESH_CONFIRMATION,
                changed,
                preflight=preflight,
                source_authority=source_authority,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["phase"], "ambient_drift_gate")
        drift_gate.assert_called_once_with()
        owner.assert_not_called()

    def test_drift_review_receipt_is_invalidated_when_input_signature_changes(self) -> None:
        plan = {"ok": True, "plan_digest": "digest", "snapshot_id": "snapshot", "source_signature": "source", "rows": []}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(mirror, "runtime_root", return_value=Path(temp_dir)), patch.object(
            mirror, "drift_review_input_signature", return_value="new-signature"
        ):
            mirror.drift_review_receipt_path().write_text(json.dumps({
                "schema": "codex_environment_mirror.drift_review_receipt.v1",
                "input_signature": "old-signature",
                "rows": [],
            }), encoding="utf-8")
            consumed = mirror.consume_drift_review(plan)
        self.assertFalse(consumed["review_consumed"])
        self.assertEqual(consumed["review_reason"], "receipt_signature_mismatch")

    def test_drift_review_cannot_be_written_when_owner_validation_fails(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "digest",
            "snapshot_id": "snapshot",
            "source_signature": "source",
            "rows": [{
                "item_id": "workspace-bridge-source:owner.py",
                "source_id": "workspace-bridge-source",
                "classification": "source_update",
                "owner": "system_membership",
                "decision_status": "pending_review",
                "blocker": True,
            }],
        }
        failed = {
            "system_membership": {
                "schema": "codex_environment_mirror.owner_validation.v1",
                "ok": False,
                "owner": "system_membership",
                "commands": [],
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(mirror, "runtime_root", return_value=Path(temp_dir)), patch.object(
            mirror, "drift_plan", return_value=plan
        ), patch.object(
            mirror, "validate_drift_review_owners", return_value=failed
        ), patch.object(
            mirror, "drift_review_input_signature", return_value="signature"
        ), patch.object(
            mirror, "_current_work_git_head", return_value="head"
        ):
            result = mirror.record_drift_review(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["source:workspace-bridge-source=adopt_current_authority"],
            )
            receipt_exists = mirror.drift_review_receipt_path().exists()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "review_plan_blocked")
        self.assertFalse(receipt_exists)
        self.assertEqual(result["plan"]["issues"][0]["code"], "owner_validation_failed")

    def test_drift_review_receipt_requires_structured_owner_success_evidence(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "digest",
            "snapshot_id": "snapshot",
            "source_signature": "source",
            "rows": [{
                "item_id": "workspace-bridge-source:owner.py",
                "source_id": "workspace-bridge-source",
                "classification": "source_update",
                "owner": "system_membership",
                "decision_status": "pending_review",
                "blocker": True,
            }],
        }
        forged = {
            "schema": "codex_environment_mirror.drift_review_receipt.v1",
            "input_signature": "signature",
            "reverse_absorption_allowed": False,
            "owner_validations": {},
            "rows": [{
                "item_id": "workspace-bridge-source:owner.py",
                "owner": "system_membership",
                "review_disposition": "adopt_current_authority",
                "owner_receipt_ref": "arbitrary-string",
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(mirror, "runtime_root", return_value=Path(temp_dir)), patch.object(
            mirror, "drift_review_input_signature", return_value="signature"
        ):
            mirror.drift_review_receipt_path().write_text(json.dumps(forged), encoding="utf-8")
            consumed = mirror.consume_drift_review(plan)
        self.assertFalse(consumed["review_consumed"])
        self.assertEqual(consumed["review_reason"], "owner_validation_evidence_invalid")
        self.assertEqual(consumed["invalid_owner_evidence"], ["system_membership"])

    def test_drift_review_receipt_requires_no_reverse_absorption_boundary(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "digest",
            "snapshot_id": "snapshot",
            "source_signature": "source",
            "rows": [],
        }
        receipt = {
            "schema": "codex_environment_mirror.drift_review_receipt.v1",
            "input_signature": "signature",
            "reverse_absorption_allowed": True,
            "owner_validations": {},
            "rows": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(mirror, "runtime_root", return_value=Path(temp_dir)), patch.object(
            mirror, "drift_review_input_signature", return_value="signature"
        ):
            mirror.drift_review_receipt_path().write_text(json.dumps(receipt), encoding="utf-8")
            consumed = mirror.consume_drift_review(plan)
        self.assertFalse(consumed["review_consumed"])
        self.assertEqual(consumed["review_reason"], "receipt_reverse_absorption_boundary_invalid")

    def test_mirror_control_plane_owner_requires_clean_committed_plan(self) -> None:
        with patch.object(mirror, "git_result_at", side_effect=[
            {"ok": True, "stdout": ""},
            {"ok": True, "stdout": "a" * 40},
        ]), patch.object(mirror, "run_mirror", return_value={"schema": "codex_mirror.plan.v1", "ok": True}):
            current = mirror._mirror_control_plane_owner_validation()
        self.assertTrue(current["ok"])
        with patch.object(mirror, "git_result_at", side_effect=[
            {"ok": True, "stdout": " M manifests/asset-dispositions.json"},
            {"ok": True, "stdout": "a" * 40},
        ]), patch.object(mirror, "run_mirror", return_value={"schema": "codex_mirror.plan.v1", "ok": True}):
            dirty = mirror._mirror_control_plane_owner_validation()
        self.assertFalse(dirty["ok"])

    def test_runtime_owner_validation_uses_windows_owner_python_when_available(self) -> None:
        owner_environment = {"PATH": r"C:\\Python;C:\\Windows\\System32"}
        with patch.object(
            mirror,
            "_windows_owner_command",
            return_value=([r"C:\\Python\\python.exe", r"C:\\mirror\\mirror_cli.py"], owner_environment),
        ), patch.object(mirror, "run_json", return_value={"ok": True}) as run:
            result = mirror._runtime_owner_validation()
        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertEqual(command[0], r"C:\\Python\\python.exe")
        self.assertEqual(command[1], "-c")
        self.assertEqual(command[-1], r"C:\\mirror\\mirror_cli.py")
        self.assertEqual(run.call_args.kwargs["extra_env"], owner_environment)

    def test_codex_cli_drift_review_validates_declared_mirror_plugin_inventory(self) -> None:
        source_inventory = {
            "schema": "codex_environment_mirror.plugin_inventory_owner_validation.v1",
            "ok": False,
            "unresolved_count": 1,
            "unresolved": ["browser@openai-bundled"],
        }
        with patch.object(
            mirror,
            "_owner_validation_commands",
            return_value=[["python", "codex_plugin_config_health.py"]],
        ), patch.object(
            mirror,
            "_run_drift_owner_validator",
            return_value={"ok": True, "returncode": 0},
        ), patch.object(
            mirror,
            "_plugin_inventory_owner_validation",
            return_value=source_inventory,
            create=True,
        ) as inventory:
            evidence = mirror.validate_drift_review_owners({"codex_cli"})

        self.assertFalse(evidence["codex_cli"]["ok"])
        self.assertEqual(evidence["codex_cli"]["commands"][-1]["ref"], "mirror_cli.export_plugin_inventory from source-authorities")
        self.assertEqual(evidence["codex_cli"]["commands"][-1]["result"]["unresolved_count"], 1)
        inventory.assert_called_once_with()

    def test_refresh_normalizes_explicit_work_git_relative_paths_for_windows_owner(self) -> None:
        expected = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace\workspace\_bridge\codex_environment_mirror.py"
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), \
                patch.object(mirror, "source_authority_variables", return_value={"WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"}), \
                patch.object(mirror, "_refresh_unlocked", return_value={"ok": False}) as refresh:
            payload = mirror.refresh("", ["workspace/_bridge/codex_environment_mirror.py"])

        self.assertFalse(payload["ok"])
        refresh.assert_called_once_with("", [expected])

    def test_changed_path_normalization_deduplicates_relative_and_unc_work_git_paths(self) -> None:
        unc = (
            r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"
            r"\workspace\_bridge\codex_environment_mirror.py"
        )
        with patch.object(
            mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")
        ), patch.object(
            mirror, "source_authority_variables", return_value={
                "WORK_GIT_ROOT": r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"
            }
        ):
            normalized = mirror.normalize_changed_paths_for_mirror_owner([
                "workspace/_bridge/codex_environment_mirror.py",
                unc,
            ])

        self.assertEqual(normalized, [unc])

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

    def test_static_release_confirmation_without_scoped_grant_has_no_release_side_effects(self) -> None:
        not_current = {"ok": False, "reason": "control_plane_not_current_for_tag"}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ""}), \
                patch.object(mirror, "existing_release_for_current_state", return_value=not_current), \
                patch.object(mirror, "release_plan") as plan, \
                patch.object(mirror, "contract_review_plan") as review, \
                patch.object(mirror, "run_mirror") as validate, \
                patch.object(mirror, "gh_result") as github:
            payload = mirror.release(mirror.RELEASE_CONFIRMATION, tag="seed-v3.4.0")

        self.assertFalse(payload["ok"])
        self.assertEqual("scoped_authorization_grant_required", payload["reason"])
        self.assertFalse(payload["static_confirmation_is_authorization"])
        plan.assert_not_called()
        review.assert_not_called()
        validate.assert_not_called()
        github.assert_not_called()

    def test_draft_resume_requires_scoped_grant_before_resume_side_effects(self) -> None:
        elsewhere = {"ok": False, "terminal": True, "reason": "existing_tag_points_elsewhere"}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ""}), \
                patch.object(mirror, "existing_release_for_current_state", return_value=elsewhere), \
                patch.object(mirror, "resume_ancestor_release_draft") as resume:
            payload = mirror.release(mirror.RELEASE_CONFIRMATION, tag="seed-v3.4.0")

        self.assertEqual("scoped_authorization_grant_required", payload["reason"])
        resume.assert_not_called()

    def test_read_only_release_preflight_failure_does_not_consume_grant(self) -> None:
        not_current = {"ok": False, "reason": "control_plane_not_current_for_tag"}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), \
                patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ""}), \
                patch.object(mirror, "existing_release_for_current_state", return_value=not_current), \
                patch.object(mirror, "release_plan", return_value={"ok": True}), \
                patch.object(mirror, "contract_review_plan", return_value={"required_review_files": ["contract"], "review_current": False}), \
                patch.object(mirror.scoped_authorization, "consume_grant") as consume:
            payload = mirror.release(
                mirror.RELEASE_CONFIRMATION,
                tag="seed-v3.4.0",
                authorization_grant_ref="scoped-authorization:grant:test",
                authorization_thread_id="thread-test",
            )

        self.assertEqual("codex_contract_review_required", payload["reason"])
        consume.assert_not_called()

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
            if args == ["ls-remote", "--tags", "origin", f"refs/tags/{tag}"]:
                return {"ok": True, "stdout": f"{head}\trefs/tags/{tag}"}
            if args == ["rev-parse", "-q", "--verify", f"refs/tags/{tag}"]:
                return {"ok": True, "stdout": head}
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

    def test_mcp_release_assets_emits_only_required_public_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_root = root / "archives"
            runtime = root / "runtime"
            archive_root.mkdir()
            archive = archive_root / "public-deadbeef.tar.gz"
            archive.write_bytes(b"bundle")
            manifest = {
                "bundles": [
                    {"id": "public", "required": True, "distribution": "github_release_asset"},
                    {"id": "private", "required": True, "distribution": "encrypted_external_archive"},
                ]
            }
            readiness = {
                "ok": True,
                "bundle_plan_ready": True,
                "bundle_index": {"bundles": {"public": {"archive": archive.name, "sha256": "deadbeef", "platform": "linux-x64", "entrypoints": ["bin/tool"]}}},
            }
            completed = type("Completed", (), {"stdout": json.dumps(readiness)})()
            with patch.object(mirror, "WORK_GIT_ROOT", root), \
                    patch.object(mirror.subprocess, "run", return_value=completed), \
                    patch.dict(os.environ, {mirror.MCP_BUNDLE_ARCHIVE_ENV: str(archive_root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(runtime)}):
                path = root / "workspace" / "_bridge"
                path.mkdir(parents=True)
                (path / "mcp_recovery_bundle_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                result = mirror.mcp_release_assets("snapshot-1")
                self.assertTrue(result["ok"], result)
                self.assertEqual([item["id"] for item in result["assets"]], ["public"])
                index = json.loads(Path(result["index_path"]).read_text(encoding="utf-8"))
                self.assertEqual(index["snapshot_id"], "snapshot-1")

    def test_mcp_bundle_readiness_uses_owner_archive_root_by_default(self) -> None:
        expected = Path.home() / ".codex-app" / "mcp-recovery-bundles"
        captured: dict[str, Path] = {}

        def readiness(_manifest, _variables, archive_root):
            captured["archive_root"] = archive_root
            return {"ok": True, "capability_restore_ready": True}

        import mcp_recovery_bundle_owner as bundle_owner

        with patch.dict(os.environ, {}, clear=False), patch.object(bundle_owner, "readiness", side_effect=readiness):
            os.environ.pop(mirror.MCP_BUNDLE_ARCHIVE_ENV, None)
            result = mirror.mcp_bundle_readiness()
        self.assertTrue(result["ok"])
        self.assertEqual(captured["archive_root"], expected)

    def test_release_bundle_assets_reuse_matching_prior_release_archive(self) -> None:
        bundle_assets = {
            "snapshot_id": "snapshot-1",
            "assets": [
                {"id": "unchanged", "name": "unchanged.tar.gz", "path": "/tmp/unchanged.tar.gz", "sha256": "a" * 64, "size_bytes": 10},
                {"id": "changed", "name": "changed.tar.gz", "path": "/tmp/changed.tar.gz", "sha256": "b" * 64, "size_bytes": 11},
            ],
        }
        releases = [{
            "draft": False,
            "tag_name": "seed-v2.7.0",
            "assets": [{"name": "unchanged.tar.gz", "digest": "sha256:" + "a" * 64, "size": 10, "browser_download_url": "https://example.test/unchanged"}],
        }]
        with patch.object(mirror, "gh_result", return_value={"ok": True, "stdout": json.dumps(releases)}), patch.object(mirror, "runtime_root", return_value=Path(tempfile.mkdtemp())):
            prepared = mirror.prepare_release_bundle_assets(bundle_assets, tag="seed-v2.8.0", remote_url="https://github.com/example/mirror.git")
        self.assertEqual([item["name"] for item in prepared["upload_assets"]], ["changed.tar.gz"])
        reused = next(item for item in prepared["assets"] if item["name"] == "unchanged.tar.gz")
        self.assertEqual(reused["release_tag"], "seed-v2.7.0")
        self.assertEqual(reused["release_asset_url"], "https://example.test/unchanged")

    def test_release_bundle_verification_accepts_hash_verified_prior_release_asset(self) -> None:
        assets = {"assets": [{"name": "unchanged.tar.gz", "sha256": "a" * 64, "size_bytes": 10, "release_tag": "seed-v2.7.0"}]}
        releases = [{"draft": False, "tag_name": "seed-v2.7.0", "assets": [{"name": "unchanged.tar.gz", "digest": "sha256:" + "a" * 64, "size": 10}]}]
        with patch.object(mirror, "gh_result", return_value={"ok": True, "stdout": json.dumps(releases)}):
            self.assertTrue(mirror.release_bundle_assets_verified({"assets": []}, assets, tag="seed-v2.8.0", remote_url="https://github.com/example/mirror.git"))

    def test_release_attachment_check_requires_every_mcp_asset(self) -> None:
        view = {"assets": [{"name": "mcp-bundle-index.json"}, {"name": "one.tar.gz"}]}
        self.assertTrue(mirror.release_has_required_attachments(view, {"mcp-bundle-index.json", "one.tar.gz"}))
        self.assertFalse(mirror.release_has_required_attachments(view, {"mcp-bundle-index.json", "two.tar.gz"}))

    def test_release_attachment_check_accepts_github_asset_label(self) -> None:
        view = {"assets": [{"name": "mcp-bundle-index-snapshot-1.json", "label": "mcp-bundle-index.json"}]}
        self.assertTrue(mirror.release_has_required_attachments(view, {"mcp-bundle-index.json"}))
        self.assertEqual(
            mirror.release_asset(view, "mcp-bundle-index.json")["name"],
            "mcp-bundle-index-snapshot-1.json",
        )

    def test_tagged_release_snapshot_uses_immutable_tag_bytes(self) -> None:
        tag = "seed-v2.7.0"
        manifest = b'{"snapshot":true}\n'
        with patch.object(
            mirror,
            "git_file_bytes",
            side_effect=[
                {"ok": True, "content": b'{"snapshot_id":"snapshot-1"}\n'},
                {"ok": True, "content": manifest},
            ],
        ):
            payload = mirror.tagged_release_snapshot(tag)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["manifest_sha256"], hashlib.sha256(manifest).hexdigest())

    def test_release_index_matches_tagged_bundle_without_timestamp_comparison(self) -> None:
        bundle_assets = {
            "assets": [{"name": "one.tar.gz", "sha256": "a" * 64, "size_bytes": 12}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "index.json"
            path.write_text(json.dumps({
                "snapshot_id": "snapshot-1",
                "generated_at": "different-time",
                "assets": [{"name": "one.tar.gz", "sha256": "a" * 64, "size_bytes": 12}],
            }), encoding="utf-8")
            self.assertTrue(mirror.release_index_matches(path, "snapshot-1", bundle_assets))

    def test_remote_tag_commit_accepts_matching_annotated_tag_object(self) -> None:
        tag = "seed-v2.7.0"
        commit = "a" * 40
        tag_object = "b" * 40

        def git(args: list[str], **_kwargs: object) -> dict[str, object]:
            if args == ["ls-remote", "--tags", "origin", f"refs/tags/{tag}"]:
                return {"ok": True, "stdout": f"{tag_object}\trefs/tags/{tag}"}
            if args == ["rev-parse", "-q", "--verify", f"refs/tags/{tag}"]:
                return {"ok": True, "stdout": tag_object}
            self.fail(f"unexpected git command: {args}")

        with patch.object(mirror, "git_result", side_effect=git):
            self.assertEqual(mirror.remote_tag_commit("origin", tag, commit), commit)

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

    def test_committed_snapshot_revalidates_expired_receipt_before_rebuild(self) -> None:
        validation = {
            "schema": "codex_environment_mirror.validate.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "source_signature": "source-1",
            "readiness": {
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
            },
            "source_freshness": {"checked": True, "ok": True},
            "issues": [],
        }
        source_authority = {"work_git": {"worktree_head": "same"}}
        with patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), \
                patch.object(mirror, "snapshot_json_asset", return_value={"work_git": {"worktree_head": "same"}}), \
                patch.object(mirror, "git_result", return_value={"ok": True, "stdout": ""}), \
                patch.object(mirror, "control_plane_validation_receipt", return_value=({}, 240.0)), \
                patch.object(mirror, "run_mirror", return_value={"ok": True, "snapshot_id": "snapshot-1"}) as owner, \
                patch.object(mirror, "validation_receipt", return_value=validation), \
                patch.object(mirror, "validation_source_signature", return_value="source-1"), \
                patch.object(mirror, "persist_status_validation_receipt") as persist:
            payload = mirror.reusable_committed_snapshot_for_publish(source_authority)

        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["validation_revalidated"])
        owner.assert_called_once_with(
            ["validate", "--live-sources", "--snapshot", "snapshot-1"],
            timeout=300,
        )
        persist.assert_called_once_with(validation)

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
                patch.object(mirror, "mirror_impact_paths", return_value={"ok": True, "impacted_paths": changed}), \
                patch.object(mirror, "affected_source_plan", return_value={"ok": True, "full_rebuild_required": False}) as affected, \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "reconcile_declared_source_drift", return_value={"ok": True, "applied": True}) as reconcile, \
                patch.object(mirror, "refresh", return_value=refresh_payload) as refresh_call, \
                patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False}), \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin"}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source_authority"], source_authority)
        self.assertEqual(payload["refresh_scope"]["mode"], "auto_changed_paths")
        affected.assert_called_once_with(changed)
        reconcile.assert_called_once_with(
            mirror.DRIFT_REVIEW_CONFIRMATION,
            changed,
            scope={"ok": True, "full_rebuild_required": False},
            source_authority=source_authority,
        )
        refresh_call.assert_called_once_with(
            mirror.REFRESH_CONFIRMATION,
            changed,
            preflight=None,
            source_authority=source_authority,
        )

    def test_publish_refresh_scope_unions_explicit_paths_with_unmirrored_git_delta(self) -> None:
        source_authority = {"ok": True, "work_git": {"worktree": "/work/codex-workspace"}}
        prior = ["/work/codex-workspace/workspace/_bridge/prior.py"]
        current = ["workspace/_bridge/current.py"]
        with patch.object(
            mirror,
            "work_git_changed_paths_since_latest",
            return_value={"ok": True, "changed_paths": prior},
        ), patch.object(
            mirror,
            "mirror_impact_paths",
            return_value={"ok": True, "impacted_paths": [*prior, *current], "ignored_non_mirror_paths": []},
        ), patch.object(
            mirror,
            "affected_source_plan",
            return_value={"ok": True, "full_rebuild_required": False},
        ) as affected:
            payload = mirror.publish_refresh_scope(current, source_authority)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "explicit_plus_work_git_delta")
        self.assertEqual(payload["changed_paths"], [*prior, *current])
        affected.assert_called_once_with([*prior, *current])

    def test_publish_refresh_scope_uses_full_capture_when_git_delta_is_unavailable(self) -> None:
        source_authority = {"ok": True, "work_git": {"worktree": "/work/codex-workspace"}}
        with patch.object(
            mirror,
            "work_git_changed_paths_since_latest",
            return_value={"ok": False, "reason": "mirrored_work_git_head_missing"},
        ):
            payload = mirror.publish_refresh_scope(["workspace/_bridge/current.py"], source_authority)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["changed_paths"], [])
        self.assertEqual(payload["fallback_reason"], "mirrored_work_git_head_missing")

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
                patch.object(mirror, "mirror_impact_paths", return_value={"ok": True, "impacted_paths": changed}), \
                patch.object(mirror, "affected_source_plan", return_value={"ok": False, "full_rebuild_required": True, "reasons": ["membership_scope_changed"]}), \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "reconcile_declared_source_drift", return_value={"ok": True, "applied": True}), \
                patch.object(mirror, "refresh", return_value=refresh_payload) as refresh_call, \
                patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": False}), \
                patch.object(mirror, "push_receipt", return_value={"ok": True, "remote": "origin"}), \
                patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}):
            payload = mirror.publish(mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["refresh_scope"]["mode"], "full")
        self.assertEqual(payload["refresh_scope"]["fallback_reason"], "affected_source_plan_requires_full_rebuild")
        refresh_call.assert_called_once_with(
            mirror.REFRESH_CONFIRMATION,
            [],
            preflight=None,
            source_authority=source_authority,
        )

    def test_publish_refresh_scope_ignores_membership_proven_non_mirror_paths(self) -> None:
        source_authority = {"ok": True, "work_git": {"worktree": "/work/codex-workspace"}}
        bridge = "/work/codex-workspace/workspace/_bridge/a.py"
        report = "/work/codex-workspace/docs/superpowers/report.md"
        with patch.object(
            mirror,
            "work_git_changed_paths_since_latest",
            return_value={"ok": True, "changed_paths": [bridge, report]},
        ), patch.object(
            mirror,
            "mirror_impact_paths",
            return_value={"ok": True, "impacted_paths": [bridge], "ignored_non_mirror_paths": [report]},
        ), patch.object(
            mirror,
            "affected_source_plan",
            return_value={"ok": True, "full_rebuild_required": False},
        ) as affected:
            payload = mirror.publish_refresh_scope(None, source_authority)

        self.assertEqual(payload["mode"], "auto_changed_paths")
        self.assertEqual(payload["changed_paths"], [bridge])
        self.assertEqual(payload["mirror_impact"]["ignored_non_mirror_paths"], [report])
        affected.assert_called_once_with([bridge])

    def test_mirror_impact_paths_uses_membership_namespaces(self) -> None:
        projection = {
            "ok": True,
            "schema": "system_membership.v2.mirror_source_projection",
            "change_roots": ["worktree:AGENTS.md", "workspace:_bridge/", "codex_home:AGENTS.md"],
        }
        paths = [
            "AGENTS.md",
            "workspace/_bridge/a.py",
            "codex-home/AGENTS.md",
            "docs/superpowers/report.md",
            "/tmp/ambient/MEMORY.md",
        ]
        with patch("system_membership.mirror_source_projection", return_value=projection):
            payload = mirror.mirror_impact_paths(paths)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["impacted_paths"], [*paths[:3], paths[4]])
        self.assertEqual(payload["ignored_non_mirror_paths"], [paths[3]])

    def test_scoped_drift_reconciliation_records_only_declared_closure(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "plan-1",
            "snapshot_id": "snapshot-1",
            "source_signature": "source-1",
            "rows": [
                {
                    "item_id": "workspace-bridge-source:a.py",
                    "source_id": "workspace-bridge-source",
                    "classification": "source_update",
                    "owner": "system_membership",
                },
                {
                    "item_id": "codex-global-agents",
                    "source_id": "codex-global-agents",
                    "classification": "projection_stale",
                    "owner": "wsl_workspace_owner",
                },
            ],
        }
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["workspace-bridge-source", "codex-global-agents"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        owner_validations = {
            "system_membership": {"ok": True},
            "wsl_workspace_owner": {"ok": True},
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "drift_plan", return_value=plan), \
                patch.object(mirror, "affected_source_plan", return_value=scope), \
                patch.object(mirror, "validate_drift_review_owners", return_value=owner_validations), \
                patch.object(mirror, "drift_review_receipt_path", return_value=Path(temp_dir) / "drift-review.json"), \
                patch.object(mirror, "_current_work_git_head", return_value="a" * 40):
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["workspace/_bridge/a.py", "AGENTS.md"],
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["decision_count"], 2)
        self.assertFalse(payload["reverse_absorption_allowed"])

    def test_scoped_drift_reconciliation_reuses_supplied_affected_source_plan(self) -> None:
        changed = ["workspace/_bridge/a.py"]
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["workspace-bridge-source"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        plan = {
            "ok": True,
            "refresh_allowed": True,
            "plan_digest": "plan",
            "rows": [],
        }
        authority = {
            "ok": True,
            "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40},
        }
        with patch.object(mirror, "affected_source_plan") as affected, patch.object(
            mirror, "drift_plan", return_value=plan
        ) as drift:
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                changed,
                scope=scope,
                source_authority=authority,
            )

        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["refresh_preflight"]["ok"])
        affected.assert_not_called()
        drift.assert_called_once_with(affected=scope)

    def test_scoped_drift_reconciliation_blocks_out_of_scope_or_private_rows(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "plan-2",
            "rows": [
                {
                    "item_id": "codex-native-memory-files:MEMORY.md",
                    "source_id": "codex-native-memory-files",
                    "classification": "private_state",
                    "owner": "memory_governance",
                }
            ],
        }
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["workspace-bridge-source"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        with patch.object(mirror, "drift_plan", return_value=plan), \
                patch.object(mirror, "affected_source_plan", return_value=scope), \
                patch.object(mirror, "_record_drift_review_plan") as record:
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["workspace/_bridge/a.py"],
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blockers"][0]["reason"], "drift_outside_declared_publish_closure")
        record.assert_not_called()

    def test_scoped_drift_reconciliation_reuses_matching_global_review_for_out_of_scope_rows(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "plan-3",
            "snapshot_id": "snapshot-1",
            "source_signature": "source-1",
            "rows": [
                {
                    "item_id": "workspace-bridge-source:a.py",
                    "source_id": "workspace-bridge-source",
                    "classification": "source_update",
                    "owner": "system_membership",
                },
                {
                    "item_id": "codex-native-memory-files:MEMORY.md",
                    "source_id": "codex-native-memory-files",
                    "classification": "private_state",
                    "owner": "memory_governance",
                },
            ],
        }
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["workspace-bridge-source"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        receipt = {
            "schema": "codex_environment_mirror.drift_review_receipt.v1",
            "input_signature": "matching-signature",
            "reverse_absorption_allowed": False,
            "owner_validations": {
                "system_membership": {"ok": True},
                "memory_governance": {"ok": True},
            },
            "rows": [
                {
                    "item_id": "workspace-bridge-source:a.py",
                    "owner": "system_membership",
                    "review_disposition": "adopt_current_authority",
                    "owner_receipt_ref": "owner-validation:system_membership",
                },
                {
                    "item_id": "codex-native-memory-files:MEMORY.md",
                    "owner": "memory_governance",
                    "review_disposition": "owner_export_reviewed",
                    "owner_receipt_ref": "owner-validation:memory_governance",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "drift_plan", return_value=plan), \
                patch.object(mirror, "affected_source_plan", return_value=scope), \
                patch.object(mirror, "drift_review_input_signature", return_value="matching-signature"), \
                patch.object(mirror, "drift_review_receipt_path", return_value=Path(temp_dir) / "drift-review.json"), \
                patch.object(mirror, "_record_drift_review_plan") as record:
            mirror.drift_review_receipt_path().write_text(json.dumps(receipt), encoding="utf-8")
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["workspace/_bridge/a.py"],
            )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["reason"], "current_global_drift_review_reused")
        self.assertEqual(payload["isolated_reviewed_count"], 1)
        self.assertEqual(
            payload["isolated_reviewed_rows"][0]["item_id"],
            "codex-native-memory-files:MEMORY.md",
        )
        self.assertTrue(payload["refresh_preflight"]["ok"])
        record.assert_not_called()

    def test_scoped_drift_reconciliation_reuses_fresh_receipt_without_second_live_plan(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "plan-fresh",
            "snapshot_id": "snapshot-1",
            "source_signature": "source-1",
            "rows": [{
                "item_id": "workspace-bridge-source:a.py",
                "source_id": "workspace-bridge-source",
                "classification": "source_update",
                "owner": "system_membership",
            }],
        }
        receipt = {
            "schema": "codex_environment_mirror.drift_review_receipt.v1",
            "ok": True,
            "generated_at": mirror.now_iso(),
            "input_signature": "unused-by-fast-path",
            "plan_digest": plan["plan_digest"],
            "snapshot_id": plan["snapshot_id"],
            "source_signature": plan["source_signature"],
            "work_git_head": "a" * 40,
            "reverse_absorption_allowed": False,
            "owner_validations": {"system_membership": {"ok": True}},
            "rows": [{
                **plan["rows"][0],
                "review_disposition": "adopt_current_authority",
                "owner_receipt_ref": "owner-validation:system_membership",
            }],
        }
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["workspace-bridge-source"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        authority = {"ok": True, "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40}}
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "drift_review_receipt_path", return_value=Path(temp_dir) / "drift-review.json"), \
                patch.object(mirror, "latest_snapshot_id", return_value="snapshot-1"), \
                patch.object(mirror, "validation_source_signature", return_value="source-1"), \
                patch.object(mirror, "_current_work_git_head", return_value="a" * 40), \
                patch.object(mirror, "drift_review_input_signature", return_value="unused-by-fast-path"), \
                patch.object(mirror, "drift_plan") as drift:
            mirror.drift_review_receipt_path().write_text(json.dumps(receipt), encoding="utf-8")
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["workspace/_bridge/a.py"],
                scope=scope,
                source_authority=authority,
            )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["reason"], "current_global_drift_review_reused")
        self.assertEqual(payload["review"]["reason"], "receipt_reused_within_preflight_ttl")
        drift.assert_not_called()

    def test_fresh_drift_review_receipt_expires_before_publish(self) -> None:
        receipt = {
            "schema": "codex_environment_mirror.drift_review_receipt.v1",
            "ok": True,
            "generated_at": "2000-01-01T00:00:00+00:00",
            "reverse_absorption_allowed": False,
        }
        authority = {"ok": True, "work_git": {"worktree_head": "a" * 40}}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            mirror, "drift_review_receipt_path", return_value=Path(temp_dir) / "drift-review.json"
        ):
            mirror.drift_review_receipt_path().write_text(json.dumps(receipt), encoding="utf-8")
            payload = mirror.consume_fresh_drift_review_for_publish(authority)

        self.assertFalse(payload["review_consumed"])
        self.assertEqual(payload["review_reason"], "receipt_preflight_ttl_expired")

    def test_scoped_drift_reconciliation_reuses_global_review_for_private_row_in_scope(self) -> None:
        plan = {
            "ok": True,
            "plan_digest": "plan-private",
            "snapshot_id": "snapshot-1",
            "source_signature": "source-1",
            "rows": [{
                "item_id": "codex-native-memory-files:MEMORY.md",
                "source_id": "codex-native-memory-files",
                "classification": "private_state",
                "owner": "memory_governance",
            }],
        }
        scope = {
            "ok": True,
            "full_rebuild_required": False,
            "affected_source_ids": ["codex-native-memory-files"],
            "dependent_generated_source_ids": [],
            "reasons": [],
        }
        consumed = {
            **plan,
            "refresh_allowed": True,
            "review_consumed": True,
            "receipt_path": "/runtime/drift-review.json",
            "rows": [{
                **plan["rows"][0],
                "review_disposition": "owner_export_reviewed",
                "owner_receipt_ref": "owner-validation:memory_governance",
                "blocker": False,
            }],
        }
        with patch.object(mirror, "drift_plan", return_value=plan), \
                patch.object(mirror, "affected_source_plan", return_value=scope), \
                patch.object(mirror, "consume_drift_review", return_value=consumed), \
                patch.object(mirror, "_record_drift_review_plan") as record:
            payload = mirror.reconcile_declared_source_drift(
                mirror.DRIFT_REVIEW_CONFIRMATION,
                ["/home/codexlab/.codex-app/memories/MEMORY.md"],
            )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["reason"], "current_global_drift_review_reused")
        self.assertEqual(payload["isolated_reviewed_count"], 0)
        record.assert_not_called()

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
        remote_advanced = False

        def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None, **_kwargs: object) -> dict:
            nonlocal remote_advanced
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
                remote_advanced = True
                return {"ok": True, "returncode": 0, "stdout": "", "stderr_tail": "pushed"}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                remote_head = "a" * 40 if remote_advanced else "b" * 40
                return {"ok": True, "stdout": f"{remote_head}\trefs/heads/main"}
            return {"ok": False, "stdout": "", "stderr_tail": str(args)}

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(mirror, "reusable_committed_snapshot_for_publish", return_value={"ok": False}), \
                patch.object(mirror, "refresh", side_effect=lambda *args, **kwargs: calls.append("refresh") or refresh_payload), \
                patch.object(mirror, "run_mirror", side_effect=lambda *args, **kwargs: calls.append("validate") or validation_payload), \
                patch.object(mirror, "git_network_env_for_remote", return_value=({"HTTPS_PROXY": "http://127.0.0.1:7897"}, {"ok": True, "used": True, "route_mode": "probe_selected_proxy"})), \
                patch.object(mirror, "commit_refresh", side_effect=lambda *args, **kwargs: calls.append("metadata_commit") or {"ok": True, "committed": False, "head": "local"}), \
                patch.object(mirror, "git_result", side_effect=git), \
                patch.object(mirror, "git_transport_result", side_effect=git), \
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

    def test_git_transport_plan_keeps_fallback_per_process_and_bounded(self) -> None:
        selected = {
            "schema": "codex_network_gateway.plan.v1",
            "ok": True,
            "plan": {
                "route_mode": "probe_selected_proxy",
                "route_reason": "probe_selected_proxy_for_this_request",
                "target_kind": "github",
                "proxy_url": "http://127.0.0.1:7897",
                "env": {"HTTPS_PROXY": "http://127.0.0.1:7897"},
                "unset_env": [],
            },
        }
        with patch.object(network_gateway, "route_plan", return_value=selected):
            payload = network_gateway.git_transport_plan("https://github.com/example/repo.git")

        self.assertTrue(payload["ok"])
        self.assertLessEqual(len(payload["attempts"]), 4)
        self.assertEqual(payload["attempts"][0]["name"], "selected_route")
        self.assertEqual(payload["attempts"][0]["env"]["GIT_CONFIG_KEY_0"], "http.proxy")
        self.assertEqual(payload["attempts"][0]["env"]["GIT_CONFIG_VALUE_0"], "http://127.0.0.1:7897")
        self.assertEqual(payload["attempts"][1]["env"]["GIT_CONFIG_KEY_1"], "http.version")
        self.assertEqual(payload["attempts"][1]["env"]["GIT_CONFIG_VALUE_1"], "HTTP/1.1")
        self.assertEqual(payload["attempts"][-2]["name"], "direct_http_1_1")
        self.assertEqual(payload["attempts"][-2]["env"]["HTTPS_PROXY"], "")
        self.assertEqual(payload["attempts"][-1]["name"], "selected_route_retry_once")
        self.assertEqual(payload["attempts"][-1]["retry_of"], "selected_route")
        self.assertEqual(payload["attempts"][-1]["delay_seconds"], 3.0)
        self.assertFalse(payload["writes_system_proxy"])
        self.assertFalse(payload["writes_git_config"])
        self.assertTrue(payload["selected_route"]["git_native_proxy_projected"])

    def test_git_network_plan_does_not_forward_remote_credentials(self) -> None:
        owner = {
            "ok": True,
            "target_kind": "github",
            "selected_route": {},
            "attempts": [{"name": "selected_route", "env": {}}],
        }
        with patch.object(mirror, "run_json", return_value=owner) as run:
            mirror.git_network_env_for_remote("https://secret-token@github.com/example/repo.git")

        command = run.call_args.args[0]
        self.assertNotIn("secret-token", " ".join(command))
        self.assertEqual(command[-1], "https://github.com/")

    def test_git_result_preserves_empty_per_process_proxy_overrides(self) -> None:
        completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(mirror, "_git_executable_and_root", return_value=("git", "/mirror")), patch.object(
            mirror.subprocess, "run", return_value=completed
        ) as run:
            payload = mirror.git_result(["status"], extra_env={"HTTPS_PROXY": ""})

        self.assertTrue(payload["ok"])
        self.assertEqual(run.call_args.kwargs["env"]["HTTPS_PROXY"], "")

    def test_git_transport_uses_wsl_git_and_gh_credential_helper(self) -> None:
        completed = type("Completed", (), {"returncode": 0, "stdout": "head\trefs/heads/main\n", "stderr": ""})()
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/mirror")), patch.object(
            mirror.shutil, "which", return_value="/usr/bin/tool"
        ), patch.object(mirror.subprocess, "run", return_value=completed) as run:
            payload = mirror.git_transport_result(
                ["ls-remote", "--heads", "origin", "main"],
                extra_env={"HTTPS_PROXY": "http://127.0.0.1:7897"},
                prefer_wsl_native=True,
            )

        self.assertTrue(payload["ok"])
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["git", "-C", "/mnt/c/mirror"])
        self.assertIn("credential.helper=!gh auth git-credential", command)
        self.assertEqual(run.call_args.kwargs["env"]["HTTPS_PROXY"], "http://127.0.0.1:7897")
        self.assertNotIn("Token", " ".join(command))

    def test_push_skips_remote_write_when_branch_is_already_current(self) -> None:
        head = "a" * 40
        calls: list[list[str]] = []

        def git(args: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(args)
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://github.com/example/repo.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": head}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                return {"ok": True, "stdout": f"{head}\trefs/heads/main"}
            self.fail(f"unexpected git command: {args}")

        attempts = ([{"name": "selected_route", "env": {}}], {"ok": True})
        with patch.object(mirror, "git_result", side_effect=git), patch.object(
            mirror, "git_transport_result", side_effect=git
        ), patch.object(
            mirror, "git_network_attempts_for_remote", return_value=attempts
        ):
            payload = mirror.push_receipt()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["push_skipped"])
        self.assertEqual(payload["reason"], "remote_branch_already_current")
        self.assertFalse(any(args and args[0] == "push" for args in calls))

    def test_push_advances_to_http1_after_selected_route_reset(self) -> None:
        head = "b" * 40
        old = "a" * 40
        seen_env: list[dict[str, str]] = []

        def git(
            args: list[str],
            *,
            timeout: int = 120,
            extra_env: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> dict[str, object]:
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://github.com/example/repo.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": head}
            seen_env.append(dict(extra_env or {}))
            if args[0] == "ls-remote" and not extra_env.get("GIT_CONFIG_VALUE_0"):
                return {"ok": False, "stderr_tail": "Recv failure: Connection reset by peer"}
            if args[0] == "ls-remote":
                remote = head if any(item.get("pushed") for item in seen_env) else old
                return {"ok": True, "stdout": f"{remote}\trefs/heads/main"}
            if args[0] == "push":
                seen_env[-1]["pushed"] = "1"
                return {"ok": True, "returncode": 0, "stdout": "", "stderr_tail": ""}
            self.fail(f"unexpected git command: {args}")

        attempts = ([
            {"name": "selected_route", "env": {"HTTPS_PROXY": "http://127.0.0.1:7897"}},
            {"name": "selected_route_http_1_1", "env": {"HTTPS_PROXY": "http://127.0.0.1:7897", "GIT_CONFIG_VALUE_0": "HTTP/1.1"}},
        ], {"ok": True})
        with patch.object(mirror, "git_result", side_effect=git), patch.object(
            mirror, "git_transport_result", side_effect=git
        ), patch.object(
            mirror, "git_network_attempts_for_remote", return_value=attempts
        ):
            payload = mirror.push_receipt()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selected_attempt"], "selected_route_http_1_1")
        self.assertEqual(payload["attempt_count"], 2)

    def test_push_accepts_remote_readback_after_ambiguous_transport_failure(self) -> None:
        head = "b" * 40
        old = "a" * 40
        remote_heads = iter((old, head))
        push_calls = 0

        def git(args: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal push_calls
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://github.com/example/repo.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": head}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                value = next(remote_heads)
                return {"ok": True, "stdout": f"{value}\trefs/heads/main"}
            if args == ["push", "origin", "HEAD:main"]:
                push_calls += 1
                return {
                    "ok": False,
                    "returncode": 1,
                    "stderr_tail": "RPC failed; remote end hung up unexpectedly",
                }
            self.fail(f"unexpected git command: {args}")

        attempts = ([{"name": "selected_route", "env": {}}], {"ok": True})
        with patch.object(mirror, "git_result", side_effect=git), patch.object(
            mirror, "git_transport_result", side_effect=git
        ), patch.object(
            mirror, "git_network_attempts_for_remote", return_value=attempts
        ):
            payload = mirror.push_receipt()

        self.assertTrue(payload["ok"])
        self.assertEqual(push_calls, 1)
        self.assertEqual(payload["selected_attempt"], "selected_route")
        self.assertTrue(payload["attempts"][0]["recovered_from_ambiguous_push_result"])
        self.assertEqual(payload["remote_verification"]["remote_head"], head)

    def test_push_retries_selected_route_once_inside_same_owner_operation(self) -> None:
        head = "b" * 40
        old = "a" * 40
        push_calls = 0

        def git(args: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal push_calls
            if args == ["status", "--short"]:
                return {"ok": True, "stdout": ""}
            if args == ["remote", "get-url", "origin"]:
                return {"ok": True, "stdout": "https://github.com/example/repo.git"}
            if args == ["branch", "--show-current"]:
                return {"ok": True, "stdout": "main"}
            if args == ["rev-parse", "HEAD"]:
                return {"ok": True, "stdout": head}
            if args == ["ls-remote", "--heads", "origin", "main"]:
                remote = head if push_calls >= 2 else old
                return {"ok": True, "stdout": f"{remote}\trefs/heads/main"}
            if args == ["push", "origin", "HEAD:main"]:
                push_calls += 1
                if push_calls == 1:
                    return {"ok": False, "returncode": 1, "stderr_tail": "connection reset"}
                return {"ok": True, "returncode": 0, "stdout": "", "stderr_tail": ""}
            self.fail(f"unexpected git command: {args}")

        attempts = ([
            {"name": "selected_route", "env": {}},
            {
                "name": "selected_route_retry_once",
                "env": {},
                "retry_of": "selected_route",
                "delay_seconds": 0.001,
            },
        ], {"ok": True})
        with patch.object(mirror, "git_result", side_effect=git), patch.object(
            mirror, "git_transport_result", side_effect=git
        ), patch.object(
            mirror, "git_network_attempts_for_remote", return_value=attempts
        ):
            payload = mirror.push_receipt()

        self.assertTrue(payload["ok"])
        self.assertEqual(push_calls, 2)
        self.assertEqual(payload["selected_attempt"], "selected_route_retry_once")
        self.assertEqual(payload["attempts"][-1]["retry_of"], "selected_route")

    def test_contract_review_reuses_current_receipt_without_rewrite_or_commit(self) -> None:
        current = {
            "schema": "codex_environment_mirror.contract_review_plan.v1",
            "ok": True,
            "review_current": True,
            "receipt_path": "/mirror/manifests/contract-review-state.json",
            "required_review_files": ["README.md"],
        }
        pushed = {"ok": True, "reason": "remote_branch_already_current", "push_skipped": True}
        with patch.object(mirror, "contract_review_plan", side_effect=[current, current]), patch.object(
            mirror, "push_receipt", return_value=pushed
        ), patch.object(mirror, "commit_refresh") as commit, patch.object(mirror.Path, "write_text") as write:
            payload = mirror.contract_review(
                mirror.CONTRACT_REVIEW_CONFIRMATION,
                decisions=[],
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["reused"])
        commit.assert_not_called()
        write.assert_not_called()

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

    def test_incremental_refresh_expands_to_full_for_unanticipated_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "previous").mkdir(parents=True)
            previous = self.write_latest(root, "previous")
            candidates = iter(("candidate-1", "candidate-2"))
            snapshot_calls: list[list[str]] = []

            def owner(args: list[str], *, timeout: int = 300) -> dict:
                if args == ["plan"]:
                    return {"ok": True}
                if args[:2] == ["snapshot", "--apply"]:
                    snapshot_calls.append(args)
                    candidate = next(candidates)
                    (root / "snapshots" / candidate).mkdir()
                    self.write_latest(root, candidate)
                    return {"ok": True, "snapshot_id": candidate}
                if "candidate-1" in args:
                    return {
                        "ok": False,
                        "issues": [
                            {"code": "source_assets_missing"},
                            {"code": "source_assets_stale"},
                            {"code": "source_assets_changed"},
                            {"code": "generated_source_changed"},
                        ],
                    }
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
                return {
                    "ok": True,
                    "mirror_valid": True,
                    "capability_restore_ready": True,
                    "full_state_restore_ready": False,
                    "issues": [],
                }

            with patch.dict(os.environ, {
                    "CODEX_ENV_MIRROR_ROOT": str(root),
                    "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime"),
                }), patch.object(
                    mirror, "stable_previous_pointer", return_value=(previous, "previous", [])
                ), patch.object(
                    mirror, "run_mirror", side_effect=owner
                ), patch.object(
                    mirror, "write_control_plane_state", return_value={"ok": True, "changed": True}
                ), patch.object(
                    mirror, "commit_refresh", return_value={"ok": True, "committed": True, "head": "abc"}
                ), patch.object(
                    mirror, "commit_retention_cleanup", return_value={"ok": True, "committed": False, "head": "abc"}
                ), patch.object(mirror.time, "sleep"):
                payload = mirror.refresh(
                    mirror.REFRESH_CONFIRMATION,
                    changed_paths=["changed.py"],
                )

            self.assertTrue(payload["ok"])
            self.assertEqual(
                snapshot_calls,
                [
                    ["snapshot", "--apply", "--changed", "changed.py"],
                    ["snapshot", "--apply"],
                ],
            )
            self.assertEqual(payload["attempts"][0]["next_capture_mode"], "full")
            self.assertGreaterEqual(payload["attempts"][0]["elapsed_ms"], 0.0)
            self.assertGreaterEqual(payload["attempts"][1]["elapsed_ms"], 0.0)

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
        self.assertEqual("/init", argv[0])
        self.assertIn("python.exe", argv[1])
        self.assertTrue(argv[2].endswith(r"\python.exe"))
        self.assertEqual(r"C:\Users\45543\codex-env-mirror\scripts\mirror_cli.py", argv[3])
        self.assertIn(r"C:\Program Files\Git", environment["PATH"])
        self.assertIn(";", environment["PATH"])
        self.assertEqual(environment["CODEX_MIRROR_SOURCE_READ_ONLY"], "1")
        self.assertEqual(environment["CODEX_MIRROR_REVERSE_OVERWRITE_BLOCKED"], "1")

    def test_windows_owner_command_never_falls_back_to_direct_pe_execution(self) -> None:
        with patch.object(mirror, "mirror_root", return_value=Path("/mnt/c/Users/45543/codex-env-mirror")), patch.object(
            mirror, "_wsl_windows_executable_command", return_value=[]
        ):
            command = mirror._windows_owner_command(
                Path("/mnt/c/Users/45543/codex-env-mirror/scripts/mirror_cli.py"), ["validate"]
            )
        self.assertIsNone(command)

    def test_windows_owner_probe_preserves_binfmt_argv_prefix(self) -> None:
        owner = [
            "/init",
            "/mnt/c/Python314/python.exe",
            r"C:\Python314\python.exe",
            r"C:\mirror\scripts\mirror_cli.py",
        ]
        command = mirror._windows_owner_probe_command(owner, "print('probe')")
        self.assertEqual(owner[:3], command[:3])
        self.assertEqual(["-c", "print('probe')", owner[3]], command[3:])

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

    def test_run_mirror_attaches_bounded_owner_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cli = root / "scripts" / "mirror_cli.py"
            cli.parent.mkdir(parents=True)
            cli.write_text("# owner\n", encoding="utf-8")

            class Result:
                returncode = 0
                stdout = '{"ok": true}'
                stderr = ""

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
                    patch("codex_environment_mirror.subprocess.run", return_value=Result()):
                payload = mirror.run_mirror(["validate"])

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["_owner_operation"], "validate")
        self.assertIsInstance(payload["_owner_elapsed_ms"], float)
        self.assertGreaterEqual(payload["_owner_elapsed_ms"], 0.0)

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

    def test_health_accepts_only_current_reviewed_publication_pending_state(self) -> None:
        state = {
            "schema": "codex_environment_mirror.status.v1",
            "ok": False,
            "latest_snapshot_id": "snapshot-1",
            "git": {"initialized": True, "clean": True},
            "control_plane": {"ok": True},
            "issues": [{"code": "source_assets_changed", "source_id": "workspace-bridge-source"}],
        }
        release_gate = {
            "ok": True,
            "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40},
        }
        reviewed_drift = {
            "review_consumed": True,
            "refresh_allowed": True,
            "receipt_path": "runtime/drift-review.json",
            "plan_digest": "digest",
            "row_count": 1,
            "affected_source_plan": {"full_rebuild_required": False},
        }
        with patch.object(mirror, "status", return_value=state), patch.object(
            mirror, "work_git_release_gate", return_value=release_gate
        ), patch.object(mirror, "drift_plan_from_validation_issues", return_value={"plan_digest": "digest"}), patch.object(
            mirror, "consume_drift_review", return_value=reviewed_drift
        ):
            payload = mirror.health()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "publication_pending")
        self.assertEqual(payload["convergence"]["terminal_action"], "publish")
        self.assertFalse(payload["convergence"]["reverse_absorption_allowed"])

    def test_health_rejects_unreviewed_or_unknown_drift(self) -> None:
        state = {
            "ok": False,
            "latest_snapshot_id": "snapshot-1",
            "git": {"initialized": True, "clean": True},
            "control_plane": {"ok": True},
            "issues": [
                {"code": "source_assets_changed", "source_id": "workspace-bridge-source"},
                {"code": "unknown_runtime_drift", "source_id": "runtime"},
            ],
        }
        with patch.object(mirror, "status", return_value=state) as status, patch.object(
            mirror, "drift_plan_from_validation_issues"
        ) as plan:
            mixed = mirror.health()
        self.assertFalse(mixed["ok"])
        plan.assert_not_called()
        status.assert_called_once()

        state["issues"] = [{"code": "source_assets_changed", "source_id": "codex-native-memory-files"}]
        with patch.object(mirror, "status", return_value=state), patch.object(
            mirror, "drift_plan_from_validation_issues", return_value={"plan_digest": "stale"}
        ), patch.object(mirror, "consume_drift_review", return_value={
            "review_consumed": False,
            "review_reason": "receipt_signature_mismatch",
        }):
            outside = mirror.health()
        self.assertFalse(outside["ok"])
        self.assertEqual(outside["status"], "blocked")
        self.assertEqual(outside["drift_review"]["reason"], "receipt_signature_mismatch")

    def test_health_accepts_reviewed_full_rebuild(self) -> None:
        state = {
            "ok": False,
            "latest_snapshot_id": "snapshot-1",
            "git": {"initialized": True, "clean": True},
            "control_plane": {"ok": True},
            "issues": [{"code": "source_assets_changed", "source_id": "workspace-bridge-source"}],
        }
        reviewed_drift = {
            "review_consumed": True,
            "refresh_allowed": True,
            "receipt_path": "runtime/drift-review.json",
            "plan_digest": "digest",
            "row_count": 1,
            "affected_source_plan": {"full_rebuild_required": True},
        }
        with patch.object(mirror, "status", return_value=state), patch.object(
            mirror, "work_git_release_gate", return_value={"ok": True, "work_git": {}}
        ), patch.object(mirror, "drift_plan_from_validation_issues", return_value={"plan_digest": "digest"}), patch.object(
            mirror, "consume_drift_review", return_value=reviewed_drift
        ):
            payload = mirror.health()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "publication_pending")
        self.assertEqual(payload["convergence"]["refresh_mode"], "full")

    def test_health_accepts_reviewed_source_and_private_state_drift(self) -> None:
        state = {
            "ok": False,
            "latest_snapshot_id": "snapshot-1",
            "git": {"initialized": True, "clean": True},
            "control_plane": {"ok": True},
            "issues": [
                {"code": "source_assets_changed", "source_id": "workspace-bridge-source"},
                {"code": "source_assets_missing", "source_id": "codex-native-memory-files"},
            ],
        }
        reviewed_drift = {
            "review_consumed": True,
            "refresh_allowed": True,
            "receipt_path": "runtime/drift-review.json",
            "plan_digest": "digest",
            "row_count": 2,
            "affected_source_plan": {"full_rebuild_required": True},
        }
        with patch.object(mirror, "status", return_value=state), patch.object(
            mirror, "drift_plan_from_validation_issues", return_value={"plan_digest": "digest"}
        ), patch.object(mirror, "consume_drift_review", return_value=reviewed_drift), patch.object(
            mirror, "work_git_release_gate", return_value={"ok": True, "work_git": {"worktree_head": "a" * 40, "bare_head": "a" * 40}}
        ):
            payload = mirror.health()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "publication_pending")
        self.assertEqual(payload["convergence"]["drift_row_count"], 2)
        self.assertFalse(payload["convergence"]["reverse_absorption_allowed"])

    def test_control_plane_validation_receipt_rejects_stale_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "manifests"
            manifests.mkdir(parents=True)
            path = manifests / "control-plane-state.json"
            path.write_text(
                json.dumps({
                    "generated_at": "2026-07-26T00:00:00+00:00",
                    "source_signature": "source-signature",
                    "snapshot": {"snapshot_id": "snapshot-1", "capture_mode": "full"},
                    "readiness": {"mirror_valid": True, "capability_restore_ready": True},
                    "source_freshness": {"checked": True, "ok": True},
                }),
                encoding="utf-8",
            )
            stale_time = path.stat().st_mtime - mirror.STATUS_VALIDATION_TTL_SECONDS - 1
            os.utime(path, (stale_time, stale_time))
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), patch.object(
                mirror, "validation_source_signature", return_value="source-signature"
            ):
                receipt, age = mirror.control_plane_validation_receipt("snapshot-1")

        self.assertEqual(receipt, {})
        self.assertIsNotNone(age)
        self.assertGreater(age, mirror.STATUS_VALIDATION_TTL_SECONDS)

    def test_control_plane_validation_receipt_rejects_signature_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "control-plane-state.json").write_text(
                json.dumps({
                    "generated_at": "2026-07-26T00:00:00+00:00",
                    "source_signature": "old-signature",
                    "snapshot": {"snapshot_id": "snapshot-1", "capture_mode": "full"},
                    "readiness": {"mirror_valid": True, "capability_restore_ready": True},
                    "source_freshness": {"checked": True, "ok": True},
                }),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), patch.object(
                mirror, "validation_source_signature", return_value="current-signature"
            ):
                receipt, _ = mirror.control_plane_validation_receipt("snapshot-1")

        self.assertEqual(receipt, {})

    def test_status_reports_stale_control_plane_without_healthy_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "snapshots" / "snapshot-1").mkdir(parents=True)
            self.write_latest(root, "snapshot-1")
            manifests = root / "manifests"
            manifests.mkdir()
            state_path = manifests / "control-plane-state.json"
            state_path.write_text(
                json.dumps({
                    "generated_at": "2026-07-26T00:00:00+00:00",
                    "source_signature": "source-signature",
                    "snapshot": {"snapshot_id": "snapshot-1", "capture_mode": "full"},
                    "readiness": {"mirror_valid": True, "capability_restore_ready": True},
                    "source_freshness": {"checked": True, "ok": True},
                }),
                encoding="utf-8",
            )
            stale_time = state_path.stat().st_mtime - mirror.STATUS_VALIDATION_TTL_SECONDS - 1
            os.utime(state_path, (stale_time, stale_time))

            def git(args: list[str], *, timeout: int = 120, extra_env: dict | None = None) -> dict:
                values = {
                    ("status", "--short"): "",
                    ("rev-parse", "--short", "HEAD"): "abc123",
                    ("remote",): "origin",
                }
                return {"ok": tuple(args) in values, "stdout": values.get(tuple(args), "")}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), patch.object(
                mirror, "validation_source_signature", return_value="source-signature"
            ), patch.object(
                mirror, "run_mirror", return_value={"ok": False, "reason": "mirror_cli_unavailable"}
            ), patch.object(mirror, "git_result", side_effect=git), patch.object(
                mirror, "control_plane_status", return_value={"ok": True, "snapshot_id": "snapshot-1"}
            ):
                payload = mirror.status()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["validation"]["validation_state"], "stale")
        self.assertFalse(payload["readiness"]["mirror_valid"])
        self.assertFalse(payload["readiness"]["capability_restore_ready"])

    def test_status_force_fresh_skips_both_caches_and_mutating_actions(self) -> None:
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
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "snapshots" / "snapshot-1").mkdir(parents=True)
            self.write_latest(root, "snapshot-1")
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), patch.object(
                mirror, "validation_source_signature", return_value="source-signature"
            ), patch.object(mirror, "load_status_validation_receipt") as status_cache, patch.object(
                mirror, "control_plane_validation_receipt"
            ) as control_plane_cache, patch.object(
                mirror, "run_mirror", return_value=validation
            ), patch.object(
                mirror, "git_result", return_value={"ok": True, "stdout": ""}
            ), patch.object(
                mirror, "control_plane_status", return_value={"ok": True, "snapshot_id": "snapshot-1"}
            ), patch.object(mirror, "refresh") as refresh, patch.object(
                mirror, "publish"
            ) as publish, patch.object(mirror, "release") as release:
                payload = mirror.status(force_fresh=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["validation"]["validation_state"], "fresh")
        status_cache.assert_not_called()
        control_plane_cache.assert_not_called()
        refresh.assert_not_called()
        publish.assert_not_called()
        release.assert_not_called()

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
            self.assertEqual(first["validation"]["validation_state"], "fresh")
            self.assertEqual(second["validation"]["validation_state"], "cached_fresh")
            self.assertEqual(second["validation"]["freshness_limit_seconds"], mirror.STATUS_VALIDATION_TTL_SECONDS)
            self.assertTrue(second["validation"]["source_signature"])
            self.assertIn("--force-fresh", second["validation"]["force_fresh_command"])
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

    def test_stage_receipt_allows_explicit_owner_handoffs_without_claiming_capability_ready(self) -> None:
        owner = {
            "schema": "codex_mirror.stage_receipt.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "assets": [],
            "hashes_verified": True,
            "membership_guard": {},
            "activation_performed": False,
        }
        bundles = {
            "ok": True,
            "bundle_plan_ready": True,
            "capability_restore_ready": False,
            "blocked_missing_bundle": [],
            "owner_reacquire_required": ["enabled-plugin-reacquisition"],
            "remote_reconnect_required": ["remote-mcp-proxies-source"],
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            mirror, "runtime_root", return_value=Path(temp_dir)
        ), patch.object(mirror, "mcp_bundle_readiness", return_value=bundles):
            payload = mirror.stage_receipt(owner)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["hashes_verified"])
        self.assertFalse(payload["activation_performed"])
        self.assertFalse(payload["capability_restore_ready"])
        self.assertEqual(
            payload["owner_handoffs_pending"],
            ["enabled-plugin-reacquisition", "remote-mcp-proxies-source"],
        )

    def test_stage_receipt_maps_windows_receipt_path_for_wsl_readback(self) -> None:
        owner = {
            "schema": "codex_mirror.stage.v1",
            "ok": True,
            "receipt": r"C:\\Temp\\stage-receipt.json",
        }
        receipt = {
            "schema": "codex_mirror.stage_receipt.v1",
            "ok": True,
            "snapshot_id": "snapshot-1",
            "assets": [],
            "hashes_verified": True,
            "membership_guard": {},
            "activation_performed": False,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "stage-receipt.json"
            local.write_text(json.dumps(receipt), encoding="utf-8")
            with patch.object(mirror, "_local_mirror_source_path", return_value=local), patch.object(
                mirror, "mcp_bundle_readiness", return_value={"ok": True, "capability_restore_ready": True}
            ), patch.object(mirror, "runtime_root", return_value=Path(temp_dir)):
                payload = mirror.stage_receipt(owner)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["hashes_verified"])
        self.assertFalse(payload["activation_performed"])


if __name__ == "__main__":
    unittest.main()
