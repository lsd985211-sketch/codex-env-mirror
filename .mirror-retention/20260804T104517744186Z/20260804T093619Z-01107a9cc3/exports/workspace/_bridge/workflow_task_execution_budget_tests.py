#!/usr/bin/env python3

import unittest

from workflow_task_execution_budget import (
    build_execution_budget,
    execution_budget_input_signature,
    validation_budget,
)


class WorkflowTaskExecutionBudgetTests(unittest.TestCase):
    @staticmethod
    def feedback(*, budget_signature: str, disposition: str = "would_promote", stability_disposition: str = "would_promote", recommended_phase_ids: list[str] | None = None) -> dict:
        return {
            "budget_input_signature_ref": budget_signature,
            "recommended_phase_ids": recommended_phase_ids or ["phase_2_recall", "phase_3_skill_selection", "phase_5_tool_route"],
            "selected_asset_count": 0,
            "experiment_shadow": {
                "schema": "adaptive_efficiency_experiment.v1",
                "eligibility": "eligible",
                "would_disposition": disposition,
                "candidate_ref": "candidate:approval-friction",
                "owner": "maintenance_owner",
                "input_signature_ref": "sha256:evidence",
                "baseline_window": {"independent_task_count": 3, "occurrence_count": 4},
                "sample_policy": {"minimum_tasks": 2, "minimum_occurrences": 3},
                "guardrails": ["semantic_equivalence", "authority_freshness", "accepted_and_consumed_required_for_promote"],
            },
            "stability": {
                "schema": "adaptive_efficiency_stability.v1",
                "disposition": stability_disposition,
                "input_signature_matches": True,
                "guardrail_ok": True,
                "cooldown_remaining": 0,
                "disposition_changes": 0,
                "policy": {"max_disposition_changes": 1},
            },
        }

    def test_read_only_maintenance_uses_quick_instead_of_profile_full(self) -> None:
        result = validation_budget(
            profile="maintenance_governance",
            task_facts={},
        )
        self.assertEqual("quick", result["tier"])
        self.assertEqual("read_only_or_no_material_side_effect", result["reason"])

    def test_each_material_side_effect_requires_full(self) -> None:
        for fact in (
            "local_write", "config_change", "external_write",
            "package_install", "database_write", "system_member_change",
            "secret_or_permission_use", "reload_or_restart_required",
        ):
            with self.subTest(fact=fact):
                result = validation_budget(
                    profile="maintenance_governance", task_facts={fact: True}
                )
                self.assertEqual("full", result["tier"])
                self.assertIn(fact, result["trigger_facts"])

    def test_high_risk_or_destructive_effect_requires_deep(self) -> None:
        self.assertEqual(
            "deep",
            validation_budget(profile="general", task_facts={}, risk="high")["tier"],
        )
        self.assertEqual(
            "deep",
            validation_budget(
                profile="general", task_facts={"destructive_or_high_risk": True}
            )["tier"],
        )

    def test_phase_selection_is_guarded_and_validation_is_enforced(self) -> None:
        phases = [
            {"id": "phase_1_preflight", "enabled": True, "commands": [{"cmd": "one"}]},
            {"id": "phase_4_template_render", "enabled": False, "skip_reason": "no_selected_templates", "commands": []},
        ]
        result = build_execution_budget(
            profile="maintenance_governance", task_facts={}, machine_phases=phases,
            selected_asset_count=2,
        )
        self.assertEqual(["phase_1_preflight"], result["required_phase_ids"])
        self.assertEqual("no_selected_templates", result["skipped_phase_reasons"]["phase_4_template_render"])
        self.assertEqual(1, result["owner_call_budget"])
        self.assertTrue(result["phase_selection_enforced"])
        self.assertTrue(result["validation_tier_enforced"])
        self.assertTrue(result["read_only_projection"])
        self.assertFalse(result["writes_business_state"])

    def test_read_only_no_route_skips_redundant_soft_phases(self) -> None:
        result = build_execution_budget(
            profile="general",
            task_facts={},
            machine_phases=[
                {"id": "phase_1_preflight", "enabled": True, "commands": [{"cmd": "plan"}]},
                {"id": "phase_2_recall", "enabled": True, "commands": [{"cmd": "memory"}]},
                {"id": "phase_3_skill_selection", "enabled": True, "commands": [{"cmd": "skill"}]},
                {"id": "phase_5_tool_route", "enabled": True, "commands": [{"cmd": "route"}]},
                {"id": "phase_7_execution", "enabled": True, "commands": []},
                {"id": "phase_8_validation", "enabled": True, "commands": [{"cmd": "validate"}]},
                {"id": "phase_9_closeout", "enabled": True, "commands": [{"cmd": "closeout"}]},
            ],
        )
        self.assertTrue(result["phase_selection_enforced"])
        self.assertEqual(
            ["phase_2_recall", "phase_3_skill_selection", "phase_5_tool_route", "phase_7_execution"],
            result["phase_selection"]["skip_phase_ids"],
        )
        self.assertEqual(4, result["estimated_saved_work_units"])

    def test_side_effect_or_route_dependency_keeps_soft_phases(self) -> None:
        result = build_execution_budget(
            profile="general",
            task_facts={"local_write": True},
            machine_phases=[
                {"id": "phase_2_recall", "enabled": True, "commands": [{"cmd": "memory"}]},
                {"id": "phase_5_tool_route", "enabled": True, "commands": [{"cmd": "route"}]},
            ],
            route_term_count=1,
        )
        self.assertFalse(result["phase_selection_enforced"])
        self.assertEqual([], result["phase_selection"]["skip_phase_ids"])

    def test_verified_feedback_reduces_repeated_bounded_budget(self) -> None:
        phases = [
            {"id": "phase_1_preflight", "enabled": True, "commands": [{"cmd": "preflight"}]},
            {"id": "phase_2_recall", "enabled": True, "commands": [{"cmd": "recall"}]},
            {"id": "phase_3_skill_selection", "enabled": True, "commands": [{"cmd": "skills"}]},
            {"id": "phase_5_tool_route", "enabled": True, "commands": [{"cmd": "route"}]},
            {"id": "phase_8_validation", "enabled": True, "commands": [{"cmd": "validate"}]},
            {"id": "phase_9_closeout", "enabled": True, "commands": [{"cmd": "closeout"}]},
        ]
        kwargs = {
            "profile": "general", "task_facts": {}, "machine_phases": phases,
            "selected_asset_count": 0, "route_term_count": 0,
            "maintenance_count": 0, "validation_count": 0,
        }
        signature = execution_budget_input_signature(**kwargs)
        result = build_execution_budget(
            **kwargs,
            feedback_projection=self.feedback(budget_signature=signature),
        )
        self.assertEqual("applied", result["adaptive_feedback"]["status"])
        self.assertEqual("adaptive_feedback_promote_consumed", result["adaptive_feedback"]["reason"])
        self.assertEqual(
            {"phase_2_recall", "phase_3_skill_selection", "phase_5_tool_route"},
            set(result["adaptive_feedback"]["applied_phase_ids"]),
        )
        self.assertEqual(3, result["skipped_owner_call_count"])
        self.assertLess(result["owner_call_budget"], result["planned_owner_call_budget"])
        self.assertTrue(result["read_only_projection"])

    def test_feedback_fails_closed_on_signature_drift_stability_or_route_dependency(self) -> None:
        phases = [{"id": "phase_2_recall", "enabled": True, "commands": [{"cmd": "recall"}]}]
        kwargs = {"profile": "general", "task_facts": {}, "machine_phases": phases}
        signature = execution_budget_input_signature(**kwargs)
        drifted = self.feedback(budget_signature="sha256:stale")
        result = build_execution_budget(**kwargs, feedback_projection=drifted)
        self.assertEqual("budget_input_signature_changed", result["adaptive_feedback"]["reason"])
        unstable = self.feedback(budget_signature=signature, stability_disposition="would_hold")
        result = build_execution_budget(**kwargs, feedback_projection=unstable)
        self.assertEqual("stability_not_promotable", result["adaptive_feedback"]["reason"])
        routed = build_execution_budget(
            **kwargs,
            route_term_count=1,
            feedback_projection=self.feedback(
                budget_signature=execution_budget_input_signature(**{**kwargs, "route_term_count": 1}),
                recommended_phase_ids=["phase_5_tool_route"],
            ),
        )
        self.assertEqual("recommended_phase_set_not_applicable", routed["adaptive_feedback"]["reason"])
        self.assertEqual([], routed["adaptive_feedback"]["applied_phase_ids"])


if __name__ == "__main__":
    unittest.main()
