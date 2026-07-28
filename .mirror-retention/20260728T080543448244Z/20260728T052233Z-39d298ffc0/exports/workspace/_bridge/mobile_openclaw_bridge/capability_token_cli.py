"""Temporary capability-token CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and command dispatch for admin-granted temporary
capability tokens, including grant/revoke permission checks and artifact-dir
lookup.
Non-goals: grant persistence format, passphrase hashing, permission policy
semantics, bridge queue mutation, or Weixin delivery.
State behavior: grant/revoke actions write only through capability_tokens;
snapshot, doctor, repair-plan, metrics, validate, and artifact-dir are read-only.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main
when args.cmd == "capability-token".
"""

from __future__ import annotations

import os
from typing import Any

import capability_tokens
import permission_policy
from openclaw_accounts import permission_account_map


def register_capability_token_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("capability-token", help="Admin-granted temporary capability tokens")
    parser.add_argument("action", choices=["grant", "revoke", "snapshot", "doctor", "repair-plan", "metrics", "validate", "artifact-dir"])
    parser.add_argument("--actor", default="", help="Granting admin Weixin user; defaults to primary account user")
    parser.add_argument("--subject-account-id", default="", help="OpenClaw account id receiving the grant")
    parser.add_argument("--subject-user", default="", help="Weixin user id receiving the grant")
    parser.add_argument("--capability", action="append", default=[], help="Capability to grant; may repeat")
    parser.add_argument("--ttl-minutes", type=int, default=60)
    parser.add_argument("--expires-at", default="", help="Absolute ISO-8601 expiry time; overrides --ttl-minutes")
    parser.add_argument("--no-expiry", action="store_true", help="Create a grant without time expiry; max-uses and revoke still apply")
    parser.add_argument("--max-uses", type=int, default=3)
    parser.add_argument("--unlimited-uses", action="store_true", help="Create a grant without use-count expiry; time expiry and revoke still apply")
    parser.add_argument("--reason", default="")
    parser.add_argument("--grant-id", default="")
    parser.add_argument("--passphrase", default="", help="Optional short challenge phrase; stored only as a salted hash")
    parser.add_argument("--passphrase-env", default="", help="Environment variable containing the challenge phrase")


def _default_capabilities(args: Any) -> list[str]:
    return list(args.capability or []) or [
        "generated_file_create",
        "generated_file_send",
        "reply_with_generated_artifact",
    ]


def _actor_and_account_map(config: dict[str, Any], args: Any) -> tuple[str, dict[str, Any]]:
    account_map = permission_account_map(config)
    actor = str(args.actor or permission_policy.primary_admin_user(config, account_map) or "").strip()
    return actor, account_map


def _admin_gate(config: dict[str, Any], args: Any, actor: str, account_map: dict[str, Any]) -> dict[str, Any] | None:
    if args.action not in {"grant", "revoke"}:
        return None
    admin_decision = permission_policy.decide(config, actor, "capability_token_admin", "", account_map)
    if permission_policy.role_for_actor(config, actor, account_map) == "admin" and admin_decision.allowed:
        return None
    return {
        "ok": False,
        "reason": "capability tokens can only be granted or revoked by the primary admin",
        "permission": admin_decision.to_dict(),
    }


def run_capability_token_command(args: Any, config: dict[str, Any]) -> dict[str, Any]:
    actor, account_map = _actor_and_account_map(config, args)
    gate = _admin_gate(config, args, actor, account_map)
    if gate is not None:
        return gate

    if args.action == "grant":
        subject_account_id = str(args.subject_account_id or "").strip()
        subject_user = str(args.subject_user or "").strip()
        if not subject_account_id and subject_user:
            subject_account_id = permission_policy.bound_account_id(config, subject_user, account_map)
        passphrase = str(args.passphrase or "")
        if not passphrase and str(args.passphrase_env or "").strip():
            passphrase = str(os.environ.get(str(args.passphrase_env).strip()) or "")
        return capability_tokens.grant(
            subject_account_id=subject_account_id,
            subject_user=subject_user,
            capabilities=_default_capabilities(args),
            issued_by=actor,
            ttl_minutes=int(args.ttl_minutes or 60),
            expires_at=str(args.expires_at or ""),
            no_expiry=bool(args.no_expiry),
            max_uses=int(args.max_uses or 3),
            unlimited_uses=bool(args.unlimited_uses),
            reason=str(args.reason or ""),
            passphrase=passphrase,
        )

    if args.action == "revoke":
        if not str(args.grant_id or "").strip():
            return {"ok": False, "reason": "--grant-id is required"}
        return capability_tokens.revoke(str(args.grant_id), revoked_by=actor, reason=str(args.reason or ""))

    if args.action == "snapshot":
        return capability_tokens.snapshot()
    if args.action == "doctor":
        return capability_tokens.doctor()
    if args.action == "repair-plan":
        return capability_tokens.repair_plan()
    if args.action == "metrics":
        return capability_tokens.metrics()
    if args.action == "validate":
        return capability_tokens.validate()

    account_id = str(args.subject_account_id or "").strip()
    subject_user = str(args.subject_user or "").strip()
    if not account_id and subject_user:
        account_id = permission_policy.bound_account_id(config, subject_user, account_map)
    return {
        "ok": bool(account_id),
        "account_id": account_id,
        "artifact_dir": str(capability_tokens.generated_artifact_dir(account_id)) if account_id else "",
        "policy": "generated artifacts for temporary grants must stay inside this account-scoped attachment directory",
    }
