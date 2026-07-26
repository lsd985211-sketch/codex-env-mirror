"""Pure Weixin bridge status and notice text formatting.

Owns: turning already-collected bridge status, per-user counters, and batch
notice task dictionaries into Chinese reply text.
Non-goals: querying SQLite, checking permissions, mutating runtime state,
sending messages, or deciding whether a notice should be sent.
State behavior: stateless and deterministic for the supplied inputs.
Normal caller context: mobile_openclaw_cli.py facade functions after they have
already gathered queue/config evidence.
"""

from __future__ import annotations

from typing import Any


def bridge_status_reply_text(status: dict[str, Any]) -> str:
    """Format a read-only bridge status snapshot for a Weixin status reply."""
    worker_count = None
    worker = status.get("worker_processes")
    if isinstance(worker, dict):
        worker_count = worker.get("count")

    task_state = "unknown"
    task = status.get("scheduled_task")
    if isinstance(task, dict):
        task_state = str(task.get("state") or ("missing" if not task.get("existed") else "unknown"))

    counts = status.get("status_counts") if isinstance(status.get("status_counts"), dict) else {}
    active_codex_tasks = status.get("active_codex_tasks")
    latest_stderr = status.get("latest_worker_stderr", {}) if isinstance(status.get("latest_worker_stderr"), dict) else {}
    latest_stderr_state = str(latest_stderr.get("state") or ("clean" if latest_stderr.get("ok") else "unknown"))

    ports_ok = _all_health_ok(status.get("ports"))
    file_ok = _all_health_ok(status.get("file_health"))
    scheduled_ok = bool(status.get("scheduled_task", {}).get("ok"))
    integrity_ok = str(status.get("integrity_check") or "").lower() == "ok"
    active_codex_count = len(active_codex_tasks) if isinstance(active_codex_tasks, list) else 0

    return "\n".join(
        [
            f"桥接状态：{'正常' if bool(status.get('ok')) else '异常'}",
            f"完整性检查：{'通过' if integrity_ok else '未通过'}",
            f"暂停：{'是' if bool(status.get('paused')) else '否'}",
            f"影子模式：{'是' if bool(status.get('shadow_mode')) else '否'}",
            f"STOP_REQUEST：{'存在' if bool(status.get('stop_request')) else '无'}",
            f"worker 进程数：{worker_count if worker_count is not None else '未知'}",
            f"计划任务：{task_state}",
            f"待处理：{counts.get('pending', 0)}",
            f"待确认：{counts.get('waiting_confirmation', 0)}",
            f"当前 Codex 活动任务：{active_codex_count}",
            f"最新 worker stderr：{_worker_stderr_label(latest_stderr_state)}",
            f"端口健康：{'正常' if ports_ok else '异常'}",
            f"文件健康：{'正常' if file_ok else '异常'}",
            f"计划任务健康：{'正常' if scheduled_ok else '异常'}",
            "说明：本回执为中文摘要；status 只读，不会修改当前任务状态。",
        ]
    )


def user_status_reply_text(
    *,
    allowed: bool,
    admin: bool,
    active_name: str,
    counts: dict[str, int],
    recent_status: str,
    recent_task_id: str,
    recent_updated: str,
) -> str:
    """Format a per-user status reply from already-collected state."""
    pending = counts.get("pending", 0)
    active_count = sum(counts.get(status, 0) for status in ("queued_for_codex", "sent_to_codex", "processing"))
    pushed = counts.get("pushed_to_wecom", 0)
    failed = counts.get("failed", 0) + counts.get("push_failed", 0) + counts.get("codex_timeout", 0)

    return "\n".join(
        [
            "你的微信桥接状态：已收到 status",
            f"触发权限：{'允许' if allowed else '未允许'}",
            f"管理员权限：{'是' if admin else '否'}",
            f"当前项目线程：{active_name}",
            f"你的待处理任务：{pending}",
            f"你的处理中任务：{active_count}",
            f"你的已回发任务：{pushed}",
            f"你的异常/失败任务：{failed}",
            f"最近任务：{recent_status}" + (f" ({recent_task_id})" if recent_task_id else ""),
            f"最近更新时间：{recent_updated or '无'}",
            "同步说明：后台独立线程以 Codex turns 正文为准；电脑线程列表预览可能陈旧。",
            "说明：这是用户级 status；stop/resume/hardstop 仍仅主账号可用。",
        ]
    )


def reply_pending_batch_notice(tasks: list[dict[str, Any]]) -> str:
    """Format a notice for previously reply-pending tasks that were redelivered."""
    if not tasks:
        return ""
    sorted_tasks = sorted(tasks, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
    lines = ["已补发以下积压信息："]
    for idx, task in enumerate(sorted_tasks, start=1):
        lines.append(f"{idx}. {_task_text(task)}")
    lines.append("说明：这些内容此前因线程状态或回发失败而积压，当前已恢复投递；后续思考结果会在这批内容发完后单独发送。")
    return "\n".join(lines).strip()


def message_supplement_notice(batch_tasks: list[dict[str, Any]]) -> str:
    """Format a notice for supplement messages accepted into the active batch."""
    if not batch_tasks:
        return ""
    sorted_tasks = sorted(batch_tasks, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    lines = ["已收到补充信息，已纳入当前对话批次："]
    for idx, task in enumerate(sorted_tasks, start=1):
        lines.append(f"{idx}. {_task_text(task)}")
    lines.append("说明：这些补充会和当前回复一起整理后继续处理。")
    return "\n".join(lines).strip()


def _all_health_ok(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    return all(bool(item.get("ok")) for item in value.values() if isinstance(item, dict))


def _worker_stderr_label(latest_stderr_state: str) -> str:
    if latest_stderr_state == "unknown":
        return "未采集"
    if latest_stderr_state == "clean":
        return "正常"
    return "有输出"


def _task_text(task: dict[str, Any]) -> str:
    text = str(task.get("text") or "").strip()
    return text if text else "（无正文）"
