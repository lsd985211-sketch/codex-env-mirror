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
from execution_route_pack import _policy_decisions, build_execution_route_pack  # noqa: E402
from mcp_route_policy import call_priority_pack  # noqa: E402
from task_route_contract import FACT_GATE_CONTRACTS, resolve_task_route_contract, structured_facts_from_envelope  # noqa: E402
from workflow_orchestrator import build_plan as build_workflow_plan, cli_projection  # noqa: E402


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

NON_PERMISSION_GOVERNANCE_CASES: tuple[str, ...] = (
    "彻底优化权限系统与授权意图识别，不读取令牌或凭据",
    "分析 permission system 的 authorization flow，不使用 secret 或 credential",
)


OWNER_CASES: tuple[tuple[str, list[str], str], ...] = (
    ("实施工作环境的系统治理", ["workflow_governance"], "workflow_governance"),
    ("重构 resource layer 的路由冲突", ["workflow_governance", "resource_acquisition"], "resource_broker"),
    ("Implement workflow governance changes", ["workflow_governance"], "workflow_governance"),
    ("持久化修复 MCP routing 机制冲突", ["workflow_governance", "mcp_tools"], "workflow_governance"),
    ("批准执行工作环境架构优化", ["workflow_governance"], "workflow_governance"),
    ("统一系统治理入口", ["workflow_governance"], "workflow_governance"),
    ("Refactor the resource layer governance", ["workflow_governance"], "workflow_governance"),
    ("修复工作环境路由冲突", ["workflow_governance"], "workflow_governance"),
    ("注意对现有资产的优化或者利用，做出一个可以投入施工的计划", ["workflow_governance"], "workflow_governance"),
    ("实施 Codex 生产级 AI 操作系统施工计划", ["workflow_governance"], "workflow_governance"),
    ("现有的维护面地图太大了，不符合按需加载的原则，需要扩散解决同类问题", ["workflow_governance"], "workflow_governance"),
    ("将大型治理资产改为按需读取，但不可牺牲功能性", ["workflow_governance"], "workflow_governance"),
    ("自主审核并消费审批包，清理审阅队列并持续改进", ["workflow_governance"], "workflow_governance"),
    ("审查当前工作树中的代码变更并找出回归风险", ["code_review_refactor"], "code_review_refactor"),
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
    ("逐步淘汰原生工作区，将 WSL 作为主环境", ["wsl_workspace"], "wsl_workspace"),
    ("把日常工作切到 Work Git 和 WSL workspace", ["wsl_workspace"], "wsl_workspace"),
    ("检查 Windows bare Git 到 WSL worktree 的发布边界", ["wsl_workspace"], "wsl_workspace"),
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


def _authorization_route(*, risk: str, costs: dict[str, float], matched_forbids: list[str] | None = None) -> dict[str, Any]:
    contract = resolve_task_route_contract("修改一个声明文件并完成验证和收口", ["workflow_governance"]).to_dict()
    contract["task_facts"] = {
        **(contract.get("task_facts") or {}),
        "local_write": True,
        "durable_closeout_required": True,
    }
    contract["authorization_assessment"] = {
        "subject": {"id": "current-task", "class": "codex_task"},
        "action": {"name": "local_source_edit", "owner": "work_git_change_owner"},
        "resource": {"kind": "git_worktree", "id": "declared-change-set"},
        "environment": {"class": "local_isolated", "reversible": True},
        "risk": {"level": risk, "facts": ["reversible", "verified_restore_point"]},
        "costs": costs,
        "matched_forbids": matched_forbids or [],
    }
    plan = {
        "ok": True,
        "message": "修改一个声明文件并完成验证和收口",
        "domains": [{"key": "workflow_governance", "drives_execution": True, "route_confidence": 1.0, "match_quality": "explicit_contract"}],
        "tools": {},
        "machine_phases": [],
        "structured_route": {"task_contract": contract},
    }
    return build_execution_route_pack(
        plan,
        environment_context={
            "relevant_systems": [{"id": "workflow"}],
            "tool_entrypoints": ["work_git_change_owner"],
        },
    )


def validate() -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    case_count = 0
    safety_false_negatives = 0
    gate_false_negatives = 0

    budget_cases = (
        ("分析当前工作流高频重复验证成本，不修改任何文件", "unknown", "quick"),
        ("修改 workflow_orchestrator.py 并持久化提交", "unknown", "full"),
        ("彻底删除已确认的废弃目录", "high", "deep"),
    )
    for message, risk, expected_tier in budget_cases:
        case_count += 1
        plan = build_workflow_plan(message, risk=risk, detail="full")
        budget = plan.get("task_execution_budget") or {}
        ok = (
            budget.get("validation_tier") == expected_tier
            and plan.get("profile", {}).get("validation_tier") == expected_tier
            and plan.get("execution_plan", {}).get("validation_tier") == expected_tier
            and budget.get("writes_business_state") is False
        )
        _record(failures, "task_execution_budget", {"message": message, "risk": risk}, ok, budget)

    simple_plan = build_workflow_plan("今天日期是什么", risk="unknown", detail="full")
    simple_budget = simple_plan.get("task_execution_budget") or {}
    simple_skips = set((simple_budget.get("phase_selection") or {}).get("skip_phase_ids") or [])
    simple_ok = {
        "phase_2_recall", "phase_3_skill_selection", "phase_5_tool_route", "phase_7_execution",
    }.issubset(simple_skips)
    _record(failures, "task_execution_budget_simple_phase_dedup", {"message": "今天日期是什么"}, simple_ok, simple_budget)

    write_plan = build_workflow_plan("修改 workflow_orchestrator.py 并持久化提交", risk="unknown", detail="full")
    write_budget = write_plan.get("task_execution_budget") or {}
    write_ok = not bool((write_budget.get("phase_selection") or {}).get("skip_phase_ids"))
    _record(failures, "task_execution_budget_write_keeps_soft_phases", {"message": "修改 workflow_orchestrator.py 并持久化提交"}, write_ok, write_budget)

    feedback_plan = build_workflow_plan(
        "分析当前工作流高频重复验证成本，不修改任何文件",
        risk="unknown",
        detail="full",
        feedback_projection={"invalid": "stale_or_incomplete"},
    )
    feedback_budget = feedback_plan.get("task_execution_budget") or {}
    feedback_ok = (
        feedback_plan.get("ok") is True
        and feedback_budget.get("adaptive_feedback", {}).get("status") == "ignored"
        and feedback_budget.get("adaptive_feedback", {}).get("reason") == "experiment_schema_invalid"
        and feedback_budget.get("writes_business_state") is False
    )
    _record(failures, "task_execution_budget_feedback_fails_closed", {}, feedback_ok, feedback_budget)

    reuse_projection = cli_projection(
        {
            "ok": True,
            "checks": [],
            "validation_receipt_reuse": True,
            "validation_receipt_reuse_status": "receipt_reused_passed",
            "validation_receipt_ref": "receipt:validation/current",
        },
        "validate",
    )
    reuse_ok = (
        reuse_projection.get("validation_receipt_reuse") is True
        and reuse_projection.get("validation_receipt_reuse_status") == "receipt_reused_passed"
        and reuse_projection.get("validation_receipt_ref") == "receipt:validation/current"
    )
    _record(failures, "validation_receipt_reuse_cli_projection", {}, reuse_ok, reuse_projection)

    low_costs = {
        "money_single_cny": 0, "money_daily_cny": 0, "elapsed_minutes": 2,
        "cpu_core_hours": 0.1, "gpu_minutes": 0, "network_gib": 0,
        "read_gib": 0.01, "read_records": 100, "write_gib": 0.01,
        "write_records": 10, "files_touched": 1, "external_calls": 0,
    }
    authorization_cases = (
        ("R2_low_cost", _authorization_route(risk="R2", costs=low_costs), "allow_without_challenge", False),
        ("R3_low_cost", _authorization_route(risk="R3", costs=low_costs), "challenge_required", True),
        ("R2_high_cost", _authorization_route(risk="R2", costs={**low_costs, "elapsed_minutes": 30}), "challenge_required", True),
        ("unknown", _authorization_route(risk="unknown", costs={**low_costs, "elapsed_minutes": None}), "bounded_preflight", False),
        ("R4", _authorization_route(risk="R4", costs=low_costs), "deny_non_overrideable", False),
    )
    for name, route_pack, expected_decision, expected_challenge in authorization_cases:
        case_count += 1
        authorization = route_pack.get("authorization") or {}
        gates = (route_pack.get("route_decision") or {}).get("required_gates") or []
        gate_facts = {str(item.get("fact") or "") for item in gates if isinstance(item, dict)}
        ok = (
            authorization.get("selected_enforcement") == expected_decision
            and bool(authorization.get("challenge_required")) is expected_challenge
            and authorization.get("status") != "scope_required"
            and "local_write" in gate_facts
            and "durable_closeout_required" in gate_facts
            and "write_or_external_action_without_explicit_approval" not in (route_pack.get("route_decision") or {}).get("stop_if", [])
            and "side_effect_without_selected_pdp_decision_or_required_owner_controls" in (route_pack.get("route_decision") or {}).get("stop_if", [])
        )
        _record(failures, "route_to_risk_cost_pdp", name, ok, {"authorization": authorization, "gate_facts": sorted(gate_facts)})
        authority = route_pack.get("decision_authority") or {}
        expected_blocking = expected_decision in {"challenge_required", "deny_non_overrideable"}
        authority_ok = bool(authority.get("blocking")) is expected_blocking
        if name == "R2_low_cost":
            authority_ok = authority_ok and bool(authority.get("recommendations")) and not authority.get("hard_gates")
        _record(failures, "route_authority_matches_pdp", name, authority_ok, authority)

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

    for message in NON_PERMISSION_GOVERNANCE_CASES:
        case_count += 1
        contract = resolve_task_route_contract(message, ["workflow_governance"])
        _record(
            failures,
            "permission_governance_is_not_secret_use",
            {"message": message},
            not bool((contract.task_facts or {}).get("secret_or_permission_use")),
            contract.to_dict(),
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

    specialized_route_cases = (
        ("通用 BGE-M3 路由优化，保持教材业务 owner 不变", "semantic_retrieval", "semantic_capability_owner"),
        ("识别扫描 PDF 并使用本机 OCR，不安装任何工具", "document_processing", "pdf_owner"),
        ("检索医学教材概念并给出引用", "textbook_knowledge", "skill:medical-textbook-study"),
        # The subject remains semantic retrieval, while the explicit online
        # research fact transfers resource acquisition to its existing owner.
        ("联网研究 BGE-M3 官方资料并给出引用", "semantic_retrieval", "resource_broker"),
        ("修复 Python 模块中的函数调用问题并验证影响范围", "general", "codex_code_review_judgment"),
    )
    for message, expected_domain, expected_owner in specialized_route_cases:
        case_count += 1
        plan = build_workflow_plan(message, detail="full")
        route = (plan.get("execution_route_pack") or {}).get("route_decision") or {}
        guidance = (plan.get("execution_route_pack") or {}).get("asset_guidance") or {}
        primary_owner = (guidance.get("primary_owner") or {}).get("name")
        ok = route.get("primary_domain") == expected_domain and primary_owner == expected_owner
        _record(
            failures,
            "specialized_environment_route",
            {"message": message, "domain": expected_domain, "owner": expected_owner},
            ok,
            {"route": route, "primary_owner": primary_owner},
        )

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
    value_projection = {
        "schema": "permission_governance.task_value_projection.v1",
        "representative_task": "R2_low_cost_reversible_local_change",
        "approval_round_trips": {"baseline": 1, "candidate": 0, "delta": -1},
        "random_authorization_tokens": {"baseline": 1, "candidate": 0, "delta": -1},
        "clarification_round_trips": {"baseline": 0, "candidate": 0, "delta": 0},
        "user_wait_ms": {"baseline": "requires_rollout_observation", "candidate": "requires_rollout_observation"},
        "active_execution_ms": {"baseline": "requires_rollout_observation", "candidate": "requires_rollout_observation"},
        "rework": {"baseline": "requires_rollout_observation", "candidate": "requires_rollout_observation"},
        "governance_tax": {"baseline": "requires_rollout_observation", "candidate": "requires_rollout_observation"},
        "first_pass": "technical_route_first_pass_only",
        "accepted_and_consumed": "unknown_until_independent_user_task_consumes_result",
        "hard_gate_equivalence": {
            "R3_low_cost_challenge": True,
            "R2_high_cost_challenge": True,
            "R4_denied": True,
            "backup_owner_validation_closeout_retained": True,
        },
        "claim_boundary": "tests_prove_structural_round_trip_removal_not_live_user_value_acceptance",
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
        "task_level_value_projection": value_projection,
        "failures": failures[:20],
        "failure_count": len(failures),
    }


def main() -> int:
    payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
