"""Control-message runtime for the mobile bridge CLI.

Owns: dispatching user-visible bridge control messages such as status, repair,
thread switching, stop/resume, and L3 confirmation replies.
Non-goals: worker scheduling, mobile task execution, final result parsing, or
permission policy definitions.
State behavior: mutates queue/config only through the legacy helper calls used
by the CLI facade; execution is rebound to the CLI global namespace to preserve
existing monkeypatch and helper lookup behavior.
Normal caller: `mobile_openclaw_cli.maybe_handle_control_message`.
"""

from __future__ import annotations

from types import FunctionType
from typing import Any


def maybe_handle_control_message(
    queue: MobileQueue,
    config_path: Path,
    config: dict[str, Any],
    text: str,
    external_user: str,
    external_conversation: str,
    account_id: str = "",
) -> dict[str, Any] | None:
    account_map = permission_account_map(config)
    ask_decision = permission_policy.decide(config, external_user, "ask", account_id, account_map)
    thread_decision = permission_policy.decide(config, external_user, "thread_switch", account_id, account_map)
    allowed = ask_decision.allowed
    admin = permission_policy.role_for_actor(config, external_user, account_map) == "admin"
    command = exact_control_command(text)
    repair_mode = parse_repair_control_command(text)
    reply_account_id = receiver_account_id(config, account_id, external_user)
    reply_task = control_reply_task(external_user, external_conversation, reply_account_id)
    candidate = (text or "").strip()

    if thread_switch_trigger(candidate):
        receipt_id = control_receipt_id("thread_switch", candidate, external_user, reply_account_id, external_conversation)
        if not thread_decision.allowed:
            queue.add_event(
                "openclaw-weixin",
                "thread_switch_rejected",
                {"receipt_id": receipt_id, "reason": thread_decision.reason, "permission": thread_decision.to_dict()},
            )
            return {"ok": False, "control": "thread_switch", "status": "rejected", "reason": thread_decision.reason}
        active = get_active_thread(queue, config, external_user)
        mark_waiting_thread_selection(queue, config, external_user)
        reply = send_control_reply(
            queue,
            reply_task,
            thread_menu_text(config, active["id"] if active else ""),
            config,
            "thread_switch",
            receipt_id=receipt_id,
        )
        return {"ok": True, "control": "thread_menu", "reply": reply}

    if allowed and is_waiting_thread_selection(queue, external_user):
        receipt_id = control_receipt_id("thread_selected", candidate, external_user, reply_account_id, external_conversation)
        selected = find_thread(config, candidate)
        if selected:
            set_active_thread(queue, external_user, selected["id"])
            clear_waiting_thread_selection(queue, external_user)
            queue.add_event(
                "openclaw-weixin",
                "thread_switched",
                {"receipt_id": receipt_id, "id": selected["id"], "name": selected["name"]},
            )
            reply = send_control_reply(
                queue,
                reply_task,
                f"已切换到项目线程：{selected['name']}",
                config,
                "thread_selected",
                receipt_id=receipt_id,
            )
            return {"ok": True, "control": "thread_selected", "id": selected["id"], "name": selected["name"], "reply": reply}
        clear_waiting_thread_selection(queue, external_user)
        reply = send_control_reply(
            queue,
            reply_task,
            "未找到这个项目线程，已取消本次切换。请重新发送“切换线程”查看可选列表。",
            config,
            "thread_selected",
            receipt_id=receipt_id,
        )
        return {"ok": False, "control": "thread_selected", "status": "not_found", "reply": reply}

    if command == "status":
        receipt_id = control_receipt_id(command, candidate, external_user, reply_account_id, external_conversation)
        status_self_decision = permission_policy.decide(config, external_user, "status_self", account_id, account_map)
        if not status_self_decision.allowed:
            queue.add_event(
                "openclaw-weixin",
                "control_rejected",
                {"receipt_id": receipt_id, "command": command, "reason": status_self_decision.reason, "permission": status_self_decision.to_dict()},
            )
            reply = send_control_reply(
                queue,
                reply_task,
                "status 已收到，但当前微信用户未被允许触发桥接。请联系主账号授权。",
                config,
                command,
                receipt_id=receipt_id,
            )
            return {
                "ok": False,
                "control": command,
                "status": "rejected",
                "reason": status_self_decision.reason,
                "reply": reply,
            }
        status_global_decision = permission_policy.decide(config, external_user, "status_global", account_id, account_map)
        if status_global_decision.allowed:
            result = bridge_status(queue, config)
            reply = send_control_reply(
                queue,
                reply_task,
                status_reply_text(result),
                config,
                command,
                receipt_id=receipt_id,
            )
            result["control"] = "status"
            result["reply"] = reply
            return result
        queue.add_event(
            "openclaw-weixin",
            "user_status_replied",
            {"receipt_id": receipt_id, "command": command, "reason": "non-admin user-level status"},
        )
        reply = send_control_reply(
            queue,
            reply_task,
            user_status_reply_text(queue, config, external_user),
            config,
            command,
            receipt_id=receipt_id,
        )
        return {
            "ok": True,
            "control": command,
            "status": "user_status",
            "reply": reply,
        }

    if command in {"stop", "resume", "hardstop", "repair"}:
        receipt_id = control_receipt_id(command, candidate, external_user, reply_account_id, external_conversation)
        action = "repair_system" if command == "repair" else command
        decision = permission_policy.decide(config, external_user, action, account_id, account_map)
        if not decision.allowed:
            queue.add_event(
                "openclaw-weixin",
                "control_rejected",
                {"receipt_id": receipt_id, "command": command, "reason": decision.reason, "permission": decision.to_dict()},
            )
            reply = send_control_reply(
                queue,
                reply_task,
                f"{command} 仅主账号可用。你的消息已收到，但不会执行全局控制操作。",
                config,
                command,
                receipt_id=receipt_id,
            )
            return {
                "ok": False,
                "control": command,
                "status": "rejected",
                "reason": decision.reason,
                "reply": reply,
            }
        if command == "repair":
            result = run_mobile_system_maintenance_control(
                apply_safe=True,
                external_user=external_user,
                account_id=str(reply_task.get("receiver_account_id") or ""),
            )
            queue.add_event(
                "openclaw-weixin",
                "system_maintenance_control_started",
                {
                    "command": "repair",
                    "ok": bool(result.get("ok")),
                    "receipt_id": receipt_id,
                    "request_id": result.get("request_id", ""),
                    "pid": result.get("pid", 0),
                    "log_path": result.get("log_path", ""),
                    "trigger_user": external_user,
                    "trigger_account": str(reply_task.get("receiver_account_id") or ""),
                    "policy": "mobile repair triggers total computer maintenance safe-apply boundary",
                },
            )
            reply = send_control_reply(
                queue,
                reply_task,
                compact_system_maintenance_reply_text(result),
                config,
                command,
                receipt_id=receipt_id,
                extra={"request_id": result.get("request_id", ""), "started": bool(result.get("started")), "skipped": bool(result.get("skipped"))},
            )
            result["reply"] = reply
            return result
        if command == "stop":
            result = emergency_stop(queue, config_path, config, actor=external_user)
            interrupt_ok = bool(result.get("codex_interrupt", {}).get("ok"))
            reply = send_control_reply(
                queue,
                reply_task,
                "mobile bridge 已急停：已暂停投递、切换 shadow、停止 worker、禁用自动启动任务；"
                + ("已尝试停止当前 Codex 生成。" if interrupt_ok else "未能确认停止当前 Codex 生成，请看本机状态。"),
                config,
                command,
                receipt_id=receipt_id,
            )
            result["reply"] = reply
            return result
        if command == "resume":
            result = resume_bridge(queue, config_path, config, actor=external_user)
            reply = send_control_reply(
                queue,
                reply_task,
                "mobile bridge 已恢复：已清理急停标记、关闭 shadow，并启动 worker。",
                config,
                command,
                receipt_id=receipt_id,
            )
            result["reply"] = reply
            return result
        result = emergency_stop(queue, config_path, config, actor=external_user)
        result["control"] = "hardstop"
        reply = send_control_reply(
            queue,
            reply_task,
            "hardstop 已执行：已急停桥接，并尝试停止当前 Codex 生成。",
            config,
            command,
            receipt_id=receipt_id,
        )
        result["reply"] = reply
        return result

    if repair_mode:
        receipt_id = control_receipt_id(f"repair_bridge:{repair_mode}", candidate, external_user, reply_account_id, external_conversation)
        decision = permission_policy.decide(config, external_user, "repair_bridge", account_id, account_map)
        if not decision.allowed:
            queue.add_event(
                "openclaw-weixin",
                "control_rejected",
                {"receipt_id": receipt_id, "command": "repair", "mode": repair_mode, "reason": decision.reason, "permission": decision.to_dict()},
            )
            reply = send_control_reply(queue, reply_task, "repair bridge 仅主账号可用。你的消息已收到，但不会执行桥接修复。", config, "repair_bridge", receipt_id=receipt_id)
            return {
                "ok": False,
                "control": "repair",
                "mode": repair_mode,
                "status": "rejected",
                "reason": decision.reason,
                "reply": reply,
            }
        result = run_mobile_repair_control(queue, config, repair_mode, apply_safe=True)
        queue.add_event(
            "openclaw-weixin",
                "repair_control_completed",
                {
                    "mode": repair_mode,
                    "ok": bool(result.get("ok")),
                    "receipt_id": receipt_id,
                    "applied": bool(result.get("applied")),
                    "policy": "mobile repair bridge runs bridge maintenance-safe repairs only; reply send remains excluded",
                },
            )
        reply = send_control_reply(
            queue,
            reply_task,
            compact_repair_reply_text(repair_mode, result),
            config,
            "repair_bridge",
            receipt_id=receipt_id,
            extra={"mode": repair_mode, "applied": bool(result.get("applied"))},
        )
        result["reply"] = reply
        return result

    secret_hash = queue.confirmation_secret_hash()
    confirm_decision = permission_policy.decide(config, external_user, "confirm_l3", account_id, account_map)
    if confirm_decision.allowed and candidate and secret_hash and sha256_text(candidate) == secret_hash:
        receipt_id = control_receipt_id("confirm_l3", candidate, external_user, reply_account_id, external_conversation)
        waiting = queue.latest_waiting_confirmation(external_user)
        if waiting:
            ok, message, task = queue.confirm_latest(candidate, external_user)
            if ok:
                worker = start_worker_once(config_path, task_id=str(task.get("id") if task else ""))
                reply_text = "密钥通过，最近一条高危任务已进入执行队列。"
            else:
                worker = None
                reply_text = f"密钥未通过：{message}"
            reply = send_control_reply(queue, reply_task, reply_text, config, "confirm_l3", receipt_id=receipt_id)
            return {
                "ok": ok,
                "control": "confirm_latest",
                "status": "confirmed" if ok else "failed",
                "task_id": task.get("id") if task else None,
                "message": message,
                "worker_once": worker,
                "reply": reply,
            }
        reply = send_control_reply(queue, reply_task, "没有等待密钥确认的高危任务。", config, "confirm_l3", receipt_id=receipt_id)
        return {
            "ok": True,
            "control": "confirm_latest",
            "status": "ignored",
            "reason": "no task is waiting for confirmation",
            "reply": reply,
        }

    return None


def run_control_message_handler(env: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Run the moved control-message handler with the CLI facade globals."""
    rebound = FunctionType(
        maybe_handle_control_message.__code__,
        env,
        "maybe_handle_control_message",
        maybe_handle_control_message.__defaults__,
        maybe_handle_control_message.__closure__,
    )
    return rebound(*args, **kwargs)
