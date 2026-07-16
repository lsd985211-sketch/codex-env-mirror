#!/usr/bin/env python3

import os
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import codex_environment_mirror as mirror


class CodexEnvironmentMirrorTests(unittest.TestCase):
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

    def test_refresh_reuses_committed_snapshot_when_live_sources_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots" / "current").mkdir(parents=True)
            current = self.write_latest(root, "current")
            validation = {
                "ok": True,
                "mirror_valid": True,
                "capability_restore_ready": True,
                "full_state_restore_ready": False,
                "source_freshness_checked": True,
                "source_freshness_ok": True,
                "issues": [],
                "advisories": {"required_archive_gaps": ["runtime-state"]},
            }
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(current, "current", [])), \
                    patch.object(mirror, "committed_latest_pointer", return_value=current), \
                    patch.object(mirror, "run_mirror", side_effect=[{"ok": True}, validation]) as owner, \
                    patch.object(mirror, "git_result", return_value={"ok": True, "stdout": "abc123"}), \
                    patch.object(mirror, "commit_refresh") as commit:
                payload = mirror.refresh(mirror.REFRESH_CONFIRMATION)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["reused"])
            self.assertEqual(payload["snapshot_id"], "current")
            self.assertFalse(payload["commit"]["committed"])
            self.assertEqual(owner.call_args_list[1].args[0], ["validate", "--live-sources", "--snapshot", "current"])
            commit.assert_not_called()

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
                if args[-1] == "candidate-1":
                    return {"ok": False, "issues": [{"code": "source_assets_changed"}]}
                return {"ok": True, "mirror_valid": True, "capability_restore_ready": True, "full_state_restore_ready": False, "issues": []}

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root), "CODEX_ENV_MIRROR_RUNTIME_ROOT": str(root / "runtime")}), \
                    patch.object(mirror, "stable_previous_pointer", return_value=(previous, "previous", [])), \
                    patch.object(mirror, "run_mirror", side_effect=owner), \
                    patch.object(mirror, "commit_refresh", return_value={"ok": True, "committed": True, "head": "abc"}), \
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

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
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

            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}), \
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
            self.assertEqual(payload["issues"], [])

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
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_RUNTIME_ROOT": temp_dir}), patch.object(mirror, "run_mirror", return_value=owner):
                payload = mirror.execute("stage", target_root=temp_dir, confirm=mirror.STAGE_CONFIRMATION)
            self.assertEqual(payload["schema"], "codex_environment_mirror.stage.v1")
            self.assertEqual(payload["receipt_schema"], "codex_mirror.stage_receipt.v1")
            self.assertEqual(payload["asset_count"], 12)
            self.assertEqual(len(payload["asset_sample"]), mirror.INLINE_SAMPLE_LIMIT)
            self.assertTrue(payload["hashes_verified"])
            self.assertTrue(payload["membership_guard"]["membership_export_sanitized"])
            self.assertFalse(payload["activation_performed"])
            self.assertTrue(Path(payload["full_receipt_artifact"]).is_file())


if __name__ == "__main__":
    unittest.main()
