#!/usr/bin/env python3

import unittest
from threading import Barrier
from unittest.mock import patch

import workflow_orchestrator
import workflow_validation
from workflow_validation_observation import ValidationObservation


class ValidationObservationTests(unittest.TestCase):
    def test_skill_context_signature_ignores_refresh_timestamp_but_tracks_skill_changes(self) -> None:
        first = {
            "lifecycle_refresh": {
                "generated_at": "2026-07-31T19:00:00+08:00",
                "records": [{"name": "workflow-automator", "sha256": "skill-v1"}],
            },
            "inventory": ["workflow-automator"],
        }
        second = {
            "lifecycle_refresh": {
                "generated_at": "2026-07-31T19:01:00+08:00",
                "records": [{"name": "workflow-automator", "sha256": "skill-v1"}],
            },
            "inventory": ["workflow-automator"],
        }
        changed = {
            "lifecycle_refresh": {
                "generated_at": "2026-07-31T19:01:00+08:00",
                "records": [{"name": "workflow-automator", "sha256": "skill-v2"}],
            },
            "inventory": ["workflow-automator"],
        }
        self.assertEqual(
            workflow_orchestrator.stable_skill_context_signature(first),
            workflow_orchestrator.stable_skill_context_signature(second),
        )
        self.assertNotEqual(
            workflow_orchestrator.stable_skill_context_signature(first),
            workflow_orchestrator.stable_skill_context_signature(changed),
        )

    def test_independent_cli_contracts_run_concurrently_with_stable_result_order(self) -> None:
        barrier = Barrier(4)

        def contract(name: str):
            def run() -> dict:
                barrier.wait(timeout=2)
                return {"ok": True, "cases": {name: {"ok": True}}}

            return run

        with (
            patch.object(workflow_validation, "machine_enum_cli_contract", side_effect=contract("machine")) as machine,
            patch.object(workflow_validation, "online_access_gate_contract", side_effect=contract("online")) as online,
            patch.object(workflow_validation, "resource_broker_contract", side_effect=contract("broker")) as broker,
            patch.object(workflow_validation, "maintenance_upgrade_governance_contract", side_effect=contract("upgrade")) as upgrade,
        ):
            result = workflow_validation.independent_cli_contracts()

        self.assertEqual(
            ["machine_enum", "online_access_gate", "resource_broker", "maintenance_upgrade"],
            list(result),
        )
        self.assertTrue(all(item["ok"] for item in result.values()))
        for mocked in (machine, online, broker, upgrade):
            mocked.assert_called_once_with()

    def test_independent_cli_contracts_propagates_worker_failure(self) -> None:
        with (
            patch.object(workflow_validation, "machine_enum_cli_contract", side_effect=RuntimeError("contract failed")),
            patch.object(workflow_validation, "online_access_gate_contract", return_value={"ok": True, "cases": {}}),
            patch.object(workflow_validation, "resource_broker_contract", return_value={"ok": True, "cases": {}}),
            patch.object(workflow_validation, "maintenance_upgrade_governance_contract", return_value={"ok": True, "cases": {}}),
        ):
            with self.assertRaisesRegex(RuntimeError, "contract failed"):
                workflow_validation.independent_cli_contracts()

    def test_groups_plan_builds_source_scans_and_bytes_are_observational(self) -> None:
        ticks = iter((1_000_000, 3_500_000, 4_000_000, 9_000_000))
        observation = ValidationObservation(clock_ns=lambda: next(ticks))

        observation.start_group("sample_plans")
        plan = {
            "execution_route_pack": {
                "environment_context": {
                    "maintenance_query_metrics": {"source_scan_count": 1}
                }
            }
        }
        self.assertIs(observation.record_plan(plan), plan)
        observation.finish_group("sample_plans")
        observation.start_group("contracts")
        observation.record_plan({"execution_route_pack": {"environment_context": {}}})
        observation.finish_group("contracts")

        payload = {"schema": "example.validate.v1", "ok": False, "checks": [{"name": "x", "ok": False}]}
        observed = observation.attach(payload)

        self.assertEqual(payload, {"schema": "example.validate.v1", "ok": False, "checks": [{"name": "x", "ok": False}]})
        self.assertFalse(observed["ok"])
        self.assertEqual(observed["checks"], payload["checks"])
        metrics = observed["validation_observation"]
        self.assertEqual(metrics["semantic_group_elapsed_ms"], {"sample_plans": 2.5, "contracts": 5.0})
        self.assertEqual(metrics["plan_build_count"], 2)
        self.assertEqual(metrics["maintenance_source_scan_count"], 1)
        self.assertGreater(metrics["serialized_output_bytes"], 0)
        self.assertEqual(metrics["serialized_output_scope"], "validation_payload_before_observation")
        self.assertTrue(metrics["read_only"])
        self.assertFalse(metrics["enforcement"])

    def test_repeated_or_unfinished_groups_fail_closed(self) -> None:
        observation = ValidationObservation(clock_ns=lambda: 1)
        observation.start_group("one")
        with self.assertRaises(ValueError):
            observation.start_group("one")
        with self.assertRaises(ValueError):
            observation.attach({"ok": True})
        observation.finish_group("one")
        with self.assertRaises(ValueError):
            observation.finish_group("one")

    def test_orchestrator_facade_attaches_observation_after_existing_decisions(self) -> None:
        def fake_plan(_message: str, **_kwargs):
            return {
                "domains": [],
                "profile": {},
                "tools": {"codegraph_policy": {}},
                "retirement_guard": {"status": "clear", "triggered": False},
                "execution_route_pack": {
                    "route_decision": {},
                    "environment_context": {
                        "maintenance_query_metrics": {"source_scan_count": 1},
                        "relevant_systems": [],
                    },
                },
            }

        with (
            patch.object(workflow_orchestrator, "doctor", return_value={"ok": True}),
            patch.object(workflow_orchestrator, "prepare_skill_routing_context", None),
            patch.object(workflow_orchestrator, "VALIDATION_SAMPLES", []),
            patch.object(workflow_orchestrator, "build_plan", side_effect=fake_plan),
            patch.object(
                workflow_orchestrator,
                "build_validation_checks",
                return_value=[{"name": "existing", "ok": False}],
            ),
            patch.object(
                workflow_orchestrator,
                "domain_binding_report",
                return_value={"ok": True},
            ),
            patch.object(
                workflow_orchestrator,
                "call_priority_pack",
                return_value={"steps": []},
            ),
            patch.object(workflow_orchestrator, "build_retirement_signal", None),
            patch.object(
                workflow_orchestrator,
                "validate_environment_context",
                return_value={"ok": True, "checks": []},
            ),
        ):
            result = workflow_orchestrator.validate()

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"][0], {"name": "existing", "ok": False})
        observation = result["validation_observation"]
        shadow = result["validation_shadow"]
        self.assertEqual(observation["plan_build_count"], 5)
        self.assertEqual(shadow["plan_build_count"], 5)
        self.assertEqual(shadow["plan_build_count"], observation["plan_build_count"])
        self.assertIn("workflow_orchestrator", {row["source_owner"] for row in shadow["manifest"]})
        self.assertTrue(shadow["read_only"])
        self.assertFalse(shadow["enforcement"])
        self.assertFalse(shadow["cache_enabled"])
        self.assertEqual(observation["maintenance_source_scan_count"], 5)
        self.assertEqual(
            list(observation["semantic_group_elapsed_ms"]),
            [
                "preflight",
                "core_validation_contracts",
                "authority_and_routing_contracts",
                "environment_context_contracts",
            ],
        )
        self.assertFalse(observation["enforcement"])

    def test_compact_cli_projection_preserves_observation_without_changing_counts(self) -> None:
        payload = {
            "schema": "workflow_orchestrator.validate.v1",
            "ok": True,
            "checks": [{"name": "existing", "ok": True}],
            "validation_observation": {
                "schema": "workflow_validation_observation.v1",
                "plan_build_count": 2,
                "read_only": True,
                "enforcement": False,
            },
            "validation_shadow": {
                "schema": "workflow_validation_shadow.v1",
                "ok": True,
                "reason": "",
                "scenario_count": 2,
                "plan_build_count": 2,
                "unique_plan_identity_count": 1,
                "duplicate_plan_build_count": 1,
                "identity_incomplete_count": 0,
                "manifest": [{"scenario_id": "hidden"}],
                "read_only": True,
                "enforcement": False,
                "cache_enabled": False,
                "slo_gate": False,
            },
        }

        projected = workflow_orchestrator.cli_projection(payload, "validate")

        self.assertTrue(projected["ok"])
        self.assertEqual(projected["check_count"], 1)
        self.assertEqual(projected["passed_count"], 1)
        self.assertEqual(
            projected["validation_observation"],
            payload["validation_observation"],
        )
        self.assertEqual(projected["validation_shadow"]["duplicate_plan_build_count"], 1)
        self.assertNotIn("manifest", projected["validation_shadow"])
        full = workflow_orchestrator.cli_projection(payload, "validate", full=True)
        self.assertEqual(full["validation_shadow"]["manifest"], [{"scenario_id": "hidden"}])


if __name__ == "__main__":
    unittest.main()
