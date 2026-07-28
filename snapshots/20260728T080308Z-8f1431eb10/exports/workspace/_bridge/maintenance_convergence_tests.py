#!/usr/bin/env python3

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from maintenance_upgrade_governance import (
    build_convergence_plan,
    build_registry_convergence_plan,
    normalize_maintenance_result,
    validate_dependency_graph,
)
from maintenance_convergence_runtime import (
    build_lifecycle_projection,
    load_plan,
    load_terminal_receipts,
    lifecycle_status,
    normalize_signals,
    persist_plan,
    route_result,
)
from workflow_review_queue import snapshot as review_snapshot


def node(
    node_id: str,
    *,
    dependencies: list[str] | None = None,
    reverse_validation: list[str] | None = None,
    cost: int = 100,
    conflict_group: str = "",
) -> dict:
    return {
        "node_id": node_id,
        "capability_id": node_id.split(":", 1)[0],
        "action": node_id.split(":", 1)[-1],
        "dependencies": dependencies or [],
        "reverse_validation": reverse_validation or [],
        "estimated_cost_ms": cost,
        "timeout_seconds": 30,
        "conflict_group": conflict_group,
        "independent_group": "read-only",
        "automation_level": "A0",
        "effect_class": "observe",
        "freshness_ttl_seconds": 900,
        "owner_contract_fingerprint": "owner-v1",
    }


class MaintenanceConvergenceTests(unittest.TestCase):
    def test_graph_reports_shortest_dependency_cycle(self) -> None:
        result = validate_dependency_graph(
            [
                node("a:validate", dependencies=["b:validate"]),
                node("b:validate", dependencies=["a:validate"]),
                node("c:validate", dependencies=["a:validate"]),
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "dependency_cycle")
        self.assertEqual(result["cycle"], ["a:validate", "b:validate", "a:validate"])

    def test_graph_rejects_missing_dependency_with_actionable_node(self) -> None:
        result = validate_dependency_graph([node("a:validate", dependencies=["missing:validate"])])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "dependency_missing")
        self.assertEqual(result["missing_dependencies"][0]["node_id"], "a:validate")

    def test_plan_reuses_matching_terminal_receipt_and_selects_first_invalid_node(self) -> None:
        nodes = [
            node("a:validate", cost=10),
            node("b:validate", dependencies=["a:validate"], cost=20),
        ]
        first = build_convergence_plan(
            intent="maintain workflow",
            nodes=nodes,
            root_node_ids=["b:validate"],
            source_generation="source-1",
            receipts=[],
        )
        a_signature = next(item["input_signature"] for item in first["nodes"] if item["node_id"] == "a:validate")
        second = build_convergence_plan(
            intent="maintain workflow",
            nodes=nodes,
            root_node_ids=["b:validate"],
            source_generation="source-1",
            receipts=[
                {
                    "node_id": "a:validate",
                    "input_signature": a_signature,
                    "status": "healthy",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        self.assertEqual(second["reused_node_ids"], ["a:validate"])
        self.assertEqual(second["next_action"]["node_id"], "b:validate")

    def test_success_receipt_expires_by_owner_ttl_but_failure_does_not_loop(self) -> None:
        current = datetime(2026, 7, 27, tzinfo=timezone.utc)
        nodes = [node("a:validate")]
        first = build_convergence_plan(
            intent="maintain a",
            nodes=nodes,
            root_node_ids=["a:validate"],
            source_generation="source-1",
            receipts=[],
            at=current,
        )
        signature = first["nodes"][0]["input_signature"]
        expired = build_convergence_plan(
            intent="maintain a",
            nodes=nodes,
            root_node_ids=["a:validate"],
            source_generation="source-1",
            receipts=[
                {
                    "node_id": "a:validate",
                    "input_signature": signature,
                    "status": "healthy",
                    "finished_at": (current - timedelta(seconds=901)).isoformat(),
                }
            ],
            at=current,
        )
        failed = build_convergence_plan(
            intent="maintain a",
            nodes=nodes,
            root_node_ids=["a:validate"],
            source_generation="source-1",
            receipts=[{"node_id": "a:validate", "input_signature": signature, "status": "failed"}],
            at=current,
        )

        self.assertEqual(expired["status"], "ready")
        self.assertEqual(failed["status"], "blocked")
        self.assertEqual(failed["terminal_failure_node_ids"], ["a:validate"])

    def test_unrelated_source_generation_change_keeps_owner_node_signature_reusable(self) -> None:
        current = datetime(2026, 7, 27, tzinfo=timezone.utc)
        nodes = [node("a:validate")]
        first = build_convergence_plan(
            intent="maintain a",
            nodes=nodes,
            root_node_ids=["a:validate"],
            source_generation="source-1",
            receipts=[],
            at=current,
        )
        signature = first["nodes"][0]["input_signature"]
        second = build_convergence_plan(
            intent="maintain a",
            nodes=nodes,
            root_node_ids=["a:validate"],
            source_generation="source-2",
            receipts=[
                {
                    "node_id": "a:validate",
                    "input_signature": signature,
                    "status": "healthy",
                    "finished_at": current.isoformat(),
                }
            ],
            at=current,
        )

        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(second["reused_node_ids"], ["a:validate"])
        self.assertEqual(second["status"], "complete")

    def test_new_signal_generation_invalidates_root_without_invalidating_reusable_dependency(self) -> None:
        current = datetime(2026, 7, 27, tzinfo=timezone.utc)
        nodes = [node("dependency:snapshot"), node("target:validate", dependencies=["dependency:snapshot"])]
        first = build_convergence_plan(
            intent="maintain target",
            nodes=nodes,
            root_node_ids=["target:validate"],
            source_generation="source-1",
            signal_generation="signal-1",
            receipts=[],
            at=current,
        )
        signatures = {item["node_id"]: item["input_signature"] for item in first["nodes"]}
        receipts = [
            {
                "node_id": node_id,
                "input_signature": signature,
                "status": "healthy",
                "finished_at": current.isoformat(),
            }
            for node_id, signature in signatures.items()
        ]
        second = build_convergence_plan(
            intent="maintain target",
            nodes=nodes,
            root_node_ids=["target:validate"],
            source_generation="source-1",
            signal_generation="signal-2",
            receipts=receipts,
            at=current,
        )

        self.assertEqual(second["reused_node_ids"], ["dependency:snapshot"])
        self.assertEqual(second["next_action"]["node_id"], "target:validate")

    def test_duplicate_owner_node_is_rejected_before_execution(self) -> None:
        result = validate_dependency_graph([node("a:validate"), node("a:validate")])

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_node_id")

    def test_source_generation_change_invalidates_only_selected_closure(self) -> None:
        nodes = [node("a:validate"), node("b:validate", dependencies=["a:validate"]), node("c:validate")]
        plan = build_convergence_plan(
            intent="maintain b",
            nodes=nodes,
            root_node_ids=["b:validate"],
            source_generation="source-2",
            receipts=[],
        )
        self.assertEqual([item["node_id"] for item in plan["nodes"]], ["a:validate", "b:validate"])

    def test_reverse_validation_runs_after_root_node(self) -> None:
        nodes = [node("a:doctor", reverse_validation=["a:validate"]), node("a:validate")]
        plan = build_convergence_plan(
            intent="repair a",
            nodes=nodes,
            root_node_ids=["a:doctor"],
            source_generation="source-1",
            receipts=[],
        )
        self.assertEqual([item["node_id"] for item in plan["nodes"]], ["a:doctor", "a:validate"])
        validation = next(item for item in plan["nodes"] if item["node_id"] == "a:validate")
        self.assertEqual(validation["dependencies"], ["a:doctor"])

    def test_result_normalization_keeps_blocked_out_of_approval_queue(self) -> None:
        blocked = normalize_maintenance_result({"ok": False, "reason": "owner_contract_missing"})
        approval = normalize_maintenance_result(
            {"ok": False, "requires_approval": True, "items": [{"id": "change-1", "risk": "high"}]}
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(blocked["enqueue_review"])
        self.assertEqual(approval["status"], "approval_required")
        self.assertTrue(approval["enqueue_review"])

    def test_plan_artifact_round_trip_uses_stable_plan_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "schema": "maintenance_convergence_plan.v1",
                "ok": True,
                "plan_id": "maintenance-plan:test",
                "status": "ready",
                "source_generation": "source-1",
            }
            persisted = persist_plan(plan, state_root=root)
            loaded = load_plan(plan["plan_id"], state_root=root)

        self.assertTrue(persisted["ok"])
        self.assertEqual(loaded["plan_id"], plan["plan_id"])
        self.assertTrue(loaded["derived_runtime"])

    def test_result_event_becomes_reusable_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = route_result(
                {"ok": True, "owner": "test-owner"},
                plan_id="maintenance-plan:test",
                node_id="owner:validate",
                input_signature="signature-1",
                state_root=root,
            )
            receipts = load_terminal_receipts(state_root=root)

        self.assertTrue(result["ok"])
        self.assertEqual(
            receipts,
            [
                {
                    "node_id": "owner:validate",
                    "input_signature": "signature-1",
                    "status": "healthy",
                    "recorded_at": receipts[0]["recorded_at"],
                    "artifact_ref": "",
                }
            ],
        )

    def test_mirror_output_is_not_a_source_maintenance_signal(self) -> None:
        result = normalize_signals(["workflow.failed", "mirror.snapshot.created", "mirror.output.ready"])

        self.assertEqual(result["accepted"], ["workflow.failed"])
        self.assertEqual(len(result["rejected"]), 2)

    def test_incomplete_approval_evidence_is_blocked_without_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "review.sqlite"
            result = route_result(
                {"requires_approval": True, "items": [{"object": "config"}]},
                plan_id="maintenance-plan:test",
                node_id="owner:repair",
                db_path=db_path,
                state_root=root,
            )
            queue = review_snapshot(db_path=db_path)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["enqueue_review"])
        self.assertEqual(len(queue["pending"]), 0)

    def test_complete_approval_evidence_enters_existing_queue_and_prepares_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "review.sqlite"
            result = route_result(
                {
                    "requires_approval": True,
                    "items": [
                        {
                            "object": "workflow policy proposal",
                            "risk": "high",
                            "recommended_action": "approve the named proposal",
                            "alternatives": ["defer", "reject"],
                            "evidence_ref": "receipt:test",
                        }
                    ],
                },
                plan_id="maintenance-plan:test",
                node_id="owner:repair",
                db_path=db_path,
                state_root=root,
            )
            queue = review_snapshot(db_path=db_path)

        self.assertTrue(result["ok"])
        self.assertTrue(result["immediate_handoff"])
        self.assertEqual(len(queue["pending"]), 1)

    def test_specific_intent_does_not_expand_on_generic_system_terms(self) -> None:
        plan = build_registry_convergence_plan(
            intent="maintain workflow review queue",
            system="workflow",
            root_limit=32,
        )

        self.assertTrue(plan["ok"])
        self.assertTrue(plan["selection"]["matched_by_intent"])
        self.assertLess(plan["selection"]["selected_root_count"], 16)

    def test_lifecycle_projection_uses_authority_evidence_not_legacy_name_matching(self) -> None:
        plan = {
            "plan_id": "maintenance-plan:test",
            "coverage": {
                "member_dispositions": [
                    {"system": "workflow", "member_id": "workflow.legacy-name-but-active", "disposition": "direct_capability"},
                    {"system": "startup", "member_id": "startup.needs-use", "disposition": "direct_capability"},
                ],
                "underutilized_members": ["startup.needs-use"],
                "unmapped_members": ["unknown.orphan"],
                "unmapped_systems": [],
            },
        }
        projection = build_lifecycle_projection(
            plan,
            tombstones=[{"system": "mcp", "member_id": "mcp.retired", "lifecycle": "decommissioned", "owner": "mcp-owner"}],
        )

        self.assertEqual("active_effective", projection["items"][0]["classification"])
        self.assertEqual("adapt_required", projection["items"][1]["classification"])
        self.assertEqual("blocked_unclassified", projection["items"][2]["classification"])
        self.assertEqual("historical_evidence_only", projection["items"][3]["classification"])
        self.assertEqual([], projection["retirement_candidates"])
        self.assertEqual("membership_and_maintenance_authorities_only", projection["classification_basis"])

    def test_lifecycle_status_refreshes_coverage_without_restarting_shadow_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persist_plan({
                "schema": "maintenance_convergence_plan.v1",
                "ok": True,
                "plan_id": "maintenance-plan:stale",
                "status": "ready",
                "source_generation": "old",
                "coverage": {"underutilized_members": ["old.member"]},
            }, state_root=root)
            current_coverage = {
                "member_dispositions": [
                    {"system": "startup", "member_id": "current.member", "disposition": "direct_capability"}
                ],
                "underutilized_members": [],
                "unmapped_members": [],
            }
            with (
                patch("maintenance_capability_registry.parse_surface_map", return_value=[{"capability_id": "current"}]),
                patch("maintenance_capability_registry.global_coverage", return_value=current_coverage),
                patch("maintenance_capability_registry.source_signature", return_value="current"),
            ):
                result = lifecycle_status("maintenance-plan:stale", state_root=root)

        self.assertTrue(result["plan_stale"])
        self.assertEqual("live_authority_readback", result["coverage_freshness"])
        self.assertEqual("current.member", result["items"][0]["member_id"])
        self.assertEqual(0, result["counts"]["adapt_required"])


if __name__ == "__main__":
    unittest.main()
