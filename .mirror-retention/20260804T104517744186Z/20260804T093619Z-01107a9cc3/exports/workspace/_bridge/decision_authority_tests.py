#!/usr/bin/env python3

import unittest

from decision_authority_policy import classify_decision, project_decisions


class DecisionAuthorityTests(unittest.TestCase):
    def test_explicit_user_goal_overrides_recommendation_without_blocking(self) -> None:
        payload = classify_decision(
            decision_class="recommendation",
            authority_ref="mirror.release_plan.release_recommended",
            condition_satisfied=False,
            user_goal_explicit=True,
            default_action="skip_release",
            requested_action="create_milestone",
        )
        self.assertFalse(payload["blocking"])
        self.assertEqual(payload["effective_outcome"], "allow")
        self.assertTrue(payload["user_override_consumed"])

    def test_recommendation_without_explicit_goal_remains_non_blocking_default(self) -> None:
        payload = classify_decision(
            decision_class="recommendation",
            authority_ref="owner.plan.recommended",
            condition_satisfied=False,
            default_action="skip",
        )
        self.assertFalse(payload["blocking"])
        self.assertEqual(payload["effective_outcome"], "recommend")
        self.assertEqual(payload["next_action"], "skip")

    def test_overrideable_policy_requires_matching_authorization(self) -> None:
        denied = classify_decision(
            decision_class="overrideable_policy",
            authority_ref="mirror.release.authorization",
            condition_satisfied=False,
        )
        allowed = classify_decision(
            decision_class="overrideable_policy",
            authority_ref="mirror.release.authorization",
            condition_satisfied=False,
            authorization_valid=True,
        )
        self.assertTrue(denied["blocking"])
        self.assertEqual(denied["effective_outcome"], "needs_authorization")
        self.assertFalse(allowed["blocking"])
        self.assertTrue(allowed["authorization_consumed"])

    def test_hard_gate_cannot_be_overridden(self) -> None:
        payload = classify_decision(
            decision_class="hard_gate",
            authority_ref="mirror.source_freshness",
            condition_satisfied=False,
            user_goal_explicit=True,
            authorization_valid=True,
        )
        self.assertTrue(payload["blocking"])
        self.assertEqual(payload["effective_outcome"], "deny")
        self.assertEqual(payload["override_authority"], "none")

    def test_ambiguous_fields_do_not_create_authority(self) -> None:
        payload = classify_decision(
            decision_class="",
            authority_ref="owner.output",
            condition_satisfied=False,
            input_context={"severity": "high", "recommended": False, "risk": "high"},
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "explicit_decision_class_and_authority_ref_required")

    def test_projection_keeps_recommendations_out_of_unresolved(self) -> None:
        recommendation = classify_decision(
            decision_class="recommendation",
            authority_ref="plan.recommended",
            condition_satisfied=False,
        )
        policy = classify_decision(
            decision_class="overrideable_policy",
            authority_ref="write.authorization",
            condition_satisfied=False,
        )
        projection = project_decisions([recommendation, policy])
        self.assertEqual(len(projection["recommendations"]), 1)
        self.assertEqual(len(projection["authorization_requirements"]), 1)
        self.assertEqual(projection["unresolved"], [policy])


if __name__ == "__main__":
    unittest.main()
