#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import scoped_authorization_policy as policy


class ScopedAuthorizationPolicyTests(unittest.TestCase):
    def low(self) -> dict:
        return {
            "subject": {"id": "codex", "class": "agent"},
            "action": {"name": "local_source_edit", "owner": "work_git_change_owner"},
            "resource": {"kind": "git_worktree", "id": "task-a"},
            "environment": {"class": "local_isolated"},
            "risk": {"level": "R2", "facts": ["reversible", "verified_restore_point"]},
            "costs": {name: 0 for name in policy.load_policy()["cost_dimensions"]},
            "matched_forbids": [],
        }

    def test_r0_r1_r2_low_cost_allow_without_challenge(self) -> None:
        for level in ("R0", "R1", "R2"):
            with self.subTest(level=level):
                assessment = self.low()
                assessment["risk"]["level"] = level
                self.assertEqual(policy.decide_gate(assessment)["decision"], "allow_without_challenge")

    def test_high_risk_or_high_cost_independently_requires_challenge(self) -> None:
        risky = self.low()
        risky["risk"]["level"] = "R3"
        self.assertEqual(policy.decide_gate(risky)["decision"], "challenge_required")
        costly = self.low()
        costly["costs"]["gpu_minutes"] = 15
        result = policy.decide_gate(costly)
        self.assertEqual(result["decision"], "challenge_required")
        self.assertIn("cost:gpu_minutes", result["determining_rules"])

    def test_risk_matrix_and_hard_trigger_preserve_severe_single_facts(self) -> None:
        matrix = self.low()
        matrix["risk"] = {"level": "R1", "severity": "S4", "likelihood": "L0", "facts": []}
        self.assertEqual(policy.decide_gate(matrix)["decision"], "challenge_required")
        hard = self.low()
        hard["risk"] = {"level": "R0", "facts": ["privilege_elevation"]}
        result = policy.decide_gate(hard)
        self.assertEqual(result["risk_evaluation"]["level"], "R3")
        self.assertEqual(result["decision"], "challenge_required")

    def test_every_cost_dimension_uses_less_equal_and_greater_boundaries(self) -> None:
        for name, contract in policy.load_policy()["cost_dimensions"].items():
            threshold = contract["high_threshold"]
            for factor, expected in ((0.99, "allow_without_challenge"), (1, "challenge_required"), (1.01, "challenge_required")):
                with self.subTest(dimension=name, factor=factor):
                    assessment = self.low()
                    assessment["costs"][name] = threshold * factor
                    self.assertEqual(policy.decide_gate(assessment)["decision"], expected)

    def test_cost_uses_maximum_of_actual_estimate_and_upper_bound(self) -> None:
        assessment = self.low()
        assessment["costs"]["network_gib"] = {"actual": 0.1, "estimate": 1, "upper_bound": 3}
        result = policy.decide_gate(assessment)
        self.assertEqual(result["decision"], "challenge_required")
        self.assertEqual(result["cost_evaluation"]["network_gib"]["effective_value"], 3)

    def test_unknown_risk_or_cost_uses_bounded_read_only_preflight(self) -> None:
        unknown_risk = self.low()
        unknown_risk["risk"]["level"] = "unknown"
        result = policy.decide_gate(unknown_risk)
        self.assertEqual(result["decision"], "bounded_preflight")
        self.assertFalse(result["preflight_budget"]["business_side_effects"])
        unknown_cost = self.low()
        unknown_cost["costs"].pop("external_calls")
        self.assertEqual(policy.decide_gate(unknown_cost)["decision"], "bounded_preflight")

    def test_known_high_trigger_wins_over_other_unknowns(self) -> None:
        assessment = self.low()
        assessment["risk"]["level"] = "unknown"
        assessment["costs"]["external_calls"] = 10000
        self.assertEqual(policy.decide_gate(assessment)["decision"], "challenge_required")

    def test_r4_and_explicit_forbid_cannot_be_overridden(self) -> None:
        r4 = self.low()
        r4["risk"]["level"] = "R4"
        self.assertEqual(policy.decide_gate(r4)["decision"], "deny_non_overrideable")
        forbidden = self.low()
        forbidden["matched_forbids"] = ["secret_exfiltration"]
        result = policy.decide_gate(forbidden)
        self.assertEqual(result["decision"], "deny_non_overrideable")
        self.assertIn("forbid:secret_exfiltration", result["determining_rules"])

    def test_owner_may_tighten_but_never_raise_threshold(self) -> None:
        accepted = policy.validate_owner_override({"elapsed_minutes": 10})
        self.assertTrue(accepted["ok"], accepted)
        rejected = policy.validate_owner_override({"elapsed_minutes": 31})
        self.assertFalse(rejected["ok"])
        self.assertIn("elapsed_minutes", rejected["invalid_dimensions"])
        self.assertFalse(policy.validate_owner_override({"unknown_cost": 1})["ok"])

    def test_invalid_policy_and_invalid_assessment_fail_closed(self) -> None:
        result = policy.decide_gate(self.low(), policy_payload={})
        self.assertEqual(result["decision"], "deny_non_overrideable")
        self.assertEqual(result["reason"], "policy_invalid")
        invalid_policy = copy.deepcopy(policy.load_policy())
        invalid_policy["risk"]["challenge_at"] = "R5"
        result = policy.decide_gate(self.low(), policy_payload=invalid_policy)
        self.assertEqual(result["decision"], "deny_non_overrideable")
        self.assertEqual(result["reason"], "policy_invalid")
        invalid_assessment = self.low()
        invalid_assessment["costs"]["elapsed_minutes"] = -1
        result = policy.decide_gate(invalid_assessment)
        self.assertEqual(result["decision"], "deny_non_overrideable")
        self.assertEqual(result["reason"], "assessment_invalid")
        invalid_assessment = self.low()
        invalid_assessment["risk"] = {"level": "R1", "severity": "S9", "likelihood": "L1", "facts": []}
        self.assertEqual(policy.decide_gate(invalid_assessment)["reason"], "assessment_invalid")
        invalid_assessment = self.low()
        invalid_assessment["matched_forbids"] = ["unregistered_forbid"]
        self.assertEqual(policy.decide_gate(invalid_assessment)["reason"], "assessment_invalid")

    def test_policy_rejects_weakened_owner_override_or_preflight_contract(self) -> None:
        weakened = copy.deepcopy(policy.load_policy())
        weakened["owner_override"]["threshold_increase"] = "allow"
        self.assertFalse(policy.validate_policy(weakened)["ok"])
        weakened = copy.deepcopy(policy.load_policy())
        weakened["bounded_preflight"]["business_side_effects"] = True
        self.assertFalse(policy.validate_policy(weakened)["ok"])
        weakened = copy.deepcopy(policy.load_policy())
        weakened["selected_consumers"] = []
        self.assertFalse(policy.validate_policy(weakened)["ok"])

    def test_decision_and_policy_signatures_are_deterministic_and_input_sensitive(self) -> None:
        first = policy.decide_gate(self.low())
        second = policy.decide_gate(self.low())
        self.assertEqual(first["input_signature"], second["input_signature"])
        self.assertEqual(first["policy_signature"], second["policy_signature"])
        changed = self.low()
        changed["costs"]["elapsed_minutes"] = 1
        self.assertNotEqual(first["input_signature"], policy.decide_gate(changed)["input_signature"])

    def test_pdp_is_pure_and_does_not_touch_authorization_runtime(self) -> None:
        with patch.object(Path, "write_text", side_effect=AssertionError("write forbidden")), patch.object(
            Path, "mkdir", side_effect=AssertionError("mkdir forbidden")
        ):
            result = policy.decide_gate(self.low(), policy_payload=policy.load_policy())
        self.assertTrue(result["ok"])
        self.assertFalse(result["shadow_only"])
        self.assertEqual("enforce_selected_consumers", result["mode"])

    def test_only_selected_consumer_enforces_and_unmigrated_consumer_fails_closed(self) -> None:
        decision = policy.decide_gate(self.low())
        selected = policy.consumer_enforcement(decision, consumer="execution_route_pack")
        self.assertTrue(selected["enforced"])
        self.assertEqual("allow_without_challenge", selected["selected_enforcement"])
        self.assertFalse(selected["legacy_gate_retained"])

        unmigrated = policy.consumer_enforcement(decision, consumer="work_git_change_owner")
        self.assertFalse(unmigrated["enforced"])
        self.assertEqual("legacy_fail_closed", unmigrated["selected_enforcement"])
        self.assertTrue(unmigrated["legacy_gate_retained"])

    def test_shadow_policy_never_enforces_even_for_named_consumer(self) -> None:
        shadow = copy.deepcopy(policy.load_policy())
        shadow["mode"] = "shadow_only"
        decision = policy.decide_gate(self.low(), policy_payload=shadow)
        enforcement = policy.consumer_enforcement(decision, consumer="execution_route_pack")
        self.assertFalse(enforcement["enforced"])
        self.assertEqual("legacy_fail_closed", enforcement["selected_enforcement"])

    def test_degraded_mode_allows_only_declared_low_risk_read_only_actions(self) -> None:
        allowed = policy.degraded_decision("read_only_local_source", risk_level="R1")
        denied = policy.degraded_decision("local_source_edit", risk_level="R1")
        self.assertEqual(allowed["decision"], "allow_without_challenge")
        self.assertEqual(denied["decision"], "deny_non_overrideable")


if __name__ == "__main__":
    unittest.main()
