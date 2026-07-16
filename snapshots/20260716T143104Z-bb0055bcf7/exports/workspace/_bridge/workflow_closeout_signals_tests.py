#!/usr/bin/env python3

import unittest
from unittest.mock import patch

import codex_environment_mirror
import codex_workflow_entry
import workflow_closeout_signals as signals


class WorkflowCloseoutSignalsTests(unittest.TestCase):
    def test_mirror_refresh_required_for_mirrored_authority_surfaces(self) -> None:
        self.assertTrue(signals.mirror_refresh_required(["_bridge/workflow_finalization.py"]))
        self.assertTrue(signals.mirror_refresh_required([r"C:\Users\45543\.codex\skills\example\SKILL.md"]))
        self.assertFalse(signals.mirror_refresh_required([r"C:\Users\45543\codex-env-mirror\README.md"]))
        self.assertFalse(signals.mirror_refresh_required(["docs/project-report.md"]))

    def test_mirror_output_is_not_a_refresh_source(self) -> None:
        self.assertFalse(signals.mirror_refresh_required([r"C:\Users\45543\codex-env-mirror\README.md"]))

    def test_mirror_refresh_uses_membership_projection_roots(self) -> None:
        with patch.object(signals, "membership_mirror_change_roots", return_value=["codex_home:future-module/"]):
            self.assertTrue(signals.mirror_refresh_required([r"C:\Users\45543\.codex\future-module\new.py"]))
            self.assertFalse(signals.mirror_refresh_required([r"C:\Users\45543\.codex\other\new.py"]))

    def test_post_closeout_mirror_publishes_after_successful_finalization(self) -> None:
        finalization = {"ok": True, "project_checkpoint": {"applied": True}}
        owner_result = {"ok": True, "snapshot_id": "snapshot-1"}
        with patch.object(codex_environment_mirror, "publish", return_value=owner_result) as publish:
            payload = signals.apply_post_closeout_mirror(
                finalization,
                changed_files=["_bridge/workflow_finalization.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )
        publish.assert_called_once_with(codex_environment_mirror.PUBLISH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["post_closeout_mirror"]["result"]["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["post_closeout_mirror"]["ordering"], "finalization_and_owner_checks_then_mirror_publish")

    def test_closeout_runs_self_update_before_post_mirror_publish(self) -> None:
        events: list[str] = []

        def optional(**_kwargs):
            events.append("finalization")
            return {
                "profile_candidates": {},
                "external_candidates": {},
                "finalization": {"ok": True, "signals": {}},
            }

        def self_update(**_kwargs):
            events.append("self_update")
            return {"checked": True, "ok": True, "signals": [], "authoritative_owners": []}

        def mirror(finalization, **_kwargs):
            events.append("mirror")
            return {**finalization, "post_closeout_mirror": {"ok": True, "applied": True}}

        def package(context):
            return {
                "ok": True,
                "status": {"outcome": "ok"},
                "pending_disposition": {"items": []},
                "self_update_governance": context["self_update_governance"],
                "finalization": context["finalization"],
            }

        with patch.object(codex_workflow_entry, "optional_closeout_sections", side_effect=optional), \
                patch.object(codex_workflow_entry, "self_update_closeout_signal", side_effect=self_update), \
                patch.object(codex_workflow_entry, "apply_post_closeout_mirror", side_effect=mirror), \
                patch.object(codex_workflow_entry, "capture_iteration_candidates", return_value={}), \
                patch.object(codex_workflow_entry, "build_closeout_package", side_effect=package), \
                patch.object(codex_workflow_entry, "sync_review_groups", return_value=[]), \
                patch.object(codex_workflow_entry, "build_review_summary", return_value={}):
            payload = codex_workflow_entry.closeout(
                task_kind="config_governance",
                outcome="ok",
                auto_finalize=True,
                finalization_changed_file=["AGENTS.md"],
            )

        self.assertEqual(events, ["finalization", "self_update", "mirror"])
        self.assertGreaterEqual(payload["timings"]["total_ms"], 0)

    def test_closeout_owner_failure_prevents_mirror_publish(self) -> None:
        def optional(**_kwargs):
            return {
                "profile_candidates": {},
                "external_candidates": {},
                "finalization": {"ok": True, "signals": {}},
            }

        def package(context):
            return {
                "ok": context["finalization"].get("ok"),
                "status": {"outcome": "ok"},
                "pending_disposition": {"items": []},
                "self_update_governance": context["self_update_governance"],
                "finalization": context["finalization"],
            }

        with patch.object(codex_workflow_entry, "optional_closeout_sections", side_effect=optional), \
                patch.object(codex_workflow_entry, "self_update_closeout_signal", return_value={"checked": True, "ok": False}), \
                patch.object(codex_workflow_entry, "capture_iteration_candidates", return_value={}), \
                patch.object(codex_workflow_entry, "build_closeout_package", side_effect=package), \
                patch.object(codex_workflow_entry, "sync_review_groups", return_value=[]), \
                patch.object(codex_workflow_entry, "build_review_summary", return_value={}), \
                patch.object(codex_environment_mirror, "publish") as publish:
            payload = codex_workflow_entry.closeout(
                task_kind="config_governance",
                outcome="ok",
                auto_finalize=True,
                finalization_changed_file=["AGENTS.md"],
            )

        publish.assert_not_called()
        self.assertFalse(payload["finalization"]["ok"])
        self.assertEqual(payload["finalization"]["blocked_reason"], "required_owner_checks_not_successful")

    def test_post_closeout_mirror_failure_blocks_finalization(self) -> None:
        with patch.object(codex_environment_mirror, "publish", return_value={"ok": False, "reason": "failed"}):
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["AGENTS.md"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blocked_reason"], "post_closeout_mirror_publish_failed")

    def test_post_closeout_mirror_reuses_successful_receipt(self) -> None:
        finalization = {
            "ok": True,
            "post_closeout_mirror": {
                "required": True,
                "applied": True,
                "ok": True,
                "result": {"snapshot_id": "snapshot-1"},
            },
        }
        with patch.object(codex_environment_mirror, "publish") as publish:
            payload = signals.apply_post_closeout_mirror(
                finalization,
                changed_files=["_bridge/workflow_finalization.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )
        publish.assert_not_called()
        self.assertTrue(payload["post_closeout_mirror"]["reused"])
        self.assertEqual(payload["post_closeout_mirror"]["result"]["snapshot_id"], "snapshot-1")

    def test_failed_owner_checks_block_mirror_publish(self) -> None:
        with patch.object(codex_environment_mirror, "publish") as publish:
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["_bridge/workflow_finalization.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=False,
            )
        publish.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blocked_reason"], "required_owner_checks_not_successful")
        self.assertFalse(payload["post_closeout_mirror"]["applied"])

    def test_closeout_projection_preserves_post_mirror_receipt(self) -> None:
        payload = codex_workflow_entry.closeout_cli_projection({
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": True,
            "status": {"outcome": "ok"},
            "finalization": {
                "ok": True,
                "post_closeout_mirror": {
                    "required": True,
                    "applied": True,
                    "ordering": "finalization_and_owner_checks_then_mirror_publish",
                    "result": {"ok": True, "snapshot_id": "snapshot-1"},
                },
            },
        })
        receipt = payload["finalization"]["post_closeout_mirror"]
        self.assertTrue(receipt["applied"])
        self.assertEqual(receipt["result"]["snapshot_id"], "snapshot-1")


if __name__ == "__main__":
    unittest.main()
