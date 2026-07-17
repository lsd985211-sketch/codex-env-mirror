#!/usr/bin/env python3
"""High-signal regression corpus for task facts, owners, and fallback contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from codex_resource_delegation import build_delegation  # noqa: E402
from execution_route_pack import _policy_decisions  # noqa: E402
from mcp_route_policy import call_priority_pack  # noqa: E402
from task_route_contract import FACT_GATE_CONTRACTS, resolve_task_route_contract, structured_facts_from_envelope  # noqa: E402


POSITIVE_FACT_CASES: dict[str, tuple[str, ...]] = {
    "local_write": (
        "修改这个文件并验证结果", "Implement the approved fix", "创建一个新的配置文件", "Refactor this module now",
    ),
    "config_change": (
        "执行配置修改并重载", "Apply a configuration update", "修复模型配置", "Perform the provider switch",
    ),
    "external_network_read": (
        "联网搜索官方资料", "Search online for current documentation", "查询 GitHub项目 的最新说明", "Use official documentation for evidence",
    ),
    "external_write": (
        "发送邮件给主发送者", "Publish the website", "上传生成的报告", "Submit remote changes",
    ),
    "package_install": (
        "安装软件 aria2", "Install tool ripgrep", "安装依赖并验证版本", "Upgrade package pytest",
    ),
    "database_write": (
        "更新数据库中的任务状态", "Insert into database after validation", "清理数据库里的过期记录", "Vacuum database using its owner",
    ),
    "gui_or_browser_state": (
        "刷新页面并读取新状态", "Click the browser control", "检查桌面界面是否更新", "Use GUI automation for this action",
    ),
    "secret_or_permission_use": (
        "使用管理员权限运行检查", "Read the credential through its owner", "需要授权后继续", "Use the API token without printing it",
    ),
    "destructive_or_high_risk": (
        "彻底删除已确认的废弃目录", "Permanently disable the proven faulty service", "批量删除前先生成回滚点", "Apply the system network policy change",
    ),
    "reload_or_restart_required": (
        "重启 Codex 后验证", "Reload the app server", "重新启动服务并读回状态", "Relaunch the desktop application",
    ),
    "system_member_change": (
        "新增一个 MCP server 并纳入工作环境", "Retire the obsolete workflow module", "注册新的系统组件", "Integrate a new owner adapter into the architecture",
    ),
    "durable_closeout_required": (
        "修改文件并完成持久化收口", "Install tool and record the durable change", "更新数据库后执行 closeout", "Publish the report and preserve the receipt",
    ),
    "explicit_mobile_envelope": (
        "<codex_delegation> prompt_schema=mobile-openclaw-final-reply/v2 task", "<codex_delegation> mobile_ack required", "prompt_schema=mobile-openclaw-final-reply/v2", "<codex_delegation> result_begin result_end",
    ),
}


NEGATED_FACT_CASES: dict[str, str] = {
    "local_write": "只分析当前规则，不要修改文件",
    "config_change": "不要修改配置，只读取当前值",
    "external_network_read": "不要联网，只看本地证据",
    "external_write": "不要发送邮件，仅生成草稿文本",
    "resource_materialization": "不要下载，只列出候选链接",
    "package_install": "不要安装依赖，只查询版本信息",
    "database_write": "不要更新数据库，只运行只读 SELECT",
    "gui_or_browser_state": "不要刷新页面，只分析源码",
    "secret_or_permission_use": "不要读取令牌，只检查字段名称",
    "destructive_or_high_risk": "不要彻底删除，只生成处理计划",
    "reload_or_restart_required": "无需重启，只验证静态配置",
    "system_member_change": "只分析 MCP 成员现状，不做新增或退役",
    "durable_closeout_required": "只读分析，不修改、不安装、不发布",
    "explicit_mobile_envelope": "讨论移动桥的协议设计，不处理真实委托包",
}


NON_ACTIVATION_RESTART_CASES: tuple[str, ...] = (
    "规划 USB 摄像头重启并要求显式确认和回滚边界",
    "重启蓝牙适配器前先检查设备指纹",
    "Codex Desktop 模型列表重启后不显示，找到根因",
    "我重启过电脑，为什么会话没有恢复",
    "Analyze why the USB camera restart failed",
)


OWNER_CASES: tuple[tuple[str, list[str], str], ...] = (
    ("实施工作环境的系统治理", ["workflow_governance"], "workflow_governance"),
    ("重构 resource layer 的路由冲突", ["workflow_governance", "resource_acquisition"], "workflow_governance"),
    ("Implement workflow governance changes", ["workflow_governance"], "workflow_governance"),
    ("持久化修复 MCP routing 机制冲突", ["workflow_governance", "mcp_tools"], "workflow_governance"),
    ("批准执行工作环境架构优化", ["workflow_governance"], "workflow_governance"),
    ("统一系统治理入口", ["workflow_governance"], "workflow_governance"),
    ("Refactor the resource layer governance", ["workflow_governance"], "workflow_governance"),
    ("修复工作环境路由冲突", ["workflow_governance"], "workflow_governance"),
    ("<codex_delegation> prompt_schema=mobile-openclaw-final-reply/v2", ["bridge"], "mobile_openclaw_bridge"),
    ("<codex_delegation> mobile_ack result_begin", ["bridge"], "mobile_openclaw_bridge"),
    ("prompt_schema=mobile-openclaw-final-reply/v2 mobile task", ["bridge"], "mobile_openclaw_bridge"),
    ("<codex_delegation> 手机委托", ["bridge"], "mobile_openclaw_bridge"),
    ("检查邮件待处理回信状态", ["email", "structured_state"], "email_scheduler"),
    ("读取 email queue", ["email"], "email_scheduler"),
    ("生成邮件草稿", ["email"], "email_scheduler"),
    ("Inspect scheduled email state", ["email", "structured_state"], "email_scheduler"),
    ("查询邮件投递回执", ["email"], "email_scheduler"),
    ("回复邮件任务", ["email"], "email_scheduler"),
    ("Check inbox state", ["email"], "email_scheduler"),
    ("分析邮件附件处理", ["email"], "email_scheduler"),
)


MCP_CASES: tuple[tuple[str, str, str], ...] = (
    ("github", "search_repositories", "github_remote"),
    ("codegraph", "explore", "code_structure"),
    ("sqlite-scratch", "sqlite_query", "sqlite_state"),
    ("filesystem-admin", "read_text_file", "filesystem"),
    ("chrome-devtools", "take_snapshot", "gui_browser"),
    ("mobile-openclaw-bridge", "get_pending_batch", "mobile_bridge"),
)


def _record(failures: list[dict[str, Any]], category: str, case: Any, ok: bool, detail: Any = None) -> None:
    if not ok:
        failures.append({"category": category, "case": case, "detail": detail})


def validate() -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    case_count = 0
    safety_false_negatives = 0
    gate_false_negatives = 0

    for fact, messages in POSITIVE_FACT_CASES.items():
        for message in messages:
            case_count += 1
            contract = resolve_task_route_contract(message, [])
            detected = bool((contract.task_facts or {}).get(fact))
            if not detected:
                safety_false_negatives += 1
            _record(failures, "positive_fact", {"fact": fact, "message": message}, detected, contract.to_dict())
            gate_present = any(item.get("fact") == fact for item in contract.required_gates or [])
            if fact in FACT_GATE_CONTRACTS and not gate_present:
                gate_false_negatives += 1
            _record(failures, "required_gate", {"fact": fact, "message": message}, gate_present, contract.required_gates)

    for fact, message in NEGATED_FACT_CASES.items():
        case_count += 1
        contract = resolve_task_route_contract(message, [])
        _record(
            failures,
            "negated_fact",
            {"fact": fact, "message": message},
            not bool((contract.task_facts or {}).get(fact)),
            contract.matched_signals,
        )

    for message in NON_ACTIVATION_RESTART_CASES:
        case_count += 1
        contract = resolve_task_route_contract(message, ["hardware"] if "USB" in message or "蓝牙" in message else [])
        _record(
            failures,
            "non_activation_restart_mention",
            message,
            not bool((contract.task_facts or {}).get("reload_or_restart_required")),
            contract.matched_signals,
        )

    explicit_case_count = 0
    explicit_pass_count = 0
    for fact, messages in POSITIVE_FACT_CASES.items():
        positive_message = messages[0]
        for message, explicit_value in ((positive_message, False), ("neutral task description", True)):
            case_count += 1
            explicit_case_count += 1
            contract = resolve_task_route_contract(message, [], {fact: explicit_value})
            actual = bool((contract.task_facts or {}).get(fact))
            source = (contract.matched_signals or {}).get(fact, {}).get("source")
            ok = actual is explicit_value and source == "explicit_structured_field"
            explicit_pass_count += int(ok)
            _record(failures, "structured_precedence", {"fact": fact, "message": message, "value": explicit_value}, ok, contract.to_dict())

    owner_pass_count = 0
    for message, domains, expected_owner in OWNER_CASES:
        case_count += 1
        contract = resolve_task_route_contract(message, domains)
        ok = contract.business_owner == expected_owner
        owner_pass_count += int(ok)
        _record(failures, "owner_route", {"message": message, "domains": domains, "expected": expected_owner}, ok, contract.to_dict())

    for index in range(1, 11):
        case_count += 1
        domain = "openpolicyagent.org" if index % 2 else "kubernetes.io"
        payload = build_delegation(
            target=f"documentation batch {index}",
            task="collect official documentation",
            intent="documentation_lookup",
            quantity=index + 1,
            minimum_quantity=index,
            maximum_quantity=index + 2,
            uniqueness_required=True,
            uniqueness_dimensions=["url", "title"],
            source_domains=[domain],
            freshness_mode="recent",
            max_age_days=30,
            target_dir=f"C:/resource-test/{index}",
            destination_policy="explicit_target",
            need_materialization=True,
            allow_filesystem_write=True,
        )
        resource = payload.get("request", {}).get("metadata", {}).get("task_envelope", {}).get("resource", {})
        ok = (
            resource.get("quantity", {}).get("requested") == index + 1
            and resource.get("quantity", {}).get("minimum") == index
            and resource.get("quantity", {}).get("maximum") == index + 2
            and resource.get("uniqueness", {}).get("required") is True
            and resource.get("source_policy", {}).get("domains") == [domain]
            and resource.get("freshness", {}).get("max_age_days") == 30
            and resource.get("materialization", {}).get("target_dir") == f"C:/resource-test/{index}"
        )
        _record(failures, "resource_structured_fields", {"index": index, "domain": domain}, ok, resource)

    structured_fact_cases = (
        ({"action": "discover_and_download", "required": True, "allow_write": True}, {"resource_materialization": True, "local_write": True, "durable_closeout_required": True}),
        ({"action": "discover_and_download", "required": True, "allow_write": False}, {"resource_materialization": True, "local_write": False, "durable_closeout_required": False}),
        ({"action": "discover", "required": False, "allow_write": True}, {"resource_materialization": False, "local_write": False, "durable_closeout_required": False}),
        ({"action": "install", "required": False, "allow_write": False}, {"package_install": True, "config_change": True, "durable_closeout_required": True}),
    )
    for case, expected in structured_fact_cases:
        case_count += 1
        facts = structured_facts_from_envelope(
            {
                "schema": "structured_task_envelope.v1",
                "domain": "resource",
                "action": case["action"],
                "resource": {"materialization": {"required": case["required"], "target_dir": "C:/resource-test"}},
                "safety": {"allow_network": True, "allow_filesystem_write": case["allow_write"]},
            }
        )
        ok = all(facts.get(key) is value for key, value in expected.items())
        _record(failures, "structured_resource_task_facts", {"case": case, "expected": expected}, ok, facts)

    canonical = ["precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"]
    for profile, tool, capability in MCP_CASES:
        case_count += 1
        pack = call_priority_pack(profile, tool, capability)
        steps = [str(item.get("id") or "") for item in pack.get("steps", []) if isinstance(item, dict)]
        indexes = [canonical.index(item) for item in steps if item in canonical]
        forward = indexes == sorted(indexes) and bool(steps) and steps[0] == pack.get("required_first_step")
        linked = all(
            step.get("on_failure_next_step") == (steps[position + 1] if position + 1 < len(steps) else "terminal_failure")
            for position, step in enumerate(pack.get("steps", []))
            if isinstance(step, dict)
        )
        ok = forward and linked and pack.get("continuation_policy", {}).get("backward_jump_allowed") is False
        _record(failures, "mcp_forward_fallback", {"profile": profile, "tool": tool, "capability": capability}, ok, pack)

    policy_cases = (
        ({"external_network_read": True}, {"external.online_access", "resource.structured_contract"}),
        ({"system_member_change": True}, {"system.membership", "workflow.closeout"}),
        ({"resource_materialization": True}, {"resource.structured_contract", "resource.source_and_satisfaction"}),
        ({"explicit_mobile_envelope": True}, {"mobile.permission_contract"}),
        ({"destructive_or_high_risk": True}, {"platform.precedence"}),
    )
    for facts, expected_rules in policy_cases:
        case_count += 1
        decisions = _policy_decisions(facts, {})
        actual_rules = {str(item.get("rule_id") or "") for item in decisions}
        ok = expected_rules.issubset(actual_rules) and not any(rule.startswith("task_fact.") for rule in actual_rules)
        _record(failures, "runtime_rule_activation", {"facts": facts, "expected_rules": sorted(expected_rules)}, ok, decisions)

    owner_accuracy = owner_pass_count / max(1, len(OWNER_CASES))
    explicit_accuracy = explicit_pass_count / max(1, explicit_case_count)
    acceptance = {
        "minimum_case_count": case_count >= 80,
        "mandatory_gate_false_negatives_zero": safety_false_negatives == 0 and gate_false_negatives == 0,
        "structured_precedence_100_percent": explicit_accuracy == 1.0,
        "owner_route_accuracy_at_least_95_percent": owner_accuracy >= 0.95,
        "all_cases_passed": not failures,
    }
    return {
        "schema": "workflow_rule_routing_tests.v1",
        "ok": all(acceptance.values()),
        "case_count": case_count,
        "fact_positive_count": sum(len(values) for values in POSITIVE_FACT_CASES.values()),
        "negation_case_count": len(NEGATED_FACT_CASES),
        "structured_precedence_case_count": explicit_case_count,
        "owner_route_case_count": len(OWNER_CASES),
        "resource_structured_case_count": 10,
        "resource_task_fact_case_count": len(structured_fact_cases),
        "mcp_fallback_case_count": len(MCP_CASES),
        "runtime_rule_activation_case_count": len(policy_cases),
        "safety_false_negatives": safety_false_negatives,
        "gate_false_negatives": gate_false_negatives,
        "structured_precedence_accuracy": round(explicit_accuracy, 3),
        "owner_route_accuracy": round(owner_accuracy, 3),
        "acceptance": acceptance,
        "failures": failures[:20],
        "failure_count": len(failures),
    }


def main() -> int:
    payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
