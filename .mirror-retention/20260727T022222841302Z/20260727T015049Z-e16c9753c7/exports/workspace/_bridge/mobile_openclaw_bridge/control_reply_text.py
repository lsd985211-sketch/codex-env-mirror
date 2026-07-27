"""User-facing text builders for mobile control replies.

Owns: compact Weixin reply text for repair and maintenance control commands.
Non-goals: starting repairs, queue mutation, permission checks, or delivery.
"""

from __future__ import annotations

from typing import Any


def compact_system_maintenance_reply_text(report: dict[str, Any]) -> str:
    if report.get("async"):
        lines = ["电脑维护已开始后台执行。"]
        request_id = str(report.get("request_id") or "")
        if request_id:
            lines.append("请求：" + request_id)
        log_path = str(report.get("log_path") or "")
        if log_path:
            lines.append("日志：" + log_path)
        lines.append("如果已有维护在运行，后台作业会自动记录 lock_held 并跳过，不会重复执行。")
        lines.append("边界：不杀主 Codex、不关闭 Defender、不重发邮件、不直接修改桥接队列。")
        return "\n".join(lines)
    if report.get("skipped") and report.get("reason") == "maintenance_already_running":
        return "电脑维护已收到；当前已有维护任务在运行，本次不重复启动。"
    if report.get("timed_out"):
        return "电脑维护已触发但超时未完成；没有执行未授权动作，请到系统维护执行记录查看详情。"
    if report.get("skipped"):
        return "电脑维护已收到，但当前已有维护任务在运行，本次跳过。"
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    applied = [
        str(step.get("name") or "")
        for step in steps
        if isinstance(step, dict) and step.get("applied")
    ]
    reports = report.get("reports") if isinstance(report.get("reports"), list) else []
    lines = ["电脑维护已完成。" if report.get("ok") else "电脑维护已运行，但检测到需要关注的问题。"]
    if applied:
        lines.append("已执行：" + "、".join(applied[:5]))
    else:
        lines.append("没有执行需要高风险授权的动作。")
    if reports:
        lines.append(f"已提交/生成报告请求：{len(reports)} 个")
    record = str(report.get("record_path") or "")
    if record:
        lines.append("记录：" + record)
    lines.append("边界：不杀主 Codex、不关闭 Defender、不重发邮件、不直接修改桥接队列。")
    return "\n".join(lines)


def compact_repair_reply_text(mode: str, report: dict[str, Any]) -> str:
    mode = mode or "safe"
    if report.get("unsupported_mode"):
        return (
            f"repair {mode} 尚未接入专项执行。\n"
            "当前已投入使用的命令是：repair、repair status、repair deep。\n"
            "未执行任何修复。"
        )
    if report.get("specialized_mode"):
        summary = str(report.get("summary") or "").strip() or f"repair {mode} 专项检查已完成。"
        lines = [summary]
        actions_taken = report.get("actions_taken") if isinstance(report.get("actions_taken"), list) else []
        actions_blocked = report.get("actions_blocked") if isinstance(report.get("actions_blocked"), list) else []
        evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
        if actions_taken:
            lines.append("已执行：" + "、".join(str(item) for item in actions_taken[:5]))
        else:
            lines.append("未执行会发送消息、重投递、杀进程或改路由的动作。")
        if actions_blocked:
            lines.append("受控阻止：" + "、".join(str(item) for item in actions_blocked[:5]))
        if evidence.get("issue_codes"):
            lines.append("相关问题：" + "、".join(str(item) for item in evidence.get("issue_codes", [])[:5]))
        if evidence.get("active_task_ids"):
            lines.append("active：" + "、".join(str(item) for item in evidence.get("active_task_ids", [])[:5]))
        if evidence.get("reply_task_ids"):
            lines.append("reply backlog：" + "、".join(str(item) for item in evidence.get("reply_task_ids", [])[:5]))
        if report.get("next_step"):
            lines.append("下一步：" + str(report.get("next_step")))
        return "\n".join(lines)
    diagnosis = report.get("diagnosis") if isinstance(report.get("diagnosis"), dict) else {}
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    codes = [str(item.get("code") or "") for item in issues if isinstance(item, dict) and item.get("code")]
    repair = report.get("repair") if isinstance(report.get("repair"), dict) else {}
    actions = repair.get("actions") if isinstance(repair.get("actions"), list) else []
    applied = [
        str(item.get("code") or "")
        for item in actions
        if isinstance(item, dict)
        and isinstance(item.get("result"), dict)
        and item["result"].get("applied")
    ]
    planned = [
        str(item.get("code") or "")
        for item in actions
        if isinstance(item, dict) and item.get("code")
    ]
    lines = [f"repair {mode} 已完成。"]
    if applied:
        lines.append("已执行：" + "、".join(applied[:5]))
    elif mode == "status":
        lines.append("只读检查完成，未执行修复。")
    elif mode == "deep":
        lines.append("已生成深度修复计划，未执行高风险动作。")
    else:
        lines.append("没有需要立即执行的低风险修复，或当前只需观察。")
    if codes:
        lines.append("当前问题：" + "、".join(codes[:5]))
    if planned and not applied:
        lines.append("候选动作：" + "、".join(planned[:5]))
    lines.append("未发送 reply backlog 微信消息；这仍需要显式发送权限。")
    return "\n".join(lines)


def issue_codes_from_diagnosis(diagnosis: dict[str, Any]) -> list[str]:
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    return [str(item.get("code") or "") for item in issues if isinstance(item, dict) and item.get("code")]
