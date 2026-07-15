"""Permission gate for worker dispatch scanning.

Owns: the per-task permission gate that runs during `worker_once` before a
pending task can become dispatchable, including capability passphrase wait,
wait-conflict rejection, ordinary ask-scope rejection, and user-facing status
acknowledgements.
Non-goals: permission policy calculation, token/grant storage, queue schema,
Codex dispatch, final replies, or enqueue-time permission handling.
State behavior: mutates only through injected queue/callback APIs previously
called directly by `mobile_openclaw_cli.worker_once`.
Normal callers: `mobile_openclaw_cli.worker_once` dispatch scan.
"""

from __future__ import annotations

from typing import Any, Callable


def enforce_worker_dispatch_permission(
    queue: Any,
    config: dict[str, Any],
    task: dict[str, Any],
    *,
    task_id: str,
    enforce_ask_scope_for_task: Callable[[Any, dict[str, Any], dict[str, Any]], dict[str, Any]],
    reject_task_for_permission: Callable[..., None],
    send_status_ack: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Return allowed=false after applying the legacy worker permission side effects."""

    ask_gate = enforce_ask_scope_for_task(queue, config, task)
    if ask_gate.get("allowed"):
        return {"allowed": True, "ask_gate": ask_gate}

    if ask_gate.get("wait_conflict"):
        reject_task_for_permission(
            queue,
            task_id,
            str(ask_gate.get("reason") or "capability passphrase wait already active"),
            ask_gate.get("detail") if isinstance(ask_gate.get("detail"), dict) else ask_gate,
        )
        send_status_ack(
            queue,
            task,
            "当前已有一条受限请求正在等待口令。请先直接回复口令，或回复“取消”后再发起新请求。",
            config,
            "status_ack_capability_passphrase_wait_conflict",
        )
        return {"allowed": False, "reason": "wait_conflict", "ask_gate": ask_gate}

    if ask_gate.get("wait_for_passphrase"):
        send_status_ack(
            queue,
            task,
            "这条请求需要管理员授予的口令后才能继续。请直接回复口令。",
            config,
            "status_ack_capability_passphrase_required",
        )
        return {"allowed": False, "reason": "wait_for_passphrase", "ask_gate": ask_gate}

    reject_task_for_permission(
        queue,
        task_id,
        str(ask_gate.get("reason") or "ask scope denied"),
        ask_gate.get("detail") if isinstance(ask_gate.get("detail"), dict) else ask_gate,
    )
    send_status_ack(
        queue,
        task,
        "这条 ask 请求超出了当前权限白名单，不能读取、修改、删除、导出本机数据或执行本机副作用。",
        config,
        "status_ack_permission_rejected",
    )
    return {"allowed": False, "reason": "ask_scope_denied", "ask_gate": ask_gate}
