#!/usr/bin/env python3
"""Mobile delegation prompt and marker contract for the OpenClaw bridge.

Owns: mobile ack/result marker generation, marker stripping, and the delegated
Codex prompt body used by mobile final-reply tasks.
Non-goals: queue mutation, task routing, result recovery, permission decisions,
or Weixin delivery.
State behavior: pure string/dict assembly; no reads or writes.
Normal callers: mobile_openclaw_cli dispatch paths and protocol regression
checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

import permission_policy
from attachment_resources import describe_attachment, task_attachments


def make_mobile_batch_id(tasks: list[dict[str, Any]]) -> str:
    task_ids = [str(task.get("id") or "") for task in tasks]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    digest = hashlib.sha256(("|".join(task_ids) + "|" + now).encode("utf-8")).hexdigest()[:12]
    return f"mobile-openclaw-{now}-{digest}"


def mobile_result_marker(task_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "", str(task_id or ""))
    return f"[[mobile_task_id:{value}]]" if value else ""


def mobile_protocol_code(mobile_batch_id: str, task_id: str, purpose: str) -> str:
    seed = f"{mobile_batch_id}|{task_id}|{purpose}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def mobile_protocol(task_id: str, mobile_batch_id: str) -> dict[str, str]:
    value = re.sub(r"[^A-Za-z0-9_-]+", "", str(task_id or ""))
    ack_code = mobile_protocol_code(mobile_batch_id, value, "ack")
    result_code = mobile_protocol_code(mobile_batch_id, value, "result")
    return {
        "task_id": value,
        "ack_code": ack_code,
        "result_code": result_code,
        "ack_marker": f"[[mobile_ack:{value}:{ack_code}]]",
        "result_begin_marker": f"[[mobile_result_begin:{value}:{result_code}]]",
        "result_end_marker": f"[[mobile_result_end:{value}:{result_code}]]",
    }


def mobile_protocols(tasks: list[dict[str, Any]], mobile_batch_id: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id:
            result[task_id] = mobile_protocol(task_id, mobile_batch_id)
    return result


def mobile_result_codes_arg(protocols: dict[str, dict[str, str]]) -> str:
    return ",".join(
        f"{task_id}={item.get('result_code')}"
        for task_id, item in protocols.items()
        if task_id and item.get("result_code")
    )


def mobile_ack_codes_arg(protocols: dict[str, dict[str, str]]) -> str:
    return ",".join(
        f"{task_id}={item.get('ack_code')}"
        for task_id, item in protocols.items()
        if task_id and item.get("ack_code")
    )


def strip_mobile_result_markers(text: str) -> str:
    cleaned = re.sub(r"\[\[mobile_task_id:[A-Za-z0-9_-]+\]\]", "", str(text or ""))
    cleaned = re.sub(r"\[\[mobile_ack:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+\]\]", "", cleaned)
    cleaned = re.sub(r"\[\[mobile_result_begin:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+\]\]", "", cleaned)
    cleaned = re.sub(r"\[\[mobile_result_end:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+\]\]", "", cleaned)
    return cleaned.strip()


def build_task_prompt(
    tasks: list[dict[str, Any]],
    continuation: dict[str, Any] | None = None,
    mobile_batch_id: str = "",
    bridge_thread_id: str = "",
    result_owner_task_ids: list[str] | None = None,
    config: dict[str, Any] | None = None,
    *,
    task_can_join_supplement: Callable[[dict[str, Any]], bool],
    task_capability_passphrase_verified: Callable[[dict[str, Any], list[str], str], bool],
    permission_account_map: Callable[[dict[str, Any]], dict[str, str]],
) -> str:
    lines = [
        "<codex_delegation>",
        "  <source>mobile-openclaw-bridge</source>",
        "  <input>",
        "prompt_schema=mobile-openclaw-final-reply/v2",
        f"Mobile bridge received {len(tasks)} new message(s). Process them in order:",
    ]
    if mobile_batch_id:
        owner_id_set = {str(item) for item in (result_owner_task_ids or []) if str(item)}
        owner_tasks = [task for task in tasks if str(task.get("id") or "") in owner_id_set] if owner_id_set else tasks
        supplement_tasks = [task for task in tasks if str(task.get("id") or "") not in owner_id_set] if owner_id_set else []
        lines.append(f"mobile_batch_id={mobile_batch_id}")
        protocols = mobile_protocols(owner_tasks, mobile_batch_id)
        markers = [mobile_result_marker(str(task.get("id") or "")) for task in owner_tasks]
        markers = [marker for marker in markers if marker]
        if protocols:
            lines.append(
                "rules={"
                "\"ack_first\":true,"
                "\"ack_means_received_only\":true,"
                "\"ack_must_continue\":true,"
                "\"ack_only_is_protocol_failure\":true,"
                "\"result_after_work_only\":true,"
                "\"result_markers_only\":true,"
                "\"mobile_equals_desktop_quality\":true,"
                "\"permission_table\":\"_bridge\\\\mobile_openclaw_bridge\\\\permission_table.json\","
                "\"ordinary_user_deny_beyond_profile\":true,"
                "\"admin_superuser_audit_risk_controls\":true"
                "}"
            )
            for task_id, protocol in protocols.items():
                lines.append(
                    "protocol "
                    f"task_id={task_id} "
                    f"ack={protocol['ack_marker']} "
                    f"result_begin={protocol['result_begin_marker']} "
                    f"result_end={protocol['result_end_marker']}"
                )
                lines.append(
                    "output_contract "
                    f"task_id={task_id} "
                    "first=ack_then_continue "
                    "final=exact_begin+weixin_text+exact_end "
                    "forbid=outside|placeholder|explain|missing"
                )
        if markers:
            lines.append("legacy_required_result_markers=" + " ".join(markers))
            lines.append("required_result_markers=" + " ".join(markers))
        if supplement_tasks:
            lines.append(
                "Delivery group ownership: only the protocol task(s) above own the final Weixin reply. "
                "The other message(s) in this batch are supplements; incorporate them into one combined answer "
                "without separate final result markers or separate final replies."
            )
    if continuation and continuation.get("active"):
        lines.append(
            "Continuation active: treat these message(s) as a direct continuation of the recent Weixin "
            f"conversation. remaining_seconds={continuation.get('remaining_seconds')}"
        )
    if bridge_thread_id and any(task_can_join_supplement(task) for task in tasks):
        supplement_contract = {
            "required": True,
            "thread_id": bridge_thread_id,
            "tools": ["bridge.get_pending_batch", "bridge.ack_message"],
            "when": ["after_ack_before_work", "before_final_result"],
            "consume": "incorporate_all_applicable_items_then_ack_each",
            "fallback": "supplement-fallback-v1",
            "fallback_on": ["transport_closed", "tool_unbound", "unsupported_call", "unknown_tool", "dispatch_failure"],
            "fail_closed": True,
            "result_scope": "active_task_markers_only",
        }
        lines.append(
            "supplement_contract="
            + json.dumps(supplement_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    for index, task in enumerate(tasks, start=1):
        text = str(task.get("text") or "").strip()
        scope_decision = permission_policy.classify_ask_scope(text)
        include_temporary_capabilities = bool(
            not scope_decision.allowed
            and task_capability_passphrase_verified(
                task,
                list(scope_decision.required_actions),
                scope_decision.scope,
            )
        )
        permission_context = permission_policy.compact_codex_context(
            config or {},
            str(task.get("external_user") or ""),
            str(task.get("receiver_account_id") or ""),
            str(task.get("risk_level") or ""),
            permission_account_map(config or {}) if config else None,
            include_temporary_capabilities=include_temporary_capabilities,
        )
        lines.append(
            f"{index}. task_id={task.get('id')} risk={task.get('risk_level')} "
            f"from={task.get('external_user')} command={task.get('command')} text={text}"
        )
        lines.append("   permission=" + json.dumps(permission_context, ensure_ascii=False, sort_keys=True))
        attachments = task_attachments(task)
        if attachments:
            lines.append(f"   attachments={len(attachments)}")
            for att_index, attachment in enumerate(attachments, start=1):
                lines.extend(describe_attachment(attachment, att_index))
    lines.extend(["  </input>", "</codex_delegation>"])
    return "\n".join(lines)


def validate_final_reply_prompt_contract(prompt: str, expected_task_ids: list[str] | None = None) -> dict[str, Any]:
    """Validate that a base mobile final-reply prompt uses the live v2 contract.

    Owns: read-only validation of generated prompt text before Codex dispatch.
    Non-goals: repairing prompts, queue mutation, recovery decisions, or result parsing.
    State behavior: pure validation; no reads or writes.
    Normal callers: live dispatch gates and protocol regression checks.
    """

    text = str(prompt or "")
    lines = [line.strip() for line in text.splitlines()]
    expected_ids = [str(item) for item in (expected_task_ids or []) if str(item)]
    issues: list[dict[str, str]] = []

    def add_issue(code: str, detail: str = "") -> None:
        issues.append({"code": code, "detail": detail})

    if "prompt_schema=mobile-openclaw-final-reply/v2" not in lines:
        add_issue("missing_schema_v2")

    rules_line = next((line for line in lines if line.startswith("rules=")), "")
    rules: dict[str, Any] = {}
    if not rules_line:
        add_issue("missing_rules")
    else:
        try:
            parsed_rules = json.loads(rules_line.split("=", 1)[1])
            if isinstance(parsed_rules, dict):
                rules = parsed_rules
            else:
                add_issue("rules_not_object")
        except Exception as exc:
            add_issue("rules_json_invalid", str(exc))

    required_true_rules = [
        "ack_first",
        "ack_means_received_only",
        "ack_must_continue",
        "ack_only_is_protocol_failure",
        "result_after_work_only",
        "result_markers_only",
        "mobile_equals_desktop_quality",
    ]
    for key in required_true_rules:
        if rules.get(key) is not True:
            add_issue("missing_or_false_rule", key)
    if not rules.get("permission_table"):
        add_issue("missing_permission_table")

    if "first=ack_only" in text:
        add_issue("legacy_ack_only_contract")
    output_lines = [line for line in lines if line.startswith("output_contract ")]
    if not output_lines:
        add_issue("missing_output_contract")
    for task_id in expected_ids:
        matches = [line for line in output_lines if f"task_id={task_id} " in f"{line} "]
        if not matches:
            add_issue("missing_task_output_contract", task_id)
            continue
        if not any("first=ack_then_continue" in line for line in matches):
            add_issue("task_not_ack_then_continue", task_id)
    if output_lines and not all("first=ack_then_continue" in line for line in output_lines):
        add_issue("output_contract_without_ack_then_continue")

    if expected_ids:
        for task_id in expected_ids:
            if f"[[mobile_ack:{task_id}:" not in text:
                add_issue("missing_task_ack_marker", task_id)
            if f"[[mobile_result_begin:{task_id}:" not in text:
                add_issue("missing_task_result_begin_marker", task_id)
            if f"[[mobile_result_end:{task_id}:" not in text:
                add_issue("missing_task_result_end_marker", task_id)
            if f"[[mobile_task_id:{task_id}]]" not in text:
                add_issue("missing_task_result_marker", task_id)

    return {
        "ok": not issues,
        "issues": issues,
        "expected_task_ids": expected_ids,
        "output_contract_count": len(output_lines),
        "rules_keys": sorted(rules.keys()),
    }
