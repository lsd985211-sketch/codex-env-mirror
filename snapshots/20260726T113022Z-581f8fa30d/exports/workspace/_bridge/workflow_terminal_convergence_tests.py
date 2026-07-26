#!/usr/bin/env python3
"""Focused regression tests for bounded terminal convergence."""

from __future__ import annotations

import unittest

from execution_route_pack import build_terminal_convergence_capsule
from workflow_plan_detail import compact_execution_route_pack
from workflow_terminal_convergence import (
    build_convergence_plan,
    invalidate_dependents,
    normalize_action_contract,
    receipt_reuse_key,
    terminal_action_adapter,
    terminal_receipt_for_action,
    terminal_source_signature,
    validate_action_graph,
)


def action(
    action_id: str,
    *,
    phase: str = "authoritative_inputs",
    effect: str = "read_only",
    depends_on: list[str] | None = None,
    authority_facts: list[str] | None = None,
    input_signature: str = "sig",
    approval_required: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "action_id": action_id,
        "owner": f"owner.{action_id}",
        "phase": phase,
        "effect": effect,
        "depends_on": list(depends_on or []),
        "invalidates": [],
        "authority_facts": list(authority_facts or []),
        "input_signature": input_signature,
        "owner_contract_version": "v1",
        "approval": {"required": approval_required, "scope": action_id},
        "receipt_acceptance": ["ok"],
        "verify_entrypoint": f"{action_id} status",
        "verify_effect": "read_only",
    }
    return value


class WorkflowTerminalConvergenceTests(unittest.TestCase):
    def test_owner_action_adapters_cover_similar_terminal_flows(self) -> None:
        cases = {
            "maintenance.index": ("authoritative_inputs", "derived_mutation"),
            "host_projection.apply": ("authoritative_inputs", "derived_mutation"),
            "baseline.adopt": ("authoritative_inputs", "mutation"),
            "checkpoint.write": ("authoritative_inputs", "mutation"),
            "work_git.sync_bare": ("git_stability", "mutation"),
            "mirror.publish": ("external_publish", "mutation"),
            "release.publish": ("external_publish", "mutation"),
            "long_command.consume": ("read_only_acceptance", "read_only"),
        }
        for action_id, expected in cases.items():
            with self.subTest(action_id=action_id):
                adapter = terminal_action_adapter(action_id, input_signature="sig")
                self.assertTrue(adapter["ok"], adapter)
                self.assertEqual((adapter["contract"]["phase"], adapter["contract"]["effect"]), expected)
                self.assertEqual(adapter["contract"]["verify_effect"], "read_only")

    def test_release_adapter_requires_independent_authorization(self) -> None:
        contract = terminal_action_adapter("release.publish", input_signature="sig")["contract"]
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="publish approved release",
            source_state={"head": "abc"},
            actions=[contract],
            receipts=[],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["next_action"]["decision"], "block")
        self.assertEqual(result["next_action"]["reason"], "approval_required")

    def test_terminal_long_command_receipt_is_consumed_without_restart(self) -> None:
        contract = terminal_action_adapter("long_command.consume", input_signature="cmd-sig")["contract"]
        receipt = terminal_receipt_for_action(
            action_id="long_command.consume",
            input_signature="cmd-sig",
            intent_id="i1",
            result={"ok": True, "terminal": True, "exit_code": 0, "status": "completed"},
            ref="artifact:state.json",
        )
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="consume terminal command receipt",
            source_state={"command_signature": "cmd-sig"},
            actions=[contract],
            receipts=[receipt],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["completed_action_ids"], ["long_command.consume"])
        self.assertEqual(result["next_action"]["decision"], "reuse")

    def test_incomplete_long_command_receipt_is_not_accepted(self) -> None:
        receipt = terminal_receipt_for_action(
            action_id="long_command.consume",
            input_signature="cmd-sig",
            intent_id="i1",
            result={"ok": True, "terminal": False, "status": "running"},
        )
        self.assertFalse(receipt["accepted"])

    def test_interrupted_push_resumes_transport_without_restarting_prior_actions(self) -> None:
        checkpoint = terminal_action_adapter("checkpoint.write", input_signature="checkpoint-sig")["contract"]
        sync = terminal_action_adapter(
            "work_git.sync_bare",
            input_signature="sync-sig",
            depends_on=["checkpoint.write"],
            transport_incomplete=True,
        )["contract"]
        checkpoint_receipt = terminal_receipt_for_action(
            action_id="checkpoint.write",
            input_signature="checkpoint-sig",
            intent_id="i1",
            result={"ok": True},
        )
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="stabilize bare Git",
            source_state={"head": "abc", "bare_head": "old"},
            actions=[checkpoint, sync],
            receipts=[checkpoint_receipt],
        )
        self.assertEqual(result["completed_action_ids"], ["checkpoint.write"])
        self.assertEqual(result["next_action"]["action_id"], "work_git.sync_bare")
        self.assertEqual(result["next_action"]["decision"], "resume")

    def test_identical_terminal_rerun_reuses_all_receipts_and_executes_nothing(self) -> None:
        actions = [
            terminal_action_adapter("checkpoint.write", input_signature="checkpoint-sig")["contract"],
            terminal_action_adapter(
                "work_git.sync_bare", input_signature="sync-sig", depends_on=["checkpoint.write"]
            )["contract"],
        ]
        receipts = [
            terminal_receipt_for_action(
                action_id="checkpoint.write",
                input_signature="checkpoint-sig",
                intent_id="i1",
                result={"ok": True},
            ),
            terminal_receipt_for_action(
                action_id="work_git.sync_bare",
                input_signature="sync-sig",
                intent_id="i1",
                result={"ok": True},
            ),
        ]
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="reuse stable terminal state",
            source_state={"head": "abc", "bare_head": "abc"},
            actions=actions,
            receipts=receipts,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["completed_action_ids"], ["checkpoint.write", "work_git.sync_bare"])
        self.assertEqual(result["next_action"]["action_id"], "terminal.complete")
        self.assertEqual(result["next_action"]["decision"], "reuse")
    def test_irrelevant_route_pack_capsule_is_explicit(self) -> None:
        self.assertEqual(
            build_terminal_convergence_capsule({}),
            {"schema": "terminal_convergence.projection.v1", "relevant": False},
        )

    def test_micro_projection_has_one_terminal_next_action(self) -> None:
        actions = [
            action("host_projection.apply", effect="derived_mutation"),
            action(
                "work_git.sync_bare",
                phase="git_stability",
                effect="mutation",
                depends_on=["host_projection.apply"],
            ),
        ]
        canonical = build_terminal_convergence_capsule(
            {
                "terminal_convergence": {
                    "convergence_id": "c1",
                    "intent_id": "i1",
                    "terminal_goal": "stabilize source before publish",
                    "source_state": {"work_git_head": "abc", "bare_head": "old"},
                    "actions": actions,
                    "receipts": [],
                }
            }
        )
        projected = compact_execution_route_pack(
            {
                "structured_route": {"task_contract": {"task_facts": {}}},
                "execution_route_pack": {
                    "schema": "execution_route_pack.v1",
                    "terminal_convergence": canonical,
                    "route_decision": {},
                },
            },
            "micro",
        )
        capsule = projected["terminal_convergence"]
        self.assertEqual(capsule["current_phase"], "authoritative_inputs")
        self.assertEqual(capsule["next_action"]["action_id"], "host_projection.apply")
        self.assertNotIn("ordered_actions", capsule)

    def test_standard_projection_explains_graph_and_barriers(self) -> None:
        canonical = build_terminal_convergence_capsule(
            {
                "terminal_convergence": {
                    "convergence_id": "c1",
                    "intent_id": "i1",
                    "terminal_goal": "verify source",
                    "source_state": {"head": "abc"},
                    "actions": [action("source.verify", phase="read_only_acceptance")],
                    "receipts": [],
                }
            }
        )
        projected = compact_execution_route_pack(
            {
                "structured_route": {"task_contract": {"task_facts": {}}},
                "execution_route_pack": {
                    "schema": "execution_route_pack.v1",
                    "terminal_convergence": canonical,
                    "route_decision": {},
                },
            },
            "standard",
        )
        capsule = projected["terminal_convergence"]
        self.assertEqual(capsule["next_action"]["decision"], "verify")
        self.assertTrue(capsule["ordered_actions"])
        self.assertIn("mutation_barrier", capsule)
        self.assertIn("verification_barrier", capsule)

    def test_receipt_key_includes_explicit_intent(self) -> None:
        first = receipt_reuse_key(
            action_id="mirror.publish",
            input_signature="source-a",
            owner_contract_version="v1",
            intent_id="closeout-1",
        )
        second = receipt_reuse_key(
            action_id="mirror.publish",
            input_signature="source-a",
            owner_contract_version="v1",
            intent_id="closeout-2",
        )
        self.assertNotEqual(first, second)

    def test_terminal_source_signature_is_mapping_order_stable(self) -> None:
        first = terminal_source_signature(
            convergence_id="c1",
            intent_id="i1",
            source_state={"work_git": {"head": "abc", "dirty": False}, "bare_head": "abc"},
        )
        second = terminal_source_signature(
            convergence_id="c1",
            intent_id="i1",
            source_state={"bare_head": "abc", "work_git": {"dirty": False, "head": "abc"}},
        )
        self.assertEqual(first, second)

    def test_mutation_requires_acceptance_and_read_only_verify(self) -> None:
        valid = normalize_action_contract(
            action(
                "mirror.publish",
                phase="external_publish",
                effect="mutation",
                depends_on=["work_git.sync_bare"],
                approval_required=True,
            )
        )
        self.assertTrue(valid["ok"])

        invalid = action("mirror.publish", phase="external_publish", effect="mutation")
        invalid["receipt_acceptance"] = []
        invalid["verify_effect"] = "mutation"
        result = normalize_action_contract(invalid)
        self.assertFalse(result["ok"])
        self.assertIn("receipt_acceptance", result["missing"])
        self.assertIn("verify_effect=read_only", result["missing"])

    def test_shortest_cycle_fails_closed(self) -> None:
        result = validate_action_graph(
            [
                action("a", depends_on=["b"]),
                action("b", depends_on=["a"]),
                action("c", depends_on=["a"]),
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "dependency_cycle")
        self.assertEqual(result["cycle"], ["a", "b", "a"])

    def test_duplicate_authority_writer_fails_closed(self) -> None:
        result = validate_action_graph(
            [
                action("a", effect="mutation", authority_facts=["work_git.head"]),
                action("b", effect="derived_mutation", authority_facts=["work_git.head"]),
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_authority_writer")
        self.assertEqual(result["authority_fact"], "work_git.head")

    def test_missing_dependency_fails_closed(self) -> None:
        result = validate_action_graph([action("mirror.publish", depends_on=["work_git.sync_bare"])])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing_dependency")
        self.assertEqual(result["dependency"], "work_git.sync_bare")

    def test_invalidation_reaches_only_dependents(self) -> None:
        actions = [
            action("host_projection.apply"),
            action("work_git.commit", depends_on=["host_projection.apply"]),
            action("work_git.sync_bare", depends_on=["work_git.commit"]),
            action("mirror.publish", depends_on=["work_git.sync_bare"]),
            action("mirror.verify", depends_on=["mirror.publish"]),
            action("external.research"),
        ]
        self.assertEqual(
            invalidate_dependents(actions, ["host_projection.apply"]),
            ["work_git.commit", "work_git.sync_bare", "mirror.publish", "mirror.verify"],
        )

    def test_matching_receipt_is_reused_and_next_invalid_action_is_unique(self) -> None:
        actions = [
            action("work_git.sync_bare", phase="git_stability", effect="mutation"),
            action(
                "mirror.publish",
                phase="external_publish",
                effect="mutation",
                depends_on=["work_git.sync_bare"],
                approval_required=True,
            ),
        ]
        reuse_key = receipt_reuse_key(
            action_id="work_git.sync_bare",
            input_signature="sig",
            owner_contract_version="v1",
            intent_id="i1",
        )
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="publish one recovery snapshot",
            source_state={"work_git_head": "abc", "bare_head": "abc"},
            actions=actions,
            receipts=[{"action_id": "work_git.sync_bare", "reuse_key": reuse_key, "accepted": True}],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["completed_action_ids"], ["work_git.sync_bare"])
        self.assertEqual(result["next_action"]["action_id"], "mirror.publish")
        self.assertEqual(result["next_action"]["decision"], "block")
        self.assertEqual(result["next_action"]["reason"], "approval_required")

    def test_verification_barrier_rejects_pending_mutation(self) -> None:
        result = build_convergence_plan(
            convergence_id="c1",
            intent_id="i1",
            terminal_goal="verify terminal state",
            source_state={"head": "abc"},
            actions=[action("mirror.publish", phase="external_publish", effect="mutation")],
            receipts=[],
            entered_read_only_acceptance=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_terminal_mutation")
        self.assertEqual(result["first_invalid_action"], "mirror.publish")


if __name__ == "__main__":
    unittest.main()
