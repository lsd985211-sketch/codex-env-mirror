"""Enqueue command handling for the mobile bridge CLI.

Owns: the `enqueue` command flow from OpenClaw inbound message to queue row,
permission wait/reject handling, context-token attachment, and immediate
received/confirmation status acknowledgements.
Non-goals: worker dispatch, final reply sending, queue schema ownership,
permission policy implementation, or attachment/resource acquisition internals.
State behavior: mutates the mobile queue only through injected queue/callback
APIs that were previously called directly by `mobile_openclaw_cli.main`.
Normal callers: `mobile_openclaw_cli.main` while the legacy CLI remains the
public entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def run_enqueue_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    config_path: Path,
    receiver_account_id: Callable[..., str],
    maybe_handle_control_message: Callable[..., dict[str, Any] | None],
    maybe_complete_capability_passphrase_reply: Callable[..., dict[str, Any] | None],
    send_status_ack: Callable[..., dict[str, Any]],
    materialize_attachments: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    parse_attachments_json: Callable[[str], list[dict[str, Any]]],
    openclaw_context_token_for_user: Callable[..., str],
    set_task_context_token: Callable[..., None],
    schedule_waiting_context_replies: Callable[..., dict[str, Any]],
    enforce_ask_scope_for_task: Callable[..., dict[str, Any]],
    reject_task_for_permission: Callable[..., None],
    print_json: Callable[[dict[str, Any]], None],
) -> int:
    """Run the legacy enqueue command without changing its side effects."""

    account_id = receiver_account_id(config, args.account_id, args.user)
    control = maybe_handle_control_message(
        queue,
        config_path,
        config,
        args.text,
        args.user,
        args.conversation,
        account_id,
    )
    if control is not None:
        print_json(control)
        return 0 if control.get("ok") else 1

    passphrase_reply = maybe_complete_capability_passphrase_reply(
        queue,
        text=args.text,
        actor=args.user,
        account_id=account_id,
        conversation=args.conversation,
    )
    if passphrase_reply is not None:
        reply = send_status_ack(
            queue,
            {
                "id": passphrase_reply.get("task_id") or "",
                "receiver_account_id": account_id,
                "external_user": args.user,
                "external_conversation": args.conversation,
            },
            str(passphrase_reply.get("reply_text") or "口令已处理。"),
            config,
            "status_ack_capability_passphrase_reply",
        )
        passphrase_reply["reply"] = reply
        print_json(passphrase_reply)
        return 0 if passphrase_reply.get("ok") else 1

    attachments = materialize_attachments(parse_attachments_json(args.attachments_json))
    metadata: dict[str, Any] = {
        "msg_id": args.msg_id,
        "transport": "openclaw-weixin",
        "receiver_account_id": account_id,
        "attachment_count": len(attachments),
    }
    if args.run_id:
        metadata["run_id"] = args.run_id

    context_token = str(args.context_token or "").strip()
    context_source = "cli_arg" if context_token else ""
    if not context_token:
        context_token = openclaw_context_token_for_user(config, account_id, args.user)
        context_source = "account_context_file" if context_token else ""
    if context_token:
        metadata["has_context"] = True
        metadata["context_source"] = context_source

    result = queue.enqueue(
        args.text,
        source="openclaw-weixin",
        external_user=args.user,
        external_conversation=args.conversation,
        metadata=metadata,
        attachments=attachments,
    )
    if not result.get("duplicate") and result.get("status") == "pending":
        ask_gate = enforce_ask_scope_for_task(
            queue,
            config,
            {
                "id": result.get("id"),
                "command": result.get("command"),
                "text": args.text,
                "external_user": args.user,
                "external_conversation": args.conversation,
                "receiver_account_id": account_id,
            },
        )
        if not ask_gate.get("allowed"):
            if ask_gate.get("wait_conflict"):
                reject_task_for_permission(
                    queue,
                    str(result.get("id") or ""),
                    str(ask_gate.get("reason") or "capability passphrase wait already active"),
                    ask_gate.get("detail") if isinstance(ask_gate.get("detail"), dict) else ask_gate,
                )
                reply = send_status_ack(
                    queue,
                    {
                        "id": result.get("id"),
                        "receiver_account_id": account_id,
                        "external_user": args.user,
                        "external_conversation": args.conversation,
                    },
                    "当前已有一条受限请求正在等待口令。请先直接回复口令，或回复“取消”后再发起新请求。",
                    config,
                    "status_ack_capability_passphrase_wait_conflict",
                )
                result["status"] = "rejected"
                result["permission_wait_conflict"] = ask_gate
                result["reply"] = reply
                print_json(result)
                return 1
            if ask_gate.get("wait_for_passphrase"):
                reply = send_status_ack(
                    queue,
                    {
                        "id": result.get("id"),
                        "receiver_account_id": account_id,
                        "external_user": args.user,
                        "external_conversation": args.conversation,
                    },
                    "这条请求需要管理员授予的口令后才能继续。请直接回复口令。",
                    config,
                    "status_ack_capability_passphrase_required",
                )
                result["status"] = "waiting_capability_passphrase"
                result["permission_wait"] = ask_gate
                result["reply"] = reply
                print_json(result)
                return 0
            reject_task_for_permission(
                queue,
                str(result.get("id") or ""),
                str(ask_gate.get("reason") or "ask scope denied"),
                ask_gate.get("detail") if isinstance(ask_gate.get("detail"), dict) else ask_gate,
            )
            reply = send_status_ack(
                queue,
                {
                    "id": result.get("id"),
                    "receiver_account_id": account_id,
                    "external_user": args.user,
                    "external_conversation": args.conversation,
                },
                "这条 ask 请求超出了当前权限白名单，不能读取、修改、删除、导出本机数据或执行本机副作用。",
                config,
                "status_ack_permission_rejected",
            )
            result["status"] = "rejected"
            result["permission_rejection"] = ask_gate
            result["reply"] = reply
            print_json(result)
            return 1

    if context_token and result.get("id"):
        set_task_context_token(queue, str(result.get("id") or ""), context_token)
        retry_result = schedule_waiting_context_replies(
            queue,
            config,
            args.user,
            account_id,
            context_token,
            str(result.get("id") or ""),
        )
        if retry_result.get("scheduled"):
            result["context_retry"] = retry_result

    if not result.get("duplicate") and result.get("status") == "pending":
        ack_text = " ".join(args.text.split())[:60]
        send_status_ack(
            queue,
            {
                "id": result.get("id"),
                "receiver_account_id": account_id,
                "external_user": args.user,
                "external_conversation": args.conversation,
            },
            f"📥 已收到：“{ack_text}”",
            config,
            "status_ack_received",
        )
    if result.get("status") == "waiting_confirmation":
        reply = send_status_ack(
            queue,
            {
                "id": result.get("id"),
                "receiver_account_id": account_id,
                "external_user": args.user,
                "external_conversation": args.conversation,
            },
            "这条手机任务被判定为高危任务。请直接回复密钥确认；密钥只会确认最近一条等待中的高危任务。",
            config,
            "status_ack_confirmation_required",
        )
        result["confirmation_prompt_reply"] = reply
    print_json(result)
    return 0
