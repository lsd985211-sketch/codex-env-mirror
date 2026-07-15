#!/usr/bin/env python3

import unittest
from unittest.mock import patch

import codex_environment_mirror
import workflow_closeout_signals as signals


class WorkflowCloseoutSignalsTests(unittest.TestCase):
    def test_mirror_refresh_required_for_mirrored_authority_surfaces(self) -> None:
        self.assertTrue(signals.mirror_refresh_required(["_bridge/workflow_finalization.py"]))
        self.assertTrue(signals.mirror_refresh_required([r"C:\Users\45543\.codex\skills\example\SKILL.md"]))
        self.assertTrue(signals.mirror_refresh_required([r"C:\Users\45543\codex-env-mirror\README.md"]))
        self.assertFalse(signals.mirror_refresh_required(["docs/project-report.md"]))

    def test_post_closeout_mirror_runs_after_successful_finalization(self) -> None:
        finalization = {"ok": True, "project_checkpoint": {"applied": True}}
        owner_result = {"ok": True, "snapshot_id": "snapshot-1"}
        with patch.object(codex_environment_mirror, "refresh", return_value=owner_result) as refresh:
            payload = signals.apply_post_closeout_mirror(
                finalization,
                changed_files=["_bridge/workflow_finalization.py"],
                apply=True,
                outcome="ok",
            )
        refresh.assert_called_once_with(codex_environment_mirror.REFRESH_CONFIRMATION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["post_closeout_mirror"]["result"]["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["post_closeout_mirror"]["ordering"], "finalization_then_mirror_refresh")

    def test_post_closeout_mirror_failure_blocks_finalization(self) -> None:
        with patch.object(codex_environment_mirror, "refresh", return_value={"ok": False, "reason": "failed"}):
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["AGENTS.md"],
                apply=True,
                outcome="ok",
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blocked_reason"], "post_closeout_mirror_refresh_failed")


if __name__ == "__main__":
    unittest.main()
