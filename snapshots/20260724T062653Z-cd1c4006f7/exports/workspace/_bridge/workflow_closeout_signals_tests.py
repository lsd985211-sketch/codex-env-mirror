#!/usr/bin/env python3

import unittest
from unittest.mock import patch

import codex_environment_mirror
import codex_workflow_entry
import work_git_change_owner
import workflow_closeout_signals as signals
import wsl_workspace_owner


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

    def test_mirror_refresh_expands_work_git_and_worktree_roots(self) -> None:
        with patch.object(
            signals,
            "membership_mirror_change_roots",
            return_value=["work_git:codex-home/", "worktree:WSL_WORKSPACE.md"],
        ):
            self.assertTrue(signals.mirror_refresh_required(["codex-home/config.wsl.template.toml"]))
            self.assertTrue(signals.mirror_refresh_required(["WSL_WORKSPACE.md"]))
            self.assertFalse(signals.mirror_refresh_required(["docs/project-report.md"]))

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
        publish.assert_called_once_with(
            codex_environment_mirror.PUBLISH_CONFIRMATION,
            changed_paths=["_bridge/workflow_finalization.py"],
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["post_closeout_mirror"]["result"]["snapshot_id"], "snapshot-1")
        self.assertEqual(payload["post_closeout_mirror"]["ordering"], "finalization_and_owner_checks_then_mirror_publish")

    def test_post_closeout_commits_generated_finalization_before_publish(self) -> None:
        events: list[str] = []
        handoff = {"ok": True, "paths": ["workspace/_bridge/shared/checkpoints/demo.md"]}
        with (
            patch.object(signals, "commit_generated_finalization", side_effect=lambda _payload: events.append("commit") or handoff),
            patch.object(codex_environment_mirror, "publish", side_effect=lambda *_args, **_kwargs: events.append("publish") or {"ok": True}),
        ):
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["workspace/_bridge/example.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(["commit", "publish"], events)

    def test_post_closeout_reuses_pre_owner_git_handoff(self) -> None:
        finalization = {
            "ok": True,
            "pre_owner_reconciliation": {
                "ok": True,
                "work_git_finalization": {
                    "ok": True,
                    "required": True,
                    "paths": ["workspace/_bridge/shared/checkpoints/demo.md"],
                },
            },
        }
        with (
            patch.object(signals, "commit_generated_finalization") as commit,
            patch.object(codex_environment_mirror, "publish", return_value={"ok": True}) as publish,
        ):
            payload = signals.apply_post_closeout_mirror(
                finalization,
                changed_files=["workspace/_bridge/example.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )

        commit.assert_not_called()
        publish.assert_called_once_with(
            codex_environment_mirror.PUBLISH_CONFIRMATION,
            changed_paths=[
                "workspace/_bridge/example.py",
                "workspace/_bridge/shared/checkpoints/demo.md",
            ],
        )
        self.assertTrue(payload["ok"])

    def test_prepare_closeout_commits_then_reconciles_projection(self) -> None:
        events: list[str] = []
        with (
            patch.object(
                signals,
                "commit_generated_finalization",
                side_effect=lambda _payload: events.append("commit") or {"ok": True, "required": True, "paths": ["checkpoint.md"]},
            ),
            patch.object(
                signals,
                "reconcile_host_projection",
                side_effect=lambda *_args, **_kwargs: events.append("projection") or {"ok": True, "applied": True},
            ),
        ):
            payload = signals.prepare_closeout_state(
                {"ok": True},
                changed_files=["workspace/_bridge/local_mcp_hub.py"],
                apply=True,
                outcome="ok",
            )

        self.assertEqual(["commit", "projection"], events)
        self.assertTrue(payload["pre_owner_reconciliation"]["ok"])
        self.assertEqual(["checkpoint.md"], payload["pre_owner_reconciliation"]["paths"])

    def test_prepare_closeout_stops_before_projection_when_git_handoff_fails(self) -> None:
        with (
            patch.object(signals, "commit_generated_finalization", return_value={"ok": False, "next_action": "repair sync"}),
            patch.object(signals, "reconcile_host_projection") as projection,
        ):
            payload = signals.prepare_closeout_state(
                {"ok": True},
                changed_files=["workspace/_bridge/local_mcp_hub.py"],
                apply=True,
                outcome="ok",
            )

        projection.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual("generated_finalization_git_handoff_failed", payload["blocked_reason"])

    def test_projection_reconciliation_uses_full_owner_apply_for_impacted_source(self) -> None:
        plan = {"ok": True, "would_change": True}
        result = {"ok": True, "applied": True, "projected_files": ["host_compatibility:_bridge/local_mcp_hub.py"]}
        with (
            patch.object(signals.subprocess, "run", return_value=type("Proc", (), {"returncode": 0, "stdout": "main\n", "stderr": ""})()),
            patch.object(wsl_workspace_owner, "host_compatibility_projection_plan", return_value=plan),
            patch.object(wsl_workspace_owner, "host_compatibility_projection_apply", return_value=result) as apply_projection,
        ):
            payload = signals.reconcile_host_projection(
                ["workspace/_bridge/local_mcp_hub.py"],
                apply=True,
            )

        apply_projection.assert_called_once_with(confirm=wsl_workspace_owner.HOST_PROJECTION_CONFIRM)
        self.assertTrue(payload["ok"])
        self.assertEqual("reconciled", payload["reason"])

    def test_projection_reconciliation_defers_outside_main(self) -> None:
        with patch.object(signals.subprocess, "run", return_value=type("Proc", (), {"returncode": 0, "stdout": "codex/task/demo\n", "stderr": ""})()), \
                patch.object(wsl_workspace_owner, "host_compatibility_projection_plan") as plan:
            payload = signals.reconcile_host_projection(
                ["workspace/_bridge/local_mcp_hub.py"],
                apply=True,
            )

        plan.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertEqual("deferred_to_main_integration_closeout", payload["reason"])

    def test_projection_reconciliation_blocks_when_branch_query_fails(self) -> None:
        process = type("Proc", (), {"returncode": 1, "stdout": "", "stderr": "not a git repository"})()
        with patch.object(signals.subprocess, "run", return_value=process):
            payload = signals.reconcile_host_projection(
                ["workspace/_bridge/local_mcp_hub.py"],
                apply=True,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual("work_git_branch_query_failed", payload["reason"])

    def test_post_closeout_blocks_publish_when_generated_git_handoff_fails(self) -> None:
        with (
            patch.object(signals, "commit_generated_finalization", return_value={"ok": False, "reason": "sync_failed"}),
            patch.object(codex_environment_mirror, "publish") as publish,
        ):
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["workspace/_bridge/example.py"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
            )

        publish.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual("generated_finalization_git_handoff_failed", payload["blocked_reason"])

    def test_reused_checkpoint_requires_no_git_handoff(self) -> None:
        finalization = {
            "project_checkpoint": {
                "applied": True,
                "result": {
                    "reused": True,
                    "checkpoint": {
                        "workspace_relative_path": "_bridge/shared/checkpoints/demo/existing.md",
                    },
                },
            },
        }

        self.assertEqual([], signals.generated_finalization_paths(finalization))
        self.assertEqual(
            {
                "schema": "workflow_closeout.finalization_git_handoff.v1",
                "ok": True,
                "required": False,
                "paths": [],
            },
            signals.commit_generated_finalization(finalization),
        )

    def test_generated_finalization_rejects_foreign_worktree_changes(self) -> None:
        finalization = {
            "project_checkpoint": {
                "applied": True,
                "result": {
                    "checkpoint": {
                        "checkpoint_id": "checkpoint-demo",
                        "title": "Demo closeout",
                        "workspace_relative_path": "_bridge/shared/checkpoints/demo/new.md",
                    },
                },
            },
        }
        with (
            patch.object(
                work_git_change_owner,
                "commit_plan",
                return_value={"ok": True, "foreign_change_count": 1},
            ),
            patch.object(work_git_change_owner, "commit_change_set") as commit,
            patch.object(work_git_change_owner, "sync_branch") as sync,
        ):
            result = signals.commit_generated_finalization(finalization)

        self.assertFalse(result["ok"])
        self.assertEqual("generated_paths_not_the_only_work_git_changes", result["reason"])
        commit.assert_not_called()
        sync.assert_not_called()

    def test_generated_finalization_commits_before_bare_git_sync(self) -> None:
        finalization = {
            "project_checkpoint": {
                "applied": True,
                "result": {
                    "checkpoint": {
                        "checkpoint_id": "checkpoint-demo",
                        "title": "Demo closeout",
                        "workspace_relative_path": "_bridge/shared/checkpoints/demo/new.md",
                    },
                },
            },
        }
        events: list[str] = []
        with (
            patch.object(
                work_git_change_owner,
                "commit_plan",
                return_value={"ok": True, "foreign_change_count": 0},
            ),
            patch.object(
                work_git_change_owner,
                "commit_change_set",
                side_effect=lambda *_args, **_kwargs: events.append("commit") or {"ok": True},
            ),
            patch.object(
                work_git_change_owner,
                "sync_branch",
                side_effect=lambda *_args, **_kwargs: events.append("sync") or {"ok": True},
            ),
        ):
            result = signals.commit_generated_finalization(finalization)

        self.assertTrue(result["ok"])
        self.assertEqual(["commit", "sync"], events)
        self.assertEqual(
            [
                "workspace/_bridge/shared/checkpoints/demo/new.md",
                "workspace/_bridge/shared/checkpoints/MANIFEST.md",
            ],
            result["paths"],
        )

    def test_single_active_workspace_thread_auto_selects_with_message_receipt(self) -> None:
        handoff = signals.concurrent_closeout_handoff(
            current_task_complete=True,
            active_workspace_threads=["thread-active"],
            handoff_receipts=['{"threadId":"thread-active"}'],
        )
        self.assertTrue(handoff["ok"])
        self.assertTrue(handoff["complete"])
        self.assertEqual(handoff["target_thread"], "thread-active")
        self.assertTrue(handoff["mirror_delegated"])
        self.assertTrue(handoff["milestone_delegated"])
        self.assertEqual(
            handoff["publication_order"],
            [
                "publish_one_final_recovery_mirror",
                "run_release_plan_after_publish",
                "run_contract_review_before_milestone_when_required",
                "create_milestone_only_when_release_plan_recommends_or_user_approves",
            ],
        )
        self.assertIn("first publishes one final recovery mirror", handoff["next_action"])
        self.assertIn("after publish, run release-plan", handoff["next_action"])

    def test_multiple_active_workspace_threads_require_explicit_target(self) -> None:
        handoff = signals.concurrent_closeout_handoff(
            current_task_complete=True,
            active_workspace_threads=["thread-a", "thread-b"],
            handoff_receipts=['{"threadId":"thread-a"}'],
        )
        self.assertFalse(handoff["ok"])
        self.assertEqual(handoff["blockers"][0]["code"], "handoff_target_required")

    def test_handoff_prevents_competing_post_closeout_publish(self) -> None:
        handoff = signals.concurrent_closeout_handoff(
            current_task_complete=True,
            active_workspace_threads=["thread-active"],
            handoff_receipts=["thread_id=thread-active"],
        )
        with patch.object(codex_environment_mirror, "publish") as publish:
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["AGENTS.md"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
                concurrent_handoff=handoff,
            )
        publish.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["post_closeout_mirror"]["delegated"])
        self.assertEqual(payload["post_closeout_mirror"]["target_thread"], "thread-active")

    def test_missing_handoff_receipt_blocks_local_closeout_and_publish(self) -> None:
        handoff = signals.concurrent_closeout_handoff(
            current_task_complete=True,
            active_workspace_threads=["thread-active"],
        )
        with patch.object(codex_environment_mirror, "publish") as publish:
            payload = signals.apply_post_closeout_mirror(
                {"ok": True},
                changed_files=["AGENTS.md"],
                apply=True,
                outcome="ok",
                owner_checks_ok=True,
                concurrent_handoff=handoff,
            )
        publish.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blocked_reason"], "concurrent_handoff_incomplete")

    def test_closeout_blocks_missing_handoff_receipt_without_mirror_change(self) -> None:
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
                patch.object(codex_workflow_entry, "self_update_closeout_signal", return_value={"checked": True, "ok": True}), \
                patch.object(codex_workflow_entry, "capture_iteration_candidates", return_value={}), \
                patch.object(codex_workflow_entry, "build_closeout_package", side_effect=package), \
                patch.object(codex_workflow_entry, "sync_review_groups", return_value=[]), \
                patch.object(codex_workflow_entry, "build_review_summary", return_value={}):
            payload = codex_workflow_entry.closeout(
                task_kind="documentation",
                outcome="ok",
                active_workspace_thread=["thread-active"],
                finalization_changed_file=["docs/project-report.md"],
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["finalization"]["blocked_reason"], "concurrent_handoff_incomplete")

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

        def prepare(finalization, **_kwargs):
            events.append("prepare")
            return {**finalization, "pre_owner_reconciliation": {"ok": True}}

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
                patch.object(codex_workflow_entry, "prepare_closeout_state", side_effect=prepare), \
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

        self.assertEqual(events, ["finalization", "prepare", "self_update", "mirror"])
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

    def test_closeout_projection_preserves_pre_owner_reconciliation(self) -> None:
        payload = codex_workflow_entry.closeout_cli_projection({
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": True,
            "status": {"outcome": "ok"},
            "finalization": {
                "ok": True,
                "pre_owner_reconciliation": {
                    "schema": "workflow_closeout.pre_owner_reconciliation.v1",
                    "ok": True,
                    "applied": True,
                    "reason": "complete",
                    "work_git_finalization": {"ok": True, "required": True, "paths": ["checkpoint.md"]},
                    "host_projection": {"ok": True, "required": True, "applied": True, "reason": "reconciled"},
                },
            },
        })
        receipt = payload["finalization"]["pre_owner_reconciliation"]
        self.assertTrue(receipt["applied"])
        self.assertEqual("reconciled", receipt["host_projection"]["reason"])

    def test_closeout_projection_preserves_concurrent_handoff(self) -> None:
        payload = codex_workflow_entry.closeout_cli_projection({
            "schema": "codex_workflow_entry.closeout.v2",
            "ok": True,
            "status": {"outcome": "ok"},
            "finalization": {
                "ok": True,
                "concurrent_handoff": {
                    "schema": "workflow_closeout.concurrent_handoff.v1",
                    "ok": True,
                    "required": True,
                    "complete": True,
                    "target_thread": "thread-active",
                    "mirror_delegated": True,
                    "milestone_delegated": True,
                },
            },
        })
        handoff = payload["finalization"]["concurrent_handoff"]
        self.assertTrue(handoff["complete"])
        self.assertEqual(handoff["target_thread"], "thread-active")


if __name__ == "__main__":
    unittest.main()
