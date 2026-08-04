from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_rule_observer import build_tool_event, read_recent_events, write_event
from workflow_observation_iteration import (
    build_experiment_shadow,
    derive_candidates,
    evaluate_disposition_stability,
    preview_business_outcomes,
    preview_efficiency_cycles,
    record_business_outcomes,
    record_efficiency_cycles,
    run,
    score_candidate_priority,
)
from workflow_review_queue import snapshot


def event(session: str, turn: str, call: str, tool: str, category_command: str, ok: bool) -> dict:
    return build_tool_event(
        {
            "session_id": session,
            "turn_id": turn,
            "tool_use_id": call,
            "tool_name": tool,
            "tool_input": {"command": category_command},
            "tool_response": {"ok": ok},
        }
    )


class WorkflowObservationIterationTests(unittest.TestCase):
    @staticmethod
    def business_outcome(*, accepted: bool, consumed: bool, suffix: str) -> dict:
        return {
            "category": "system_maintenance",
            "accepted": accepted,
            "consumed": consumed,
            "result_ref": f"/private/business/{suffix}",
            "evidence_refs": [f"/private/evidence/{suffix}"],
        }

    @staticmethod
    def efficiency_segment(*, approvals: int = 1, governance_tax_ms: int = 600) -> dict:
        return {
            "task_segment_class": "authorized_repository_change",
            "active_execution_ms": 1200,
            "tool_wait_ms": 300,
            "user_wait_ms": 700,
            "idle_gap_ms": 0,
            "rework_ms": 200,
            "approval_round_trip_count": approvals,
            "clarification_round_trip_count": 0,
            "first_pass": False,
            "governance_tax_ms": governance_tax_ms,
            "measurement_confidence": "high",
            "timeline_evidence_ref": "owner-receipt:timeline",
        }

    def test_business_outcomes_are_redacted_deduplicated_and_do_not_enter_queue_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            first = record_business_outcomes([self.business_outcome(accepted=False, consumed=False, suffix="a")], task_ref="business-a", runtime_root=root)
            replay = record_business_outcomes([self.business_outcome(accepted=False, consumed=False, suffix="a")], task_ref="business-a", runtime_root=root)
            events = read_recent_events(runtime_root=root)
        self.assertTrue(first["ok"] and replay["ok"])
        self.assertFalse(first["writes_business_state"])
        self.assertFalse(first["writes_review_queue"])
        self.assertEqual(len(events), 1)
        self.assertNotIn("/private/business/a", str(events))
        self.assertNotIn("/private/evidence/a", str(events))

    def test_business_degradation_requires_cross_task_repetition_and_excludes_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            for task_ref, suffix in (("business-a", "a"), ("business-b", "b"), ("business-c", "c")):
                recorded = record_business_outcomes([self.business_outcome(accepted=False, consumed=False, suffix=suffix)], task_ref=task_ref, runtime_root=root)
                self.assertTrue(recorded["ok"], recorded)
            successful = record_business_outcomes([self.business_outcome(accepted=True, consumed=True, suffix="ok")], task_ref="business-d", runtime_root=root)
            candidates = derive_candidates(read_recent_events(runtime_root=root), runtime_root=root)
        self.assertTrue(successful["ok"])
        business = [item for item in candidates if item["attributes"].get("signal_kind") == "business_outcome_degradation"]
        self.assertEqual(len(business), 1)
        self.assertEqual(business[0]["target_namespace"], "memory.project_conclusions")
        self.assertEqual(business[0]["attributes"]["owner"], "memory_governance")
        self.assertEqual(business[0]["attributes"]["distinct_task_count"], 3)

    def test_invalid_business_outcome_never_persists_an_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            result = record_business_outcomes([{"category": "unknown"}], task_ref="business-a", runtime_root=root)
            events = read_recent_events(runtime_root=root)
        self.assertFalse(result["ok"])
        self.assertEqual(events, [])

    def test_business_outcome_preview_is_redacted_and_never_writes_events(self) -> None:
        outcome = self.business_outcome(accepted=True, consumed=True, suffix="preview")
        outcome["task_segment"] = self.efficiency_segment()
        result = preview_business_outcomes([outcome], task_ref="private-preview-task")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["writes_observer_events"])
        self.assertFalse(result["writes_business_state"])
        self.assertFalse(result["writes_review_queue"])
        serialized = str(result)
        self.assertNotIn("private-preview-task", serialized)
        self.assertNotIn("owner-receipt:timeline", serialized)

    def test_efficiency_event_replay_is_idempotent_and_keeps_raw_timeline_private(self) -> None:
        outcome = self.business_outcome(accepted=True, consumed=True, suffix="efficiency")
        outcome["task_segment"] = self.efficiency_segment()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            first = record_business_outcomes([outcome], task_ref="private-task", runtime_root=root)
            replay = record_business_outcomes([outcome], task_ref="private-task", runtime_root=root)
            events = read_recent_events(runtime_root=root)
        self.assertTrue(first["ok"] and replay["ok"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["task_segment_class"], "authorized_repository_change")
        self.assertNotIn("owner-receipt:timeline", str(events))
        self.assertNotIn("private-task", str(events))

    def test_repeated_approval_friction_requires_independent_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            for suffix in ("a", "b", "c"):
                outcome = self.business_outcome(accepted=True, consumed=True, suffix=suffix)
                outcome["task_segment"] = self.efficiency_segment()
                self.assertTrue(record_business_outcomes([outcome], task_ref=f"task-{suffix}", runtime_root=root)["ok"])
            candidates = derive_candidates(read_recent_events(runtime_root=root), runtime_root=root)
        efficiency = [item for item in candidates if item["attributes"].get("signal_kind") == "efficiency_constraint"]
        self.assertEqual(len(efficiency), 1)
        self.assertEqual(efficiency[0]["attributes"]["constraint"], "approval_and_clarification_round_trips")
        self.assertEqual(efficiency[0]["attributes"]["distinct_task_count"], 3)
        self.assertFalse(efficiency[0]["attributes"]["write_authorization_inherited"])

    def test_experiment_shadow_promotes_only_with_repeated_accepted_outcomes(self) -> None:
        events = []
        for suffix in ("a", "b", "c"):
            outcome = self.business_outcome(accepted=True, consumed=True, suffix=suffix)
            outcome["task_segment"] = self.efficiency_segment()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "events"
                self.assertTrue(record_business_outcomes([outcome], task_ref=f"task-{suffix}", runtime_root=root)["ok"])
                events.extend(read_recent_events(runtime_root=root))
        candidates = derive_candidates(events, minimum_occurrences=3, minimum_tasks=2)
        efficiency = next(item for item in candidates if item["attributes"].get("signal_kind") == "efficiency_constraint")
        shadow = efficiency["experiment_shadow"]
        self.assertEqual("eligible", shadow["eligibility"])
        self.assertEqual("would_promote", shadow["would_disposition"])
        self.assertEqual("L1", shadow["autonomy_level"])
        self.assertFalse(shadow["writes_business_state"])
        self.assertFalse(shadow["writes_review_queue"])
        replay = derive_candidates(events, minimum_occurrences=3, minimum_tasks=2)
        replay_efficiency = next(item for item in replay if item["attributes"].get("signal_kind") == "efficiency_constraint")
        self.assertEqual(shadow["input_signature_ref"], replay_efficiency["experiment_shadow"]["input_signature_ref"])
        self.assertEqual(efficiency["candidate_id"], replay_efficiency["candidate_id"])

    def test_experiment_shadow_holds_unknown_acceptance_and_rejects_low_samples(self) -> None:
        unknown = {
            "candidate_id": "candidate:unknown",
            "source_checkpoint": "policy:unknown",
            "attributes": {
                "signal_kind": "efficiency_cycle",
                "owner": "maintenance_owner",
                "distinct_task_count": 2,
                "occurrence_count": 3,
                "measurement_confidence": "high",
                "evidence_signature": "evidence",
            },
        }
        shadow = build_experiment_shadow(unknown)
        self.assertEqual("eligible", shadow["eligibility"])
        self.assertEqual("would_hold", shadow["would_disposition"])

        low_sample = {
            **unknown,
            "candidate_id": "candidate:low-sample",
            "attributes": {**unknown["attributes"], "distinct_task_count": 1},
        }
        low_shadow = build_experiment_shadow(low_sample)
        self.assertEqual("ineligible", low_shadow["eligibility"])
        self.assertIn("independent_task_sample_insufficient", low_shadow["eligibility_reasons"])
        self.assertEqual("would_hold", low_shadow["would_disposition"])

    def test_business_degradation_shadow_is_rollback_advice_only(self) -> None:
        candidate = {
            "candidate_id": "candidate:degraded",
            "source_checkpoint": "policy:degraded",
            "attributes": {
                "signal_kind": "business_outcome_degradation",
                "owner": "maintenance_owner",
                "distinct_task_count": 3,
                "occurrence_count": 3,
                "measurement_confidence": "high",
                "evidence_signature": "evidence",
            },
        }
        shadow = build_experiment_shadow(candidate)
        self.assertEqual("would_rollback", shadow["would_disposition"])
        self.assertTrue(shadow["automatic_remediation"] is False)

    def test_disposition_stability_uses_hysteresis_and_cooldown(self) -> None:
        promote = evaluate_disposition_stability(
            success_rate=0.85,
            sample_count=4,
            independent_task_count=3,
            guardrail_ok=True,
            input_signature_matches=True,
        )
        self.assertEqual("would_promote", promote["disposition"])
        hold = evaluate_disposition_stability(
            success_rate=0.65,
            sample_count=4,
            independent_task_count=3,
            guardrail_ok=True,
            input_signature_matches=True,
        )
        self.assertEqual("would_hold", hold["disposition"])
        self.assertEqual("hysteresis_band", hold["reason"])
        cooldown = evaluate_disposition_stability(
            success_rate=0.95,
            sample_count=4,
            independent_task_count=3,
            guardrail_ok=True,
            input_signature_matches=True,
            cooldown_remaining=1,
        )
        self.assertEqual("cooldown_active", cooldown["reason"])
        self.assertTrue(cooldown["read_only"])

    def test_disposition_stability_fails_closed_on_drift_and_guardrail_failure(self) -> None:
        drift = evaluate_disposition_stability(
            success_rate=0.95,
            sample_count=10,
            independent_task_count=5,
            guardrail_ok=True,
            input_signature_matches=False,
        )
        self.assertEqual("invalidated", drift["disposition"])
        self.assertEqual("input_signature_changed", drift["reason"])
        rollback = evaluate_disposition_stability(
            success_rate=0.95,
            sample_count=10,
            independent_task_count=5,
            guardrail_ok=False,
            input_signature_matches=True,
        )
        self.assertEqual("would_rollback", rollback["disposition"])
        self.assertEqual("guardrail_failed", rollback["reason"])

    def test_disposition_stability_respects_change_budget_and_sample_floor(self) -> None:
        budget = evaluate_disposition_stability(
            success_rate=0.95,
            sample_count=10,
            independent_task_count=5,
            guardrail_ok=True,
            input_signature_matches=True,
            disposition_changes=1,
        )
        self.assertEqual("change_budget_exhausted", budget["reason"])
        sparse = evaluate_disposition_stability(
            success_rate=0.95,
            sample_count=2,
            independent_task_count=1,
            guardrail_ok=True,
            input_signature_matches=True,
        )
        self.assertEqual("sample_window_insufficient", sparse["reason"])

    def test_candidate_priority_prefers_accumulated_avoidable_cost(self) -> None:
        expensive = {
            "candidate_id": "candidate-expensive",
            "attributes": {
                "signal_kind": "efficiency_constraint",
                "measurement_confidence": "high",
                "occurrence_count": 4,
                "distinct_task_count": 3,
                "total_user_wait_ms": 4000,
                "total_governance_tax_ms": 2000,
            },
        }
        frequent_low_cost = {
            "candidate_id": "candidate-frequent",
            "attributes": {
                "signal_kind": "efficiency_cycle",
                "measurement_confidence": "high",
                "occurrence_count": 20,
                "distinct_task_count": 2,
            },
        }
        expensive_score = score_candidate_priority(expensive)
        frequent_score = score_candidate_priority(frequent_low_cost)
        self.assertGreater(expensive_score["net_value_score"], frequent_score["net_value_score"])
        self.assertTrue(expensive_score["read_only"])
        self.assertFalse(expensive_score["automatic_remediation"])

    def test_candidate_priority_fails_closed_on_malformed_cost_fields(self) -> None:
        score = score_candidate_priority(
            {
                "candidate_id": "candidate-malformed",
                "attributes": {
                    "measurement_confidence": "high",
                    "occurrence_count": "unknown",
                    "distinct_task_count": "NaN",
                    "total_user_wait_ms": object(),
                },
            }
        )
        self.assertEqual(0, score["frequency"])
        self.assertEqual(0, score["gross_avoidable_cost_ms"])
        self.assertEqual(0.0, score["net_value_score"])

    def test_candidate_priority_order_is_deterministic_and_preserves_identity(self) -> None:
        candidates = [
            {
                "candidate_id": "candidate-low",
                "attributes": {"measurement_confidence": "medium", "occurrence_count": 3, "distinct_task_count": 2},
            },
            {
                "candidate_id": "candidate-high",
                "attributes": {
                    "measurement_confidence": "high",
                    "occurrence_count": 3,
                    "distinct_task_count": 2,
                    "total_user_wait_ms": 9000,
                },
            },
        ]
        from workflow_observation_iteration import project_candidate_experiments

        first = project_candidate_experiments(candidates)
        replay = project_candidate_experiments(list(reversed(candidates)))
        self.assertEqual(["candidate-high", "candidate-low"], [item["candidate_id"] for item in first])
        self.assertEqual([item["candidate_id"] for item in first], [item["candidate_id"] for item in replay])
        self.assertTrue(all("priority_projection" in item for item in first))

    def test_efficiency_candidate_excludes_low_confidence_and_single_task_replay(self) -> None:
        outcome = self.business_outcome(accepted=True, consumed=True, suffix="same")
        outcome["task_segment"] = self.efficiency_segment()
        low = self.business_outcome(accepted=True, consumed=True, suffix="low")
        low["task_segment"] = {**self.efficiency_segment(), "measurement_confidence": "low"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            for _ in range(3):
                self.assertTrue(record_business_outcomes([outcome], task_ref="one-task", runtime_root=root)["ok"])
            self.assertTrue(record_business_outcomes([low], task_ref="low-task", runtime_root=root)["ok"])
            candidates = derive_candidates(read_recent_events(runtime_root=root), runtime_root=root)
        self.assertFalse(any(item["attributes"].get("signal_kind") == "efficiency_constraint" for item in candidates))

    def test_efficiency_cycle_preview_is_read_only_and_record_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            rollout = base / "rollout.jsonl"
            rollout.write_text(
                "".join(
                    '{"type":"response_item","payload":{"type":"function_call","name":"wait_threads"}}\n'
                    for _ in range(3)
                ),
                encoding="utf-8",
            )
            root = base / "events"
            preview = preview_efficiency_cycles(rollout_path=rollout, task_ref="private-task")
            self.assertFalse(root.exists())
            first = record_efficiency_cycles(rollout_path=rollout, task_ref="private-task", runtime_root=root)
            replay = record_efficiency_cycles(rollout_path=rollout, task_ref="private-task", runtime_root=root)
            events = read_recent_events(runtime_root=root)
        self.assertTrue(preview["ok"] and first["ok"] and replay["ok"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "EfficiencyCycle")
        self.assertNotIn("private-task", str(events))

    def test_efficiency_cycle_candidate_requires_cross_task_occurrences(self) -> None:
        events = []
        for task_key, count in (("task-a", 2), ("task-b", 2)):
            events.append({
                "schema": "codex_rule_observer.event.v1",
                "event": "EfficiencyCycle",
                "event_id": f"event-{task_key}",
                "task_key": task_key,
                "session_id": task_key,
                "turn_id": "validation_replay",
                "cycle_type": "validation_replay",
                "occurrence_count": count,
                "measurement_confidence": "high",
            })
        candidates = derive_candidates(events, minimum_occurrences=3, minimum_tasks=2)
        cycle = [item for item in candidates if item["attributes"].get("signal_kind") == "efficiency_cycle"]
        self.assertEqual(len(cycle), 1)
        self.assertEqual(cycle[0]["attributes"]["occurrence_count"], 4)
        self.assertFalse(cycle[0]["attributes"]["business_acceptance_inferred"])

    def test_insufficient_samples_do_not_produce_candidate(self) -> None:
        events = [event("s", "t", "1", "Bash", "apply_patch", True)]
        self.assertEqual(derive_candidates(events), [])

    def test_repeated_tool_failure_has_stable_identity(self) -> None:
        events = [
            event("s", f"t-{index % 3}", str(index), "mcp__owner__call", "", index >= 3)
            for index in range(5)
        ]
        first = derive_candidates(events)
        replay = derive_candidates([*events, event("s", "t-4", "6", "mcp__owner__call", "", False)])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["candidate_id"], replay[0]["candidate_id"])

    def test_apply_replays_one_pending_review_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "events"
            queue = Path(tmp) / "review.sqlite"
            for index in range(5):
                write_event(
                    event("s", f"t-{index % 3}", str(index), "mcp__owner__call", "", index >= 3),
                    root,
                )
            first = run(apply=True, confirm="APPLY-OBSERVATION-PROPOSALS", runtime_root=root, queue_path=queue)
            replay = run(apply=True, confirm="APPLY-OBSERVATION-PROPOSALS", runtime_root=root, queue_path=queue)
            snap = snapshot(db_path=queue)
        self.assertTrue(first["ok"] and replay["ok"])
        self.assertEqual(len(snap["pending"]), 1)
        self.assertFalse(first["contracts"]["direct_pmb_write"])

    def test_untrusted_tool_label_is_not_copied_to_candidate(self) -> None:
        events = [event("s", f"t-{index % 3}", str(index), "password=not-a-tool", "", False) for index in range(5)]
        candidate = derive_candidates(events)[0]
        self.assertNotIn("password=not-a-tool", candidate["summary"])

    def test_governance_occurrence_count_uses_only_trigger_events(self) -> None:
        events = [
            event("s", "t-1", "w-1", "apply_patch", "apply_patch", True),
            event("s", "t-1", "o-1", "Bash", "echo unrelated", True),
            event("s", "t-2", "w-2", "apply_patch", "apply_patch", True),
            event("s", "t-2", "o-2", "Bash", "echo unrelated", True),
        ]
        self.assertEqual(derive_candidates(events, minimum_occurrences=3), [])


if __name__ == "__main__":
    unittest.main()
