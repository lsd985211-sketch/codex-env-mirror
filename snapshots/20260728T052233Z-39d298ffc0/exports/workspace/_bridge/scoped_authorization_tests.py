#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scoped_authorization import (
    authorization_turn_id,
    build_scope,
    consume_grant,
    consume_permit,
    create_challenge,
    create_intent,
    decide,
    grant_from_thread,
    ensure_system_intent,
    inspect_context,
    introspect,
    issue_permit,
    record_effect,
    transition_intent,
    validate_grant,
)
from scoped_authorization_environment import (
    classify_environment_change,
    environment_signature,
    normalize_environment_snapshot,
)


class ScopedAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "auth"
        self.rollout = Path(self.temp.name) / "rollout-thread-a.jsonl"
        self.issued = datetime(2026, 7, 28, tzinfo=timezone.utc)
        self.scope = build_scope(
            thread_id="thread-a", action="review.approve", target={"package": "a", "revision": 1},
            risk="high", phase="decision_apply", source_signature="version-a", requested_by_owner="workflow_review_delivery",
        )
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-a"}}) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def message(self, text: str, at: datetime, *, role: str = "user") -> None:
        event = {"timestamp": at.isoformat(), "type": "response_item", "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def grant(self) -> tuple[dict, dict]:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertTrue(grant["ok"], grant)
        return challenge, grant

    def test_historical_or_broad_approval_cannot_grant_new_challenge(self) -> None:
        self.message("全部批准，批准执行", self.issued - timedelta(seconds=1))
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        missing = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertFalse(missing["ok"])
        self.assertEqual("post_challenge_user_response_not_found", missing["reason"])

    def test_assistant_echo_of_token_is_not_user_authorization(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1), role="assistant")
        missing = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertFalse(missing["ok"])

    def test_token_must_be_exact_user_message_and_rollout_must_match_thread(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(f"批准 {challenge['response_token']}", self.issued + timedelta(seconds=1))
        embedded = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("post_challenge_user_response_not_found", embedded["reason"])
        wrong_rollout = self.rollout.with_name("rollout-thread-b.jsonl")
        wrong_rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-b"}}) + "\n" + self.rollout.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        mismatch = grant_from_thread(challenge["challenge_ref"], rollout_path=wrong_rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("authorization_rollout_thread_mismatch", mismatch["reason"])

    def test_scope_changes_fail_for_action_target_phase_risk_version_or_owner(self) -> None:
        _, grant = self.grant()
        for field in ("action", "target_fingerprint", "phase", "risk", "source_signature", "requested_by_owner"):
            with self.subTest(field=field):
                changed = dict(self.scope)
                changed[field] += "-different"
                result = validate_grant(grant["grant_ref"], changed, state_root=self.root, checked_at=self.issued + timedelta(seconds=3))
                self.assertFalse(result["ok"])
                self.assertIn(field, result.get("mismatched_fields", []))

    def test_challenge_safety_ttl_does_not_expire_granted_turn_authorization(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, ttl_seconds=60, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        result = validate_grant(grant["grant_ref"], self.scope, state_root=self.root, checked_at=self.issued + timedelta(seconds=61))
        self.assertTrue(result["ok"], result)

    def test_single_use_is_atomic_and_same_operation_retry_reuses_receipt(self) -> None:
        _, grant = self.grant()
        first = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-a", state_root=self.root, consumed_at=self.issued + timedelta(seconds=3))
        self.assertTrue(first["ok"], first)
        retry = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-a", state_root=self.root, consumed_at=self.issued + timedelta(seconds=4))
        self.assertTrue(retry["ok"])
        self.assertTrue(retry["reused"])
        replay = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-b", state_root=self.root, consumed_at=self.issued + timedelta(seconds=5))
        self.assertFalse(replay["ok"])
        self.assertEqual("authorization_grant_consumed", replay["reason"])

    def test_same_operation_recovers_receipt_from_consumed_grant_after_crash_boundary(self) -> None:
        _, grant = self.grant()
        first = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-crash", state_root=self.root, consumed_at=self.issued + timedelta(seconds=3))
        Path(first["consumption_ref"]).unlink()
        recovered = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-crash", state_root=self.root, consumed_at=self.issued + timedelta(seconds=4))
        self.assertTrue(recovered["ok"])
        self.assertTrue(recovered["recovered_from_operation"])
        other = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-other", state_root=self.root, consumed_at=self.issued + timedelta(seconds=5))
        self.assertEqual("authorization_grant_consumed", other["reason"])

    def test_operation_receipt_cannot_authorize_a_different_grant(self) -> None:
        _, first_grant = self.grant()
        first = consume_grant(first_grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-bound", state_root=self.root, consumed_at=self.issued + timedelta(seconds=3))
        self.assertTrue(first["ok"])
        second_challenge = create_challenge(self.scope, target_summary="same scope new challenge", state_root=self.root, issued_at=self.issued + timedelta(seconds=4))
        self.message(second_challenge["response_token"], self.issued + timedelta(seconds=5))
        second_grant = grant_from_thread(second_challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=6))
        collision = consume_grant(second_grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-bound", state_root=self.root, consumed_at=self.issued + timedelta(seconds=7))
        self.assertEqual("authorization_operation_already_bound_to_another_grant", collision["reason"])

    def test_runtime_receipts_are_private_to_current_user(self) -> None:
        challenge = create_challenge(self.scope, target_summary="private receipt", state_root=self.root, issued_at=self.issued)
        challenge_path = self.root / "challenges" / f"{challenge['challenge_id']}.json"
        self.assertEqual(0o700, self.root.stat().st_mode & 0o777)
        self.assertEqual(0o700, challenge_path.parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, challenge_path.stat().st_mode & 0o777)

    def v2_intent(self, intent_type: str = "long_lived", **kwargs: object) -> dict:
        return create_intent(
            self.scope, intent_type=intent_type, authorizer="user", subject="user",
            evidence_ref="rollout:turn:approved", allowed_actor_classes=["codex", "scheduler"],
            delegation_allowed=True, max_delegation_depth=1, state_root=self.root, **kwargs,
        )

    def test_turn_id_is_stable_and_message_specific(self) -> None:
        first = authorization_turn_id("r:1", "hash-a")
        self.assertEqual(first, authorization_turn_id("r:1", "hash-a"))
        self.assertNotEqual(first, authorization_turn_id("r:2", "hash-a"))

    def test_long_lived_and_permanent_intents_only_issue_single_run_permits(self) -> None:
        for intent_type in ("long_lived", "permanent_default"):
            with self.subTest(intent_type=intent_type):
                intent = self.v2_intent(intent_type)
                self.assertTrue(intent["ok"], intent)
                permit = issue_permit(
                    intent["intent_ref"], self.scope, actor_chain=[{"class": "scheduler", "id": "daily"}],
                    executor="workflow_review_delivery", audience="workflow_review_delivery",
                    operation_id=f"{intent_type}:op", workflow_semantic_hash="workflow-v1",
                    automation_run_id=f"{intent_type}:run", state_root=self.root,
                )
                self.assertTrue(permit["ok"], permit)
                consumed = consume_permit(
                    permit["permit_ref"], executor="workflow_review_delivery",
                    operation_id=f"{intent_type}:op", state_root=self.root,
                )
                self.assertTrue(consumed["ok"], consumed)
                self.assertFalse(introspect(permit["permit_ref"], state_root=self.root)["active"])

    def test_explicit_deny_and_expanding_delegation_fail_closed(self) -> None:
        intent = self.v2_intent()
        denied = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", explicit_denies=["platform_hard_boundary"],
            state_root=self.root,
        )
        self.assertEqual("deny", denied["decision"])
        expanded = dict(self.scope)
        expanded["actions"] = ["review.approve", "mirror.release"]
        refused = decide(
            intent["intent_ref"], expanded, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", state_root=self.root,
        )
        self.assertIn("scope_not_attenuated", refused["determining_denies"])

    def test_revoke_fences_previously_issued_permit(self) -> None:
        intent = self.v2_intent()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="revoke-op", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-a", state_root=self.root,
        )
        revoked = transition_intent(intent["intent_ref"], action="revoke", reason="user withdrew", state_root=self.root)
        self.assertTrue(revoked["ok"])
        active = introspect(permit["permit_ref"], state_root=self.root)
        self.assertFalse(active["active"])
        self.assertIn("authorization_generation_fenced", active["reasons"])

    def test_resume_is_meta_permission_and_requires_new_evidence(self) -> None:
        intent = self.v2_intent()
        self.assertTrue(transition_intent(intent["intent_ref"], action="suspend", reason="safety", state_root=self.root)["ok"])
        blocked = transition_intent(intent["intent_ref"], action="resume", reason="resume", state_root=self.root)
        self.assertEqual("authorization_meta_permission_evidence_required", blocked["reason"])
        resumed = transition_intent(intent["intent_ref"], action="resume", reason="user approved", evidence_ref="rollout:turn:new", state_root=self.root)
        self.assertTrue(resumed["ok"], resumed)

    def test_effect_unknown_requires_readback_before_completion(self) -> None:
        intent = self.v2_intent()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="effect-op", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-effect", state_root=self.root,
        )
        self.assertTrue(consume_permit(permit["permit_ref"], executor="workflow_review_delivery", operation_id="effect-op", state_root=self.root)["ok"])
        self.assertTrue(record_effect("effect-op", executor="workflow_review_delivery", status="effect_started", state_root=self.root)["ok"])
        self.assertTrue(record_effect("effect-op", executor="workflow_review_delivery", status="effect_unknown", state_root=self.root)["ok"])
        premature = record_effect("effect-op", executor="workflow_review_delivery", status="completed", state_root=self.root)
        self.assertEqual("authorization_effect_transition_invalid", premature["reason"])
        observed = record_effect("effect-op", executor="workflow_review_delivery", status="effect_observed", effect_receipt_ref="owner:readback:1", state_root=self.root)
        self.assertTrue(observed["ok"], observed)
        self.assertTrue(record_effect("effect-op", executor="workflow_review_delivery", status="completed", state_root=self.root)["ok"])
        recovered = record_effect(
            "effect-op", executor="workflow_review_delivery", status="effect_observed",
            effect_receipt_ref="owner:readback:1", state_root=self.root,
        )
        self.assertTrue(recovered["recovered_terminal_effect"])

    def test_inspect_returns_minimal_authorization_delta(self) -> None:
        self.v2_intent()
        report = inspect_context(
            self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", state_root=self.root,
        )
        self.assertEqual("allow", report["effective_decision"])
        self.assertEqual("issue_permit", report["required_next_action"])

    def environment(self, *, effect: str = "neutral", workflow: str = "workflow-v1") -> dict:
        return normalize_environment_snapshot({
            "workflow_semantic_hash": workflow,
            "authorization_semantic_signature": "auth-semantics-v1",
            "required_sources": ["workflow", "owner_capability"],
            "sources": {
                "workflow": {"signature": workflow, "authority_ref": "workflow:contract:1"},
                "owner_capability": {
                    "signature": "owner-v1", "authority_ref": "owner:capability:1",
                    "authorization_effect": effect,
                },
            },
        })

    def test_environment_change_classification_never_auto_expands(self) -> None:
        baseline = self.environment()
        equivalent = self.environment()
        self.assertEqual(environment_signature(baseline), environment_signature(equivalent))
        self.assertEqual("equivalent", classify_environment_change(baseline, equivalent)["classification"])
        for effect, expected in (("tighten", "tightened"), ("incompatible", "incompatible"), ("expand", "expansion_required"), ("unavailable", "unavailable")):
            with self.subTest(effect=effect):
                result = classify_environment_change(baseline, self.environment(effect=effect))
                self.assertEqual(expected, result["classification"])
                self.assertNotEqual("reuse", result["automatic_action"])

    def test_permit_is_fenced_by_tightening_or_expansion_but_not_equivalent_refresh(self) -> None:
        intent = self.v2_intent()
        baseline = self.environment()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="environment-op", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-environment", environment_snapshot=baseline, state_root=self.root,
        )
        self.assertTrue(permit["ok"], permit)
        equivalent = introspect(permit["permit_ref"], current_environment_snapshot=self.environment(), state_root=self.root)
        self.assertTrue(equivalent["active"], equivalent)
        tightened = introspect(permit["permit_ref"], current_environment_snapshot=self.environment(effect="tighten"), state_root=self.root)
        self.assertFalse(tightened["active"], tightened)
        expanded = introspect(permit["permit_ref"], current_environment_snapshot=self.environment(effect="expand"), state_root=self.root)
        self.assertFalse(expanded["active"], expanded)
        self.assertTrue(expanded["authorization_reconciliation_required"])

    def test_missing_required_environment_source_fails_closed_at_consumption(self) -> None:
        intent = self.v2_intent()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="missing-source-op", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-missing", environment_snapshot=self.environment(), state_root=self.root,
        )
        missing = self.environment()
        missing["sources"].pop("owner_capability")
        result = consume_permit(
            permit["permit_ref"], executor="workflow_review_delivery", operation_id="missing-source-op",
            current_environment_snapshot=missing, state_root=self.root,
        )
        self.assertEqual("authorization_permit_inactive", result["reason"])
        self.assertEqual("unavailable", result["introspection"]["environment_change"]["classification"])

    def test_system_default_is_idempotent_and_semantic_change_requires_reconciliation(self) -> None:
        created = ensure_system_intent(
            self.scope, authority_ref="system-membership:scheduler:test", subject="scheduler:test",
            state_root=self.root,
        )
        self.assertTrue(created["ok"], created)
        reused = ensure_system_intent(
            self.scope, authority_ref="system-membership:scheduler:test", subject="scheduler:test",
            state_root=self.root,
        )
        self.assertTrue(reused["reused"], reused)
        changed = dict(self.scope)
        changed["target_fingerprint"] = "different"
        blocked = ensure_system_intent(
            changed, authority_ref="system-membership:scheduler:test", subject="scheduler:test",
            state_root=self.root,
        )
        self.assertEqual("authorization_reconciliation_required", blocked["reason"])
        self.assertFalse(blocked["automatic_expansion_allowed"])

    def test_intent_environment_baseline_fences_once_and_never_accepts_pending_expansion(self) -> None:
        intent = self.v2_intent()
        baseline = self.environment()
        first = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", environment_snapshot=baseline, state_root=self.root,
        )
        self.assertEqual("allow", first["decision"])
        expanded = self.environment(effect="expand")
        blocked = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", environment_snapshot=expanded, state_root=self.root,
        )
        self.assertEqual("deny", blocked["decision"])
        generation = blocked["environment_reconciliation"]["generation"]
        repeated = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", environment_snapshot=expanded, state_root=self.root,
        )
        self.assertEqual("deny", repeated["decision"])
        self.assertEqual(generation, repeated["environment_reconciliation"]["generation"])
        self.assertEqual("expansion_required", repeated["environment_reconciliation"]["classification"])

    def test_tightened_environment_advances_generation_and_recovery_preserves_original_scope(self) -> None:
        intent = self.v2_intent()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="baseline-permit", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-baseline", environment_snapshot=self.environment(), state_root=self.root,
        )
        tightened = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", environment_snapshot=self.environment(effect="tighten"), state_root=self.root,
        )
        self.assertEqual("deny", tightened["decision"])
        fenced = introspect(permit["permit_ref"], current_environment_snapshot=self.environment(effect="tighten"), state_root=self.root)
        self.assertIn("authorization_generation_fenced", fenced["reasons"])
        recovered = decide(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            workflow_semantic_hash="workflow-v1", environment_snapshot=self.environment(), state_root=self.root,
        )
        self.assertEqual("allow", recovered["decision"])


if __name__ == "__main__":
    unittest.main()
