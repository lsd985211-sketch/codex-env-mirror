"""Capability-token passphrase regression checks for the mobile bridge.

Owns: temp-only self-tests for generated-artifact capability grants, passphrase
challenge flow, prompt exposure gating, and cancellation/conflict behavior.
Non-goals: production permission decisions, token storage implementation, or
worker dispatch logic.
State behavior: checks use synthetic queues and temporary token stores; each
check is rebound to the CLI global namespace to preserve legacy fixture behavior
after extraction.
Normal caller: `mobile_openclaw_cli` facade functions preserving CLI command
names.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def run_capability_passphrase_regression_check(name: str, env: dict[str, Any]) -> dict[str, Any]:
    """Run a moved capability-passphrase regression check in the CLI global namespace."""
    try:
        check = _CHECKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown capability passphrase regression check: {name}") from exc
    rebound = FunctionType(check.__code__, env, name, check.__defaults__, check.__closure__)
    return rebound()

def capability_passphrase_state_machine_check() -> dict[str, Any]:
    original_store = capability_tokens.STORE_PATH
    original_audit_log = capability_tokens.AUDIT_LOG
    original_attachments_root = capability_tokens.ATTACHMENTS_ROOT

    def make_env(root: Path) -> tuple[MobileQueue, dict[str, Any]]:
        capability_tokens.STORE_PATH = root / "capability_grants.json"
        capability_tokens.AUDIT_LOG = root / "capability-grants.jsonl"
        capability_tokens.ATTACHMENTS_ROOT = root / "attachments"
        queue = MobileQueue(root / "queue.db")
        config = {
            "security": {"allowed_users": ["backup_user"]},
            "openclaw_accounts": {"backup1": {"userId": "backup_user", "token": "present"}},
            "permissions": {
                "users": {"backup_user": {"role": "user", "allowed_actions": ["ask"]}},
                "profiles": {"user": {"allowed_actions": ["ask"]}},
            },
        }
        capability_tokens.grant(
            subject_account_id="backup1",
            subject_user="backup_user",
            capabilities=["generated_file_create", "generated_file_send", "reply_with_generated_artifact"],
            issued_by="primary",
            ttl_minutes=60,
            max_uses=5,
            passphrase="szp",
        )
        return queue, config

    def enqueue_waiting(queue: MobileQueue, config: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        task = queue.enqueue(
            text,
            source="openclaw-weixin",
            external_user="backup_user",
            external_conversation="conv1",
            metadata={"receiver_account_id": "backup1"},
        )
        gate = enforce_ask_scope_for_task(
            queue,
            config,
            {
                "id": task.get("id"),
                "command": task.get("command"),
                "text": text,
                "external_user": "backup_user",
                "external_conversation": "conv1",
                "receiver_account_id": "backup1",
                "metadata_json": task.get("metadata_json") or "{}",
            },
        )
        return task, gate, queue.get_task(str(task.get("id") or "")) or {}

    checks: dict[str, Any] = {}
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue, config = make_env(Path(temp_dir))
            task, gate, waiting = enqueue_waiting(queue, config, "生成一个文件发给我")
            unverified_prompt_context = permission_policy.compact_codex_context(
                config,
                "backup_user",
                "backup1",
                "",
                permission_account_map(config),
            )
            unlock = maybe_complete_capability_passphrase_reply(
                queue,
                text="szp",
                actor="backup_user",
                account_id="backup1",
                conversation="conv1",
            )
            restored = queue.get_task(str(task.get("id") or "")) or {}
            pending = queue.list_pending(10, preferred_task_id=str(task.get("id") or ""))
            worker_gate = enforce_ask_scope_for_task(queue, config, pending[0]) if pending else {"allowed": False, "reason": "pending_not_listed"}
            verified_prompt_context = permission_policy.compact_codex_context(
                config,
                "backup_user",
                "backup1",
                "",
                permission_account_map(config),
                include_temporary_capabilities=bool(
                    pending
                    and task_capability_passphrase_verified(
                        pending[0],
                        ["generated_file_create", "generated_file_send"],
                        "generated_artifact_create_or_send",
                    )
                ),
            )
            checks["correct_passphrase_restores_worker_visible_task"] = {
                "ok": bool(
                    not gate.get("allowed")
                    and gate.get("wait_for_passphrase")
                    and waiting.get("status") == "waiting_capability_passphrase"
                    and unlock
                    and unlock.get("ok")
                    and restored.get("status") == "pending"
                    and pending
                    and "metadata_json" in pending[0]
                    and worker_gate.get("allowed")
                ),
                "worker_gate": {"allowed": worker_gate.get("allowed"), "reason": worker_gate.get("reason")},
                "pending_has_metadata": bool(pending and "metadata_json" in pending[0]),
            }
            checks["active_grant_without_passphrase_not_prompt_exposed"] = {
                "ok": bool(
                    gate.get("wait_for_passphrase")
                    and not unverified_prompt_context.get("temporary_capabilities")
                    and not unverified_prompt_context.get("generated_artifact_dir")
                    and verified_prompt_context.get("temporary_capabilities")
                    and verified_prompt_context.get("generated_artifact_dir")
                ),
                "gate": {"allowed": gate.get("allowed"), "reason": gate.get("reason"), "wait_for_passphrase": gate.get("wait_for_passphrase")},
                "unverified_context": {
                    "temporary_capabilities": unverified_prompt_context.get("temporary_capabilities"),
                    "generated_artifact_dir": unverified_prompt_context.get("generated_artifact_dir"),
                },
                "verified_context": {
                    "temporary_capabilities": verified_prompt_context.get("temporary_capabilities"),
                    "generated_artifact_dir": verified_prompt_context.get("generated_artifact_dir"),
                },
            }
        with tempfile.TemporaryDirectory() as temp_dir:
            queue, config = make_env(Path(temp_dir))
            task, _, _ = enqueue_waiting(queue, config, "生成一个文件发给我")
            neutral = maybe_complete_capability_passphrase_reply(
                queue,
                text="ok",
                actor="backup_user",
                account_id="backup1",
                conversation="conv1",
            )
            store = capability_tokens.read_store()
            checks["neutral_word_not_consumed_or_counted"] = {
                "ok": neutral is None and int(store["grants"][0].get("passphrase_failed_count") or 0) == 0 and (queue.get_task(str(task.get("id") or "")) or {}).get("status") == "waiting_capability_passphrase",
                "handled": neutral is not None,
                "failed_count": int(store["grants"][0].get("passphrase_failed_count") or 0),
            }
        with tempfile.TemporaryDirectory() as temp_dir:
            queue, config = make_env(Path(temp_dir))
            first, first_gate, _ = enqueue_waiting(queue, config, "生成一个文件A发给我")
            second = queue.enqueue(
                "生成一个文件B发给我",
                source="openclaw-weixin",
                external_user="backup_user",
                external_conversation="conv1",
                metadata={"receiver_account_id": "backup1"},
            )
            second_gate = enforce_ask_scope_for_task(
                queue,
                config,
                {
                    "id": second.get("id"),
                    "command": second.get("command"),
                    "text": "生成一个文件B发给我",
                    "external_user": "backup_user",
                    "external_conversation": "conv1",
                    "receiver_account_id": "backup1",
                    "metadata_json": second.get("metadata_json") or "{}",
                },
            )
            unlock = maybe_complete_capability_passphrase_reply(
                queue,
                text="szp",
                actor="backup_user",
                account_id="backup1",
                conversation="conv1",
            )
            checks["single_waiting_task_per_conversation"] = {
                "ok": bool(first_gate.get("wait_for_passphrase") and second_gate.get("wait_conflict") and unlock and unlock.get("task_id") == first.get("id")),
                "first_status": (queue.get_task(str(first.get("id") or "")) or {}).get("status"),
                "second_gate": {"allowed": second_gate.get("allowed"), "reason": second_gate.get("reason"), "wait_conflict": second_gate.get("wait_conflict")},
                "unlocked_task": unlock.get("task_id") if unlock else "",
            }
        with tempfile.TemporaryDirectory() as temp_dir:
            queue, config = make_env(Path(temp_dir))
            task, _, _ = enqueue_waiting(queue, config, "生成一个文件发给我")
            cancel = maybe_complete_capability_passphrase_reply(
                queue,
                text="取消",
                actor="backup_user",
                account_id="backup1",
                conversation="conv1",
            )
            checks["cancel_closes_waiting_task"] = {
                "ok": bool(cancel and cancel.get("ok") and (queue.get_task(str(task.get("id") or "")) or {}).get("status") == "cancelled"),
                "cancel": {"ok": cancel.get("ok") if cancel else None, "status": cancel.get("status") if cancel else ""},
            }
    finally:
        capability_tokens.STORE_PATH = original_store
        capability_tokens.AUDIT_LOG = original_audit_log
        capability_tokens.ATTACHMENTS_ROOT = original_attachments_root

    failed = {name: item for name, item in checks.items() if not item.get("ok")}
    return {
        "schema": "capability-passphrase-state-machine-check/v1",
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "read_only": True,
        "uses_temp_state_only": True,
    }

_CHECKS = {
    "capability_passphrase_state_machine_check": capability_passphrase_state_machine_check,
}
