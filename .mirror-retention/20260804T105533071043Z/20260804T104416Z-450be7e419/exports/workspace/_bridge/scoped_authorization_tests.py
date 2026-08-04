#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scoped_authorization import (
    authorization_turn_id,
    _user_messages,
    build_scope,
    cancel_challenge,
    challenge_presentation,
    challenge_snapshot,
    consume_challenge,
    consume_grant,
    consume_permit,
    create_challenge,
    create_intent,
    decide,
    grant_from_thread,
    ensure_system_intent,
    inspect_context,
    introspect,
    create_current_task_intent,
    issue_current_task_permit,
    issue_permit,
    operation_projection,
    operation_snapshot,
    record_effect,
    relevant_input_signature,
    reserve_prepared_operation,
    rich_scope_signature,
    scope_signature,
    transition_intent,
    target_fingerprint,
    validate_same_scope_fallback,
    validate_grant,
)
import scoped_authorization_policy as authorization_policy
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

    def test_post_challenge_direct_approval_binds_once_without_repeating_a_token(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message("批准继续", self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertTrue(grant["ok"], grant)
        self.assertFalse(grant["response_token_matched"])
        self.assertEqual("direct_user_approval", grant["authorization_binding_mode"])

        replay = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=3))
        self.assertTrue(replay["reused"])
        self.assertEqual(grant["user_message_ref"], replay["user_message_ref"])

    def test_pre_challenge_direct_task_binds_low_risk_exact_challenge_once(self) -> None:
        self.message("请修复 package a 的本地规则", self.issued - timedelta(seconds=1))
        directive = _user_messages(self.rollout)[-1]
        challenge = create_challenge(
            {**self.scope, "risk": "R2"}, target_summary="package a", state_root=self.root, issued_at=self.issued,
            task_directive_ref=directive["message_ref"], task_directive_hash=directive["message_hash"],
        )
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=1))
        self.assertTrue(grant["ok"], grant)
        self.assertEqual("pre_challenge_direct_task", grant["authorization_binding_mode"])
        self.assertEqual(directive["message_ref"], grant["user_message_ref"])
        self.assertTrue(grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root)["reused"])

    def test_current_direct_approval_auto_binds_low_risk_challenge_without_token(self) -> None:
        self.message("请完善 package a 的本地规则", self.issued - timedelta(seconds=2))
        self.message("批准实施", self.issued - timedelta(seconds=1))
        challenge = create_challenge({**self.scope, "risk": "R2"}, target_summary="package a", rollout_path=self.rollout, state_root=self.root, issued_at=self.issued)
        self.assertTrue(challenge["ok"], challenge)
        self.assertEqual("", challenge["response_token"])
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root)
        self.assertTrue(grant["ok"], grant)
        self.assertEqual("pre_challenge_direct_approval", grant["authorization_binding_mode"])

    def test_scope_neutral_efficiency_wording_keeps_direct_approval_bound(self) -> None:
        self.message("请修复 package a 的本地规则", self.issued - timedelta(seconds=2))
        self.message("你继续你的任务，不要浪费成本", self.issued - timedelta(seconds=1))
        challenge = create_challenge(
            {**self.scope, "risk": "R2"}, target_summary="package a", rollout_path=self.rollout,
            state_root=self.root, issued_at=self.issued,
        )
        self.assertEqual("", challenge["response_token"])
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root)
        self.assertTrue(grant["ok"], grant)

    def test_token_only_message_does_not_hide_prior_direct_approval(self) -> None:
        self.message("请修复 package a 的本地规则", self.issued - timedelta(seconds=3))
        self.message("批准实施", self.issued - timedelta(seconds=2))
        self.message("AUTHORIZE-token-for-an-earlier-challenge", self.issued - timedelta(seconds=1))
        challenge = create_challenge(
            {**self.scope, "risk": "R2"}, target_summary="package a", rollout_path=self.rollout,
            state_root=self.root, issued_at=self.issued,
        )
        self.assertEqual("", challenge["response_token"])

    def test_current_direct_approval_discovers_exact_rollout_without_caller_plumbing(self) -> None:
        sessions = Path(self.temp.name) / "sessions"
        sessions.mkdir()
        self.message("请优化 package a 的本地规则", self.issued - timedelta(seconds=2))
        self.message("批准实施", self.issued - timedelta(seconds=1))
        discovered = sessions / self.rollout.name
        discovered.write_text(self.rollout.read_text(encoding="utf-8"), encoding="utf-8")
        challenge = create_challenge({**self.scope, "risk": "R2"}, target_summary="package a", sessions_root=sessions, state_root=self.root, issued_at=self.issued)
        self.assertEqual("", challenge["response_token"])
        self.assertTrue(grant_from_thread(challenge["challenge_ref"], rollout_path=discovered, state_root=self.root)["ok"])

    def test_auto_pre_challenge_approval_refuses_r3_and_rollout_mismatch(self) -> None:
        self.message("请完善 package a 的本地规则", self.issued - timedelta(seconds=2))
        self.message("批准实施", self.issued - timedelta(seconds=1))
        self.assertTrue(create_challenge({**self.scope, "risk": "R3"}, target_summary="package a", rollout_path=self.rollout, state_root=self.root, issued_at=self.issued)["response_token"])
        wrong_rollout = self.rollout.with_name("rollout-thread-b.jsonl")
        wrong_rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-b"}}) + "\n", encoding="utf-8")
        rejected = create_challenge({**self.scope, "risk": "R2"}, target_summary="package a", rollout_path=wrong_rollout, state_root=self.root, issued_at=self.issued)
        self.assertEqual("authorization_rollout_thread_mismatch", rejected["reason"])

    def test_subtask_challenge_binds_desktop_parent_thread_and_rejects_child_rollout(self) -> None:
        sessions = Path(self.temp.name) / "sessions"
        sessions.mkdir()
        parent = sessions / "parent.jsonl"
        child = sessions / "child.jsonl"
        parent_id = "019fb0f5-892b-7d81-aeaf-dbd3d306fbf7"
        child_id = "019fc511-f06e-79f0-8faf-a88c0aba6440"
        parent.write_text(json.dumps({"type": "session_meta", "payload": {"id": parent_id}}) + "\n", encoding="utf-8")
        child.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": child_id, "parent_thread_id": parent_id,
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent_id}}},
        }}) + "\n", encoding="utf-8")
        subtask_scope = build_scope(
            thread_id=child_id, action="local.reversible.edit", target={"path": "a.txt"}, risk="R2",
            phase="implementation", source_signature="head-a", requested_by_owner="task_owner",
        )
        challenge = create_challenge(
            subtask_scope, target_summary="a.txt", rollout_path=child, sessions_root=sessions,
            state_root=self.root, issued_at=self.issued,
        )
        self.assertTrue(challenge["ok"], challenge)
        self.assertEqual(parent_id, challenge["scope"]["thread_id"])
        self.assertEqual(parent_id, challenge["authorization_thread"]["thread_id"])
        self.assertEqual(child_id, challenge["authorization_thread"]["executor_thread_id"])
        self.assertEqual("codex_app_session_meta.parent_thread_id", challenge["authorization_thread"]["source"])
        with parent.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": (self.issued + timedelta(seconds=1)).isoformat(), "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": challenge["response_token"]}],
            }}) + "\n")
        self.assertEqual(
            "authorization_rollout_thread_mismatch",
            grant_from_thread(challenge["challenge_ref"], rollout_path=child, state_root=self.root)["reason"],
        )
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=parent, state_root=self.root)
        self.assertTrue(grant["ok"], grant)
        self.assertEqual(challenge["authorization_thread"], grant["authorization_thread"])
        consumed = consume_grant(
            grant["grant_ref"], challenge["scope"], consumer_owner="task_owner", operation_id="parent-bound",
            state_root=self.root,
        )
        self.assertTrue(consumed["ok"], consumed)
        permit_id = str(consumed["permit_ref"]).rsplit(":", 1)[-1]
        permit = json.loads((self.root / "permits" / f"{permit_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(challenge["authorization_thread"], permit["authorization_thread"])
        consumption = json.loads(Path(consumed["consumption_ref"]).read_text(encoding="utf-8"))
        operation = json.loads(Path(consumed["operation_ref"]).read_text(encoding="utf-8"))
        self.assertEqual(challenge["authorization_thread"], consumption["authorization_thread"])
        self.assertEqual(challenge["authorization_thread"], operation["authorization_thread"])

    def test_subtask_parent_projection_is_not_used_for_r3(self) -> None:
        sessions = Path(self.temp.name) / "sessions"
        sessions.mkdir()
        child = sessions / "child.jsonl"
        child.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "child-thread", "parent_thread_id": "parent-thread",
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent-thread"}}},
        }}) + "\n", encoding="utf-8")
        challenge = create_challenge(
            build_scope(thread_id="child-thread", action="local.reversible.edit", target={"path": "a.txt"}, risk="R3",
                        phase="implementation", source_signature="head-a", requested_by_owner="task_owner"),
            target_summary="a.txt", rollout_path=child, sessions_root=sessions, state_root=self.root, issued_at=self.issued,
        )
        self.assertEqual("child-thread", challenge["scope"]["thread_id"])
        self.assertEqual("scope_thread_id", challenge["authorization_thread"]["source"])

    def test_pre_challenge_direct_task_cannot_bind_r3_or_changed_evidence(self) -> None:
        self.message("请修复 package a 的本地规则", self.issued - timedelta(seconds=1))
        directive = _user_messages(self.rollout)[-1]
        r3 = create_challenge(
            {**self.scope, "risk": "R3"}, target_summary="package a", state_root=self.root, issued_at=self.issued,
            task_directive_ref=directive["message_ref"], task_directive_hash=directive["message_hash"],
        )
        self.assertEqual("post_challenge_user_response_not_found", grant_from_thread(r3["challenge_ref"], rollout_path=self.rollout, state_root=self.root)["reason"])
        mismatched = create_challenge(
            {**self.scope, "risk": "R2"}, target_summary="package a", state_root=self.root, issued_at=self.issued,
            task_directive_ref=directive["message_ref"], task_directive_hash="wrong",
        )
        self.assertEqual("authorization_task_directive_unverified", grant_from_thread(mismatched["challenge_ref"], rollout_path=self.rollout, state_root=self.root)["reason"])

    def test_direct_approval_rejects_ambiguous_text_or_unknown_token(self) -> None:
        ambiguous = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message("批准处理所有事情", self.issued + timedelta(seconds=1))
        blocked = grant_from_thread(ambiguous["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("post_challenge_user_response_not_found", blocked["reason"])

        unknown = create_challenge(self.scope, target_summary="package b", state_root=self.root, issued_at=self.issued + timedelta(seconds=3))
        self.message("AUTHORIZE-not-this-challenge", self.issued + timedelta(seconds=4))
        refused = grant_from_thread(unknown["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=5))
        self.assertEqual("authorization_response_token_not_for_challenge", refused["reason"])

    def test_assistant_echo_of_token_is_not_user_authorization(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1), role="assistant")
        missing = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertFalse(missing["ok"])

    def test_token_may_carry_attenuation_but_rollout_must_match_thread(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(f"批准 {challenge['response_token']}，不要发布镜像", self.issued + timedelta(seconds=1))
        embedded = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertTrue(embedded["ok"], embedded)
        self.assertEqual(["mirror.publish", "mirror.refresh"], embedded["attenuation"]["prohibitions"])
        wrong_rollout = self.rollout.with_name("rollout-thread-b.jsonl")
        wrong_rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-b"}}) + "\n" + self.rollout.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        mismatch = grant_from_thread(challenge["challenge_ref"], rollout_path=wrong_rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("authorization_rollout_thread_mismatch", mismatch["reason"])

    def test_challenge_presentation_requires_a_persisted_owner_issued_token(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        missing = challenge_presentation("", response_token="AUTHORIZE-RESTART-invented", state_root=self.root)
        self.assertEqual("authorization_challenge_ref_required_for_presentation", missing["reason"])
        invented = challenge_presentation(
            challenge["challenge_ref"], response_token="AUTHORIZE-RESTART-invented", state_root=self.root,
        )
        self.assertEqual("authorization_response_token_not_owner_issued", invented["reason"])
        presented = challenge_presentation(
            challenge["challenge_ref"], response_token=challenge["response_token"], state_root=self.root,
        )
        self.assertTrue(presented["ok"], presented)
        self.assertEqual(challenge["challenge_ref"], presented["challenge_ref"])
        self.assertEqual(challenge["response_token"], presented["response_token"])

    def test_grant_projects_scope_bound_owner_context_from_token_attachment(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(
            f"{challenge['response_token']}; owner=workflow_review_delivery; action=review.approve; phase=decision_apply",
            self.issued + timedelta(seconds=1),
        )
        grant = grant_from_thread(
            challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root,
            checked_at=self.issued + timedelta(seconds=2),
        )
        self.assertTrue(grant["ok"], grant)
        self.assertEqual("workflow_review_delivery", grant["owner_context"]["requested_by_owner"])
        self.assertEqual("workflow_review_delivery", grant["owner_context"]["attachment_context"]["owner"])
        validated = validate_grant(grant["grant_ref"], self.scope, state_root=self.root)
        self.assertEqual(grant["owner_context"], validated["owner_context"])

    def test_scope_changes_fail_for_action_target_phase_risk_version_or_owner(self) -> None:
        _, grant = self.grant()
        for field in ("action", "target_fingerprint", "phase", "risk", "source_signature", "requested_by_owner"):
            with self.subTest(field=field):
                changed = dict(self.scope)
                changed[field] += "-different"
                result = validate_grant(grant["grant_ref"], changed, state_root=self.root, checked_at=self.issued + timedelta(seconds=3))
                self.assertFalse(result["ok"])
                self.assertIn(field, result.get("mismatched_fields", []))

    def test_explicit_challenge_deadline_does_not_expire_granted_authorization_without_grant_deadline(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, ttl_seconds=60, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        result = validate_grant(grant["grant_ref"], self.scope, state_root=self.root, checked_at=self.issued + timedelta(seconds=61))
        self.assertTrue(result["ok"], result)

    def test_authorization_has_no_wall_clock_expiry_by_default(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.assertEqual("", challenge["challenge_expires_at"])
        persisted = json.loads((self.root / "challenges" / f"{challenge['challenge_id']}.json").read_text(encoding="utf-8"))
        self.assertNotIn("response_token", persisted)
        self.assertNotIn(challenge["response_token"], json.dumps(persisted))
        self.message(challenge["response_token"], self.issued + timedelta(days=30))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(days=30, seconds=1))
        self.assertTrue(grant["ok"], grant)
        self.assertEqual("", grant["grant_consume_by"])
        self.assertTrue(validate_grant(grant["grant_ref"], self.scope, state_root=self.root, checked_at=self.issued + timedelta(days=365))["ok"])

    def test_first_qualified_evidence_wins_and_replay_is_diagnostic(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(f"{challenge['response_token']}，不要删除", self.issued + timedelta(seconds=1))
        self.message(challenge["response_token"], self.issued + timedelta(seconds=2))
        first = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=3))
        replay = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=4))
        self.assertEqual(["delete"], first["attenuation"]["prohibitions"])
        self.assertTrue(replay["reused"])
        self.assertEqual(first["user_message_ref"], replay["user_message_ref"])

    def test_wrapped_token_is_ignored_and_multiple_tokens_cannot_grant(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(f"<codex_delegation>{challenge['response_token']}</codex_delegation>", self.issued + timedelta(seconds=1))
        blocked = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("post_challenge_user_response_not_found", blocked["reason"])
        self.message(f"批准 {challenge['response_token']}", self.issued + timedelta(seconds=3))
        granted = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=4))
        self.assertTrue(granted["ok"], granted)

    def test_legacy_v1_expiry_is_not_reinterpreted_as_a_new_default_deadline(self) -> None:
        challenge = create_challenge(self.scope, target_summary="legacy", state_root=self.root, issued_at=self.issued)
        path = self.root / "challenges" / f"{challenge['challenge_id']}.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy.update({"schema": "scoped_authorization.challenge.v1", "response_token": challenge["response_token"], "expires_at": (self.issued + timedelta(seconds=60)).isoformat()})
        legacy.pop("response_token_salt", None)
        legacy.pop("response_token_hash", None)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.message(challenge["response_token"], self.issued + timedelta(days=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(days=1, seconds=1))
        self.assertTrue(grant["ok"], grant)
        self.assertTrue(validate_grant(grant["grant_ref"], self.scope, state_root=self.root, checked_at=self.issued + timedelta(days=2))["ok"])

    def test_explicit_grant_and_permit_deadlines_are_opt_in(self) -> None:
        challenge = create_challenge(
            self.scope, target_summary="deadline transaction", state_root=self.root, issued_at=self.issued,
            grant_ttl_seconds=60, permit_ttl_seconds=60,
        )
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        expired = validate_grant(grant["grant_ref"], self.scope, state_root=self.root, checked_at=self.issued + timedelta(seconds=63))
        self.assertEqual("authorization_grant_expired", expired["reason"])

    def test_permit_deadline_is_opt_in_and_scope_signature_stays_stable(self) -> None:
        intent = self.v2_intent()
        rich = {**self.scope, "prohibitions": ["mirror.publish"], "budget": {"money_cny": 10}}
        self.assertEqual(scope_signature(self.scope), scope_signature(rich))
        self.assertEqual(relevant_input_signature(self.scope), relevant_input_signature(rich))
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="deadline-permit", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-deadline", permit_ttl_seconds=60, issued_at=self.issued,
            state_root=self.root,
        )
        self.assertEqual("", issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="default-permit", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-default", issued_at=self.issued, state_root=self.root,
        )["permit_expires_at"])
        expired = introspect(permit["permit_ref"], state_root=self.root, checked_at=self.issued + timedelta(seconds=61))
        self.assertIn("authorization_permit_expired", expired["reasons"])

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

    def test_challenge_consume_discovers_exact_rollout_and_exposes_signature_projections(self) -> None:
        low_scope = {**self.scope, "risk": "R2"}
        challenge = create_challenge(low_scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        result = consume_challenge(
            challenge["challenge_ref"], consumer_owner="workflow_review_delivery", operation_id="one-stop",
            sessions_root=self.rollout.parent, state_root=self.root, checked_at=self.issued + timedelta(seconds=2),
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual("exact_session_id", result["discovery"]["discovery"])
        self.assertEqual(scope_signature(low_scope), result["challenge_scope_signature"])
        self.assertEqual(rich_scope_signature(low_scope), result["permit_scope_signature"])
        self.assertTrue(result["scope_signature_relationship"]["same_normalized_scope"])

    def test_challenge_consume_refuses_r3_or_wrong_owner(self) -> None:
        low_scope = {**self.scope, "risk": "R2"}
        challenge = create_challenge(low_scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        wrong_owner = consume_challenge(
            challenge["challenge_ref"], consumer_owner="other_owner", operation_id="one-stop",
            rollout_path=self.rollout, state_root=self.root,
        )
        self.assertEqual("authorization_challenge_consume_owner_not_bound", wrong_owner["reason"])
        r3_scope = {**self.scope, "risk": "R3"}
        r3 = create_challenge(r3_scope, target_summary="r3", state_root=self.root, issued_at=self.issued)
        blocked = consume_challenge(r3["challenge_ref"], consumer_owner="workflow_review_delivery", operation_id="r3", rollout_path=self.rollout, state_root=self.root)
        self.assertEqual("authorization_challenge_consume_risk_not_eligible", blocked["reason"])

    def test_external_target_fingerprint_and_source_drift_are_reported_at_challenge_consume(self) -> None:
        target = {"kind": "external_non_git", "path": "/tmp/owned-rule.json", "digest": "bytes-v1"}
        fingerprint = target_fingerprint(target)
        scope = build_scope(
            thread_id="thread-a", action="local.reversible.edit", target={"ignored": "different"},
            target_fingerprint_value=fingerprint, risk="R2", phase="implementation",
            source_signature="head-v1", requested_by_owner="workflow_review_delivery",
        )
        challenge = create_challenge(scope, target_summary="external rule", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        expected = {**scope, "source_signature": "head-v2"}
        rejected = consume_challenge(
            challenge["challenge_ref"], consumer_owner="workflow_review_delivery", operation_id="external-drift",
            expected_scope=expected, rollout_path=self.rollout, state_root=self.root,
        )
        self.assertEqual("authorization_challenge_scope_mismatch", rejected["reason"])
        self.assertIn("source_signature", rejected["mismatched_fields"])
        self.assertEqual("head-v2", rejected["expected_scope"]["source_signature"])
        self.assertEqual("head-v1", rejected["actual_scope"]["source_signature"])

    def test_challenge_consume_reports_audience_and_operation_binding_evidence(self) -> None:
        low_scope = {**self.scope, "risk": "R2"}
        challenge = create_challenge(low_scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        audience = consume_challenge(
            challenge["challenge_ref"], consumer_owner="workflow_review_delivery", audience="other-audience",
            operation_id="audience-check", rollout_path=self.rollout, state_root=self.root,
        )
        self.assertEqual("authorization_challenge_consume_audience_not_bound", audience["reason"])
        self.assertEqual("workflow_review_delivery", audience["expected_audience"])
        self.assertEqual("other-audience", audience["actual_audience"])

        challenge = create_challenge(
            self.scope, target_summary="package a", state_root=self.root,
            issued_at=self.issued + timedelta(seconds=10),
        )
        self.message(challenge["response_token"], self.issued + timedelta(seconds=11))
        grant = grant_from_thread(
            challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root,
            checked_at=self.issued + timedelta(seconds=12),
        )
        self.assertTrue(grant["ok"], grant)
        first = consume_grant(
            grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery",
            operation_id="duplicate-a", state_root=self.root,
        )
        self.assertTrue(first["ok"], first)
        duplicate = consume_grant(
            grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery",
            operation_id="duplicate-b", state_root=self.root,
        )
        self.assertEqual("authorization_grant_consumed", duplicate["reason"])
        self.assertEqual("duplicate-b", duplicate["operation_id"])
        self.assertEqual("duplicate-a", duplicate["existing_operation_id"])

    def test_fallback_action_must_be_predeclared_in_the_same_scope(self) -> None:
        scope = {**self.scope, "fallback_actions": ["local.lexical_fallback"]}
        allowed = validate_same_scope_fallback(scope, "local.lexical_fallback")
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual("same_scope_fallback", allowed["authorization_binding"])
        rejected = validate_same_scope_fallback(scope, "external.network_fallback")
        self.assertEqual("authorization_fallback_scope_expansion", rejected["reason"])
        self.assertNotIn("permit", rejected)

        challenge_scope = {**scope, "risk": "R2"}
        challenge = create_challenge(
            challenge_scope, target_summary="package a", state_root=self.root, issued_at=self.issued,
        )
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        drifted = {**challenge_scope, "fallback_actions": ["other.fallback"]}
        mismatch = consume_challenge(
            challenge["challenge_ref"], consumer_owner="workflow_review_delivery",
            operation_id="fallback-drift", expected_scope=drifted,
            rollout_path=self.rollout, state_root=self.root,
        )
        self.assertEqual("authorization_challenge_scope_mismatch", mismatch["reason"])
        self.assertIn("fallback_actions", mismatch["mismatched_fields"])

    def test_same_operation_recovers_receipt_from_consumed_grant_after_crash_boundary(self) -> None:
        _, grant = self.grant()
        first = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-crash", state_root=self.root, consumed_at=self.issued + timedelta(seconds=3))
        Path(first["consumption_ref"]).unlink()
        recovered = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-crash", state_root=self.root, consumed_at=self.issued + timedelta(seconds=4))
        self.assertTrue(recovered["ok"])
        self.assertTrue(recovered["recovered_from_operation"])
        other = consume_grant(grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery", operation_id="op-other", state_root=self.root, consumed_at=self.issued + timedelta(seconds=5))
        self.assertEqual("authorization_grant_consumed", other["reason"])

    def test_attenuation_survives_permit_consumption_and_crash_recovery(self) -> None:
        challenge = create_challenge(self.scope, target_summary="package a", state_root=self.root, issued_at=self.issued)
        self.message(f"{challenge['response_token']}，不要发布镜像", self.issued + timedelta(seconds=1))
        grant = grant_from_thread(
            challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root,
            checked_at=self.issued + timedelta(seconds=2),
        )
        consumed = consume_grant(
            grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery",
            operation_id="attenuation-op", state_root=self.root,
            consumed_at=self.issued + timedelta(seconds=3),
        )
        expected = ["mirror.publish", "mirror.refresh"]
        self.assertEqual(expected, consumed["attenuation"]["prohibitions"])
        permit_id = str(consumed["permit_ref"]).rsplit(":", 1)[-1]
        permit = json.loads((self.root / "permits" / f"{permit_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(expected, permit["attenuation"]["prohibitions"])
        Path(consumed["consumption_ref"]).unlink()
        recovered = consume_grant(
            grant["grant_ref"], self.scope, consumer_owner="workflow_review_delivery",
            operation_id="attenuation-op", state_root=self.root,
            consumed_at=self.issued + timedelta(seconds=4),
        )
        self.assertTrue(recovered["recovered_from_operation"])
        self.assertEqual(expected, recovered["attenuation"]["prohibitions"])

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

    def test_cancelled_challenge_cannot_issue_a_grant_and_snapshot_is_read_only(self) -> None:
        challenge = create_challenge(self.scope, target_summary="fence full package", state_root=self.root, issued_at=self.issued)
        before = challenge_snapshot(challenge["challenge_ref"], state_root=self.root, checked_at=self.issued)
        self.assertEqual("pending", before["status"])
        cancelled = cancel_challenge(challenge["challenge_ref"], reason="partial package selected", state_root=self.root)
        self.assertTrue(cancelled["ok"], cancelled)
        after = challenge_snapshot(challenge["challenge_ref"], state_root=self.root, checked_at=self.issued)
        self.assertEqual("cancelled", after["status"])
        self.message(challenge["response_token"], self.issued + timedelta(seconds=1))
        grant = grant_from_thread(challenge["challenge_ref"], rollout_path=self.rollout, state_root=self.root, checked_at=self.issued + timedelta(seconds=2))
        self.assertEqual("authorization_challenge_cancelled", grant["reason"])

    def v2_intent(self, intent_type: str = "long_lived", **kwargs: object) -> dict:
        return create_intent(
            self.scope, intent_type=intent_type, authorizer="user", subject="user",
            evidence_ref="rollout:turn:approved", allowed_actor_classes=["codex", "scheduler"],
            delegation_allowed=True, max_delegation_depth=1, state_root=self.root, **kwargs,
        )

    def task_assessment(self, scope: dict, *, level: str = "R2", high_cost: bool = False, unknown_cost: bool = False) -> dict:
        costs = {name: 0 for name in authorization_policy.load_policy()["cost_dimensions"]}
        if high_cost:
            costs["elapsed_minutes"] = 30
        if unknown_cost:
            costs.pop("elapsed_minutes")
        return {
            "subject": {"thread_id": scope["thread_id"]},
            "action": {"id": scope["action"]},
            "resource": {"target_fingerprint": scope["target_fingerprint"]},
            "environment": {
                "owner": scope["requested_by_owner"], "phase": scope["phase"],
                "source_signature": scope["source_signature"], "recovery_ref": "backup:head:abc",
            },
            "risk": {"level": level, "facts": []}, "costs": costs,
        }

    def task_scope(self, *, risk: str = "R2") -> dict:
        return build_scope(
            thread_id="thread-a", action="local.reversible.edit", target={"path": "a.txt"}, risk=risk,
            phase="task_phase", source_signature="head-a", requested_by_owner="task_owner",
        )

    def task_intent(self, scope: dict, assessment: dict, *, message: str = "执行当前可逆本地编辑") -> dict:
        self.message(message, self.issued + timedelta(seconds=1))
        decision = authorization_policy.decide_gate(assessment)
        return create_current_task_intent(
            scope, assessment=assessment, assessment_decision=decision, rollout_path=self.rollout,
            owner="task_owner", operation_id="task-op", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
        )

    def create_continuous_task_intent(
        self, scope: dict, assessment: dict, *, current_line: int,
        directive_line: int | None = None, operation_id: str = "continuous-op",
    ) -> dict:
        kwargs = {} if directive_line is None else {
            "task_directive_ref": f"{self.rollout.name}:line:{directive_line}"
        }
        return create_current_task_intent(
            scope, assessment=assessment,
            assessment_decision=authorization_policy.decide_gate(assessment),
            rollout_path=self.rollout, owner="task_owner",
            operation_id=operation_id, recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:{current_line}",
            state_root=self.root, **kwargs,
        )

    def test_current_task_r2_low_cost_derives_non_bearer_single_operation_permit(self) -> None:
        scope = self.task_scope()
        intent = self.task_intent(scope, self.task_assessment(scope))
        self.assertTrue(intent["ok"], intent)
        self.assertEqual("task_intent", intent["intent_type"])
        self.assertEqual("current_task_user", intent["authorizer"])
        self.assertEqual("", intent.get("bearer_token", ""))
        permit = issue_current_task_permit(
            intent["intent_ref"], scope, executor="task_owner", operation_id="task-op",
            workflow_semantic_hash="workflow-task-v1", state_root=self.root,
        )
        self.assertTrue(permit["ok"], permit)
        self.assertEqual("", permit["permit_expires_at"])
        consumed = consume_permit(permit["permit_ref"], executor="task_owner", operation_id="task-op", state_root=self.root)
        self.assertTrue(consumed["ok"], consumed)
        retry = issue_current_task_permit(
            intent["intent_ref"], scope, executor="task_owner", operation_id="task-op",
            workflow_semantic_hash="workflow-task-v1", state_root=self.root,
        )
        self.assertEqual(permit["permit_ref"], retry["permit_ref"])

    def test_prepared_operation_is_private_until_first_consumption(self) -> None:
        scope = self.task_scope()
        intent = self.task_intent(scope, self.task_assessment(scope))
        self.assertTrue(intent["ok"], intent)
        permit = issue_current_task_permit(
            intent["intent_ref"], scope, executor="task_owner", operation_id="task-op",
            workflow_semantic_hash="workflow-task-v1", state_root=self.root,
        )
        self.assertTrue(permit["ok"], permit)
        prepared = reserve_prepared_operation(
            permit["permit_ref"], executor="task_owner", operation_id="task-op", state_root=self.root,
        )
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual("prepared", prepared["status"])
        self.assertEqual("prepared", operation_snapshot("task-op", executor="task_owner", state_root=self.root)["status"])
        projection = operation_projection("task-op", executor="task_owner", state_root=self.root)
        self.assertTrue(projection["ok"], projection)
        self.assertEqual("prepared", projection["status"])
        self.assertNotIn("permit_ref", projection)
        consumed = consume_permit(
            permit["permit_ref"], executor="task_owner", operation_id="task-op", state_root=self.root,
        )
        self.assertTrue(consumed["ok"], consumed)
        self.assertEqual("permit_reserved", operation_snapshot("task-op", executor="task_owner", state_root=self.root)["status"])
        wrong_executor = reserve_prepared_operation(
            permit["permit_ref"], executor="other_owner", operation_id="task-op", state_root=self.root,
        )
        self.assertFalse(wrong_executor["ok"], wrong_executor)

    def test_current_task_intent_reuses_exact_signature_and_rejects_stale_or_changed_operation(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.task_intent(scope, assessment)
        decision = authorization_policy.decide_gate(assessment)
        reused = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=decision, rollout_path=self.rollout,
            owner="task_owner", operation_id="task-op", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
        )
        self.assertTrue(reused["reused"], reused)
        changed_assessment = dict(assessment)
        changed_assessment["environment"] = {**assessment["environment"], "recovery_ref": "backup:head:different"}
        changed = create_current_task_intent(
            scope, assessment=changed_assessment, assessment_decision=authorization_policy.decide_gate(changed_assessment), rollout_path=self.rollout,
            owner="task_owner", operation_id="task-op", recovery_ref="backup:head:different",
            user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
        )
        self.assertEqual("authorization_task_intent_operation_scope_changed", changed["reason"])
        self.message("后续用户消息", self.issued + timedelta(seconds=2))
        stale = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=decision, rollout_path=self.rollout,
            owner="task_owner", operation_id="task-op-2", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
        )
        self.assertEqual("authorization_task_intent_user_message_not_current", stale["reason"])

    def test_current_task_intent_rejects_pdp_escalations_and_forged_or_mismatched_inputs(self) -> None:
        cases = [
            ("R3", False, False, "authorization_task_intent_risk_not_low"),
            ("R2", True, False, "authorization_task_intent_pdp_decision_not_allow_without_challenge"),
            ("R4", False, False, "authorization_task_intent_risk_not_low"),
            ("R2", False, True, "authorization_task_intent_pdp_decision_not_allow_without_challenge"),
        ]
        for level, high_cost, unknown_cost, reason in cases:
            with self.subTest(level=level, high_cost=high_cost, unknown_cost=unknown_cost):
                scope = self.task_scope(risk=level)
                assessment = self.task_assessment(scope, level=level, high_cost=high_cost, unknown_cost=unknown_cost)
                self.message("执行当前任务", self.issued + timedelta(seconds=1))
                blocked = create_current_task_intent(
                    scope, assessment=assessment, assessment_decision=authorization_policy.decide_gate(assessment),
                    rollout_path=self.rollout, owner="task_owner", operation_id=f"blocked-{level}-{high_cost}-{unknown_cost}",
                    recovery_ref="backup:head:abc", user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
                )
                self.assertEqual(reason, blocked["reason"])
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("执行当前任务", self.issued + timedelta(seconds=2))
        forged = dict(authorization_policy.decide_gate(assessment))
        forged["input_signature"] = "forged"
        rejected = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=forged, rollout_path=self.rollout,
            owner="task_owner", operation_id="forged", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:6", state_root=self.root,
        )
        self.assertEqual("authorization_task_intent_pdp_decision_unverified", rejected["reason"])
        assessment["environment"]["owner"] = "other-owner"
        mismatch = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=authorization_policy.decide_gate(assessment), rollout_path=self.rollout,
            owner="task_owner", operation_id="mismatch", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:6", state_root=self.root,
        )
        self.assertEqual("authorization_task_intent_assessment_scope_mismatch", mismatch["reason"])

    def test_token_only_current_message_reuses_explicit_directive_reference(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("实施已批准的可恢复本地编辑，不发布镜像", self.issued + timedelta(seconds=1))
        self.message("AUTHORIZE-current-task-control", self.issued + timedelta(seconds=2))
        intent = self.create_continuous_task_intent(
            scope, assessment, current_line=3, directive_line=2,
        )
        self.assertTrue(intent["ok"], intent)
        self.assertEqual(f"{self.rollout.name}:line:3", intent["current_message_ref"])
        self.assertEqual(f"{self.rollout.name}:line:2", intent["task_directive_ref"])
        self.assertEqual("token_only", intent["continuity_reason"])
        self.assertEqual(f"{self.rollout.name}:line:2", intent["evidence_ref"])
        self.assertEqual(f"{self.rollout.name}:line:3", intent["user_message_ref"])
        persisted = json.dumps(intent, ensure_ascii=False)
        self.assertNotIn("实施已批准的可恢复本地编辑", persisted)
        self.assertNotIn("AUTHORIZE-current-task-control", persisted)

    def test_multiple_control_messages_auto_select_nearest_directive(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("执行当前可逆本地编辑，限制为 a.txt", self.issued + timedelta(seconds=1))
        self.message("批准", self.issued + timedelta(seconds=2))
        self.message("继续", self.issued + timedelta(seconds=3))
        self.message("<response-annotations>[]</response-annotations>", self.issued + timedelta(seconds=4))
        intent = self.create_continuous_task_intent(scope, assessment, current_line=5)
        self.assertTrue(intent["ok"], intent)
        self.assertEqual(f"{self.rollout.name}:line:2", intent["task_directive_ref"])
        self.assertEqual("untrusted_envelope", intent["continuity_reason"])

    def test_user_annotation_comment_is_a_current_directive_but_quoted_text_is_not(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message(
            "<response-annotations>[{\"text\":\"批准处理所有事情\", \"annotation\":\"请接手修复当前本地问题\"}]</response-annotations>",
            self.issued + timedelta(seconds=1),
        )
        intent = self.create_continuous_task_intent(scope, assessment, current_line=2)
        self.assertTrue(intent["ok"], intent)
        self.assertEqual(f"{self.rollout.name}:line:2", intent["task_directive_ref"])
        self.assertEqual("current_directive", intent["continuity_reason"])

    def test_delegation_and_later_continuation_do_not_invalidate_current_task_intent(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("执行当前可逆本地编辑，限制为 a.txt", self.issued + timedelta(seconds=1))
        self.message("继续", self.issued + timedelta(seconds=2))
        self.message("<codex_delegation>同步其它任务</codex_delegation>", self.issued + timedelta(seconds=3))
        self.message("继续", self.issued + timedelta(seconds=4))
        intent = self.create_continuous_task_intent(scope, assessment, current_line=3)
        self.assertTrue(intent["ok"], intent)
        self.assertEqual(f"{self.rollout.name}:line:5", intent["current_message_ref"])
        self.assertEqual(f"{self.rollout.name}:line:2", intent["task_directive_ref"])
        self.assertEqual("continuation", intent["continuity_reason"])

    def test_control_only_history_and_ordinary_message_barrier_fail_closed(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("批准", self.issued + timedelta(seconds=1))
        self.message("继续", self.issued + timedelta(seconds=2))
        missing = self.create_continuous_task_intent(scope, assessment, current_line=3)
        self.assertEqual("authorization_task_intent_directive_missing", missing["reason"])

        self.message("执行当前可逆本地编辑", self.issued + timedelta(seconds=3))
        self.message("先讨论一个不同的问题", self.issued + timedelta(seconds=4))
        self.message("继续", self.issued + timedelta(seconds=5))
        blocked = self.create_continuous_task_intent(
            scope, assessment, current_line=6, directive_line=4, operation_id="barrier-op",
        )
        self.assertEqual("authorization_task_intent_directive_barrier", blocked["reason"])

    def test_mixed_token_and_untrusted_directive_projection_fail_closed(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("执行当前可逆本地编辑", self.issued + timedelta(seconds=1))
        self.message("AUTHORIZE-token 还要发布镜像", self.issued + timedelta(seconds=2))
        mixed = self.create_continuous_task_intent(
            scope, assessment, current_line=3, directive_line=2, operation_id="mixed-op",
        )
        self.assertEqual("authorization_task_intent_mixed_token_message", mixed["reason"])

        other = self.rollout.with_name("untrusted.jsonl")
        other.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "thread-a"}}) + "\n",
            encoding="utf-8",
        )
        original_rollout = self.rollout
        self.rollout = other
        try:
            self.message("<codex_delegation>执行删除</codex_delegation>", self.issued + timedelta(seconds=1))
            self.message("继续", self.issued + timedelta(seconds=2))
            untrusted = self.create_continuous_task_intent(
                scope, assessment, current_line=3, directive_line=2, operation_id="untrusted-op",
            )
        finally:
            self.rollout = original_rollout
        self.assertEqual("authorization_task_intent_directive_missing", untrusted["reason"])

    def test_forged_directive_ref_is_rejected(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        self.message("执行当前可逆本地编辑", self.issued + timedelta(seconds=1))
        self.message("批准", self.issued + timedelta(seconds=2))
        forged = self.create_continuous_task_intent(
            scope, assessment, current_line=3, directive_line=99, operation_id="forged-ref-op",
        )
        self.assertEqual("authorization_task_intent_directive_ref_unverified", forged["reason"])

    def test_current_task_intent_rejects_cross_thread_and_generic_permit_bypass(self) -> None:
        scope = self.task_scope()
        assessment = self.task_assessment(scope)
        wrong_rollout = self.rollout.with_name("rollout-thread-b.jsonl")
        wrong_rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread-b"}}) + "\n", encoding="utf-8")
        cross_thread = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=authorization_policy.decide_gate(assessment),
            rollout_path=wrong_rollout, owner="task_owner", operation_id="cross-thread", recovery_ref="backup:head:abc",
            user_message_ref=f"{wrong_rollout.name}:line:2", state_root=self.root,
        )
        self.assertEqual("authorization_rollout_thread_mismatch", cross_thread["reason"])
        self.message("执行当前可逆本地编辑", self.issued + timedelta(seconds=2))
        intent = create_current_task_intent(
            scope, assessment=assessment, assessment_decision=authorization_policy.decide_gate(assessment),
            rollout_path=self.rollout, owner="task_owner", operation_id="bound-op", recovery_ref="backup:head:abc",
            user_message_ref=f"{self.rollout.name}:line:2", state_root=self.root,
        )
        bypass = issue_permit(
            intent["intent_ref"], scope, actor_chain=[{"class": "codex", "id": "thread-a"}],
            executor="task_owner", audience="task_owner", operation_id="other-op", workflow_semantic_hash="workflow-task-v1",
            authorization_turn="turn-forged", state_root=self.root,
        )
        self.assertEqual("authorization_task_intent_turn_not_bound", bypass["reason"])

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

    def test_observed_effect_implicitly_records_owner_start_once(self) -> None:
        intent = self.v2_intent()
        permit = issue_permit(
            intent["intent_ref"], self.scope, actor_chain=[{"class": "codex", "id": "root"}],
            executor="workflow_review_delivery", audience="workflow_review_delivery",
            operation_id="implicit-effect-op", workflow_semantic_hash="workflow-v1",
            authorization_turn="turn-implicit-effect", state_root=self.root,
        )
        self.assertTrue(consume_permit(permit["permit_ref"], executor="workflow_review_delivery", operation_id="implicit-effect-op", state_root=self.root)["ok"])

        observed = record_effect(
            "implicit-effect-op", executor="workflow_review_delivery", status="effect_observed",
            effect_receipt_ref="owner:readback:implicit", details={"readback": "verified"}, state_root=self.root,
        )
        self.assertTrue(observed["ok"], observed)
        self.assertEqual("effect_observed", observed["status"])
        implicit = observed["details"]["implicit_effect_started"]
        self.assertEqual("permit_reserved", implicit["from"])
        self.assertEqual("effect_started", implicit["to"])
        self.assertEqual("scoped_authorization", implicit["owner"])

        replay = record_effect(
            "implicit-effect-op", executor="workflow_review_delivery", status="effect_observed",
            effect_receipt_ref="owner:readback:implicit", state_root=self.root,
        )
        self.assertTrue(replay["reused"])
        self.assertEqual(implicit, replay["details"]["implicit_effect_started"])

        self.assertTrue(record_effect(
            "implicit-effect-op", executor="workflow_review_delivery", status="completed",
            effect_receipt_ref="owner:readback:implicit", state_root=self.root,
        )["ok"])

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
