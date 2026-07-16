#!/usr/bin/env python3
"""Validation helpers for workflow orchestration.

This module owns read-only validation scenarios and contract checks for
workflow plans. It does not classify messages, build plans by itself, execute
commands, mutate state, or change routing policy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tool_utilization_audit import validate_with_build_plan as validate_tool_utilization_with_build_plan
from workflow_owner_facade import validate as validate_owner_facade
from workflow_closeout_package import build_closeout_package


BuildPlan = Callable[..., dict[str, Any]]

VALIDATION_SAMPLES = [
    "MCP transport closed and tool current turn unstable",
    "邮箱收件箱待处理回信任务",
    "backup2 任务状态卡在入队，检查队列和回执",
    "又出现了只ack不思考的问题，找到根本原因",
    "codex_delegation mobile_ack 后没有 mobile_result_begin",
    "记忆 note 吸收和 PMB 整理",
    "检查当前全局下系统存在的冗余和互相矛盾或拮抗的机制问题",
    "GitHub 仓库 README 优化",
    "联网搜索相关知识并优先使用官方文档 MCP",
    "资源层获取资源策略继续完善",
    "完善工具路由收敛、资源层自然主入口、记录索引优先、工作流分类触发",
    "目前触发工作流的时机还没有覆盖完全，而且工作流分流机制还是不够成熟，联网搜索相关成熟知识，完善这些",
    "修改 AGENTS.md 并做好备份",
    "代码可维护性治理和重构目标选择",
    "代码模块系统深度整合到工作流",
]

REQUIRED_PHASE_IDS = [
    "phase_1_preflight",
    "phase_2_recall",
    "phase_3_skill_selection",
    "phase_4_template_render",
    "phase_5_tool_route",
    "phase_6_module_context",
    "phase_7_execution",
    "phase_8_validation",
    "phase_9_closeout",
]

REQUIRED_PHASE_FIELDS = {
    "id",
    "owner",
    "enabled",
    "skip_reason",
    "trigger_reason",
    "checkpoint_triggers",
    "checkpoint_command",
    "depends_on",
    "commands",
    "action_contract",
    "validation_tier",
    "read_only",
    "approval_required",
    "approval_reason",
    "fallback",
    "validation",
    "stop_conditions",
    "evidence_to_record",
    "next_phase",
}

PROFILE_EXPECTATIONS = [
    ("维护 MCP 工具稳定性，检查当前故障", "diagnose_only", False),
    ("维护 MCP 工具稳定性并修复相关代码", "repair_or_code_change", True),
    ("联网搜索相关知识完善方案", "research", False),
    ("代码模块系统深度整合到工作流", "repair_or_code_change", True),
]


def run_cli_contract_case(args: list[str], *, expect_success: bool, expected_text: str = "") -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=20,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    ok = (proc.returncode == 0) if expect_success else (proc.returncode != 0)
    if expected_text:
        ok = ok and expected_text in combined
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "expect_success": expect_success,
        "expected_text_found": expected_text in combined if expected_text else True,
        "stderr_excerpt": (proc.stderr or "").strip()[:500],
    }


def machine_enum_cli_contract() -> dict[str, Any]:
    """Regression checks for machine enum fields rejecting prose values."""
    cases = {
        "closeout_valid": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--validation",
                "machine_enum=ok",
            ],
            expect_success=True,
        ),
        "closeout_rejects_prose": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "resource delegation now has job submit status progress wait",
            ],
            expect_success=False,
            expected_text="Put prose in --finalization-summary, --validation, or --notes.",
        ),
        "skill_usage_rejects_prose": run_cli_contract_case(
            [
                "_bridge/skill_orchestrator.py",
                "record-usage",
                "--task-kind",
                "validate",
                "--selected",
                "global-framework",
                "--used",
                "global-framework",
                "--outcome",
                "everything worked",
            ],
            expect_success=False,
            expected_text="Put prose in --notes.",
        ),
        "finalization_rejects_prose": run_cli_contract_case(
            [
                "_bridge/workflow_finalization.py",
                "--task-kind",
                "validate",
                "--outcome",
                "resource delegation now has job submit",
            ],
            expect_success=False,
            expected_text="Put prose in --summary.",
        ),
    }
    return {
        "ok": all(item.get("ok") for item in cases.values()),
        "cases": cases,
    }


def online_access_gate_contract() -> dict[str, Any]:
    """Regression checks for resource-layer-first direct web gating."""
    cases = {
        "gate_validate": run_cli_contract_case(
            ["_bridge/online_access_gate.py", "validate"],
            expect_success=True,
        ),
        "closeout_rejects_direct_web_without_resource_exception": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
            ],
            expect_success=True,
            expected_text="direct_web_without_resource_exception",
        ),
        "closeout_rejects_resource_deferred": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--resource-request-id",
                "res_test",
                "--resource-status",
                "deferred",
            ],
            expect_success=True,
            expected_text="resource_layer_needs_refinement",
        ),
        "closeout_rejects_failed_without_route_chain": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--resource-request-id",
                "res_test",
                "--resource-status",
                "failed",
            ],
            expect_success=True,
            expected_text="resource_layer_failure_requires_online_route_chain",
        ),
        "closeout_allows_route_chain_exhausted": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--resource-request-id",
                "res_test",
                "--resource-status",
                "failed",
                "--direct-web-fallback-reason",
                "predefined_online_route_exhausted",
                "--owner-mcp-fallback-reason",
                "native_owner_failed;hub_owner_failed;local_hub_not_applicable;owner_cli_not_applicable",
            ],
            expect_success=True,
            expected_text='"matched_reason": "predefined_online_route_exhausted"',
        ),
        "closeout_allows_explicit_user_direct_web": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--user-direct-web",
            ],
            expect_success=True,
            expected_text='"matched_reason": "explicit_user_direct_web"',
        ),
        "closeout_allows_platform_required_web": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--platform-web-required",
                "--resource-request-id",
                "batch_test",
                "--resource-status",
                "completed",
            ],
            expect_success=True,
            expected_text='"matched_reason": "higher_precedence_platform_web_required"',
        ),
        "closeout_rejects_platform_reason_without_flag": run_cli_contract_case(
            [
                "_bridge/codex_workflow_entry.py",
                "closeout",
                "--task-kind",
                "validate",
                "--outcome",
                "ok",
                "--web-search-used",
                "--direct-web-fallback-reason",
                "higher_precedence_platform_web_required",
            ],
            expect_success=True,
            expected_text="direct_web_without_resource_exception",
        ),
    }
    return {
        "ok": all(item.get("ok") for item in cases.values()),
        "cases": cases,
    }


def resource_broker_contract() -> dict[str, Any]:
    """Regression checks for resource broker log-shape boundaries."""

    cases = {
        "broker_validate": run_cli_contract_case(
            ["_bridge/resource_broker.py", "validate"],
            expect_success=True,
            expected_text="source_selection_not_loggable",
        ),
    }
    return {
        "ok": all(item.get("ok") for item in cases.values()),
        "cases": cases,
    }


def maintenance_upgrade_governance_contract() -> dict[str, Any]:
    """Regression checks for task-specific upgrade evidence planning."""
    cases = {
        "planner_validate": run_cli_contract_case(
            ["_bridge/maintenance_upgrade_governance.py", "validate"],
            expect_success=True,
        ),
        "planner_exposes_conditional_evidence": run_cli_contract_case(
            [
                "_bridge/maintenance_upgrade_governance.py",
                "plan",
                "--message",
                "优化模块系统并根据任务选择证据链",
                "--target-system",
                "workflow",
                "--target",
                "_bridge/workflow_orchestrator.py",
            ],
            expect_success=True,
            expected_text="conditional_evidence_chain",
        ),
        "planner_references_existing_policy_instead_of_tool_order": run_cli_contract_case(
            [
                "_bridge/maintenance_upgrade_governance.py",
                "plan",
                "--message",
                "检查邮件队列状态和回执",
            ],
            expect_success=True,
            expected_text="policy_ref",
        ),
    }
    return {
        "ok": all(item.get("ok") for item in cases.values()),
        "cases": cases,
    }


def phase_contract(plans: list[dict[str, Any]]) -> dict[str, Any]:
    detail: list[dict[str, Any]] = []
    ok = True
    for plan in plans:
        phases = plan.get("machine_phases", [])
        ids = [str(item.get("id") or "") for item in phases if isinstance(item, dict)]
        missing_ids = [item for item in REQUIRED_PHASE_IDS if item not in ids]
        missing_fields = [
            {"phase": item.get("id"), "missing": sorted(REQUIRED_PHASE_FIELDS - set(item))}
            for item in phases
            if isinstance(item, dict) and REQUIRED_PHASE_FIELDS - set(item)
        ]
        if missing_ids or missing_fields:
            ok = False
        detail.append({"missing_ids": missing_ids, "missing_fields": missing_fields})
    return {"ok": ok, "detail": detail}


def profile_and_placement(build_plan: BuildPlan) -> dict[str, Any]:
    profile_details: list[dict[str, Any]] = []
    placement_details: list[dict[str, Any]] = []
    profile_ok = True
    placement_ok = True
    for message, expected_profile, expected_module_enabled in PROFILE_EXPECTATIONS:
        plan = build_plan(message)
        phase6 = next(
            (phase for phase in plan.get("machine_phases", []) if phase.get("id") == "phase_6_module_context"),
            {},
        )
        actual_profile = plan.get("profile", {}).get("profile")
        actual_module_enabled = bool(phase6.get("enabled"))
        phase6_commands = [
            str(command.get("cmd") or "")
            for command in phase6.get("commands", [])
            if isinstance(command, dict)
        ]
        has_placement_command = any("code_maintainability.py placement-plan" in command for command in phase6_commands)
        has_upgrade_governance_command = any("maintenance_upgrade_governance.py plan" in command for command in phase6_commands)
        has_placement_output = "placement_decision" in [str(item) for item in phase6.get("outputs", [])]
        has_upgrade_governance_output = "conditional_evidence_chain" in [str(item) for item in phase6.get("outputs", [])]
        item = {
            "message": message,
            "expected_profile": expected_profile,
            "actual_profile": actual_profile,
            "expected_module_enabled": expected_module_enabled,
            "actual_module_enabled": actual_module_enabled,
            "ok": actual_profile == expected_profile and actual_module_enabled == expected_module_enabled,
        }
        if not item["ok"]:
            profile_ok = False
        profile_details.append(item)

        placement_item = {
            "message": message,
            "phase6_enabled": actual_module_enabled,
            "has_placement_command": has_placement_command,
            "has_upgrade_governance_command": has_upgrade_governance_command,
            "has_placement_output": has_placement_output,
            "has_upgrade_governance_output": has_upgrade_governance_output,
            "ok": not actual_module_enabled
            or (
                has_placement_command
                and has_placement_output
                and has_upgrade_governance_command
                and has_upgrade_governance_output
            ),
        }
        if not placement_item["ok"]:
            placement_ok = False
        placement_details.append(placement_item)
    return {
        "profile_ok": profile_ok,
        "profile_detail": profile_details,
        "placement_ok": placement_ok,
        "placement_detail": placement_details,
    }


def structured_state_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("backup2 任务状态卡在入队，检查队列和回执")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
    policy = plan.get("tools", {}).get("structured_state_policy", {})
    domain_detail = next((item for item in plan.get("domains", []) if item.get("key") == "structured_state"), {})
    routing_ok = (
        "structured_state" in domains
        and "sqlite_state" in lookup_terms
        and bool(policy.get("enabled"))
        and "owning business maintenance" in str(policy.get("repair_rule", ""))
    )
    semantics_ok = (
        domain_detail.get("match_quality") == "strong"
        and bool(domain_detail.get("drives_execution"))
        and float(domain_detail.get("route_confidence", 0.0)) >= 0.5
        and float(domain_detail.get("candidate_ratio", 0.0)) == 1.0
        and plan.get("profile", {}).get("profile") == "diagnose_only"
    )
    return {
        "routing_ok": routing_ok,
        "semantics_ok": semantics_ok,
        "domains": domains,
        "lookup_terms": lookup_terms,
        "policy": policy,
        "domain_detail": domain_detail,
        "profile": plan.get("profile", {}),
    }


def network_routing_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("分析 OpenAI 网络卡断和代理 DNS 问题")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
    policy = plan.get("tools", {}).get("network_policy", {})
    domain_detail = next((item for item in plan.get("domains", []) if item.get("key") == "network_routing"), {})
    routing_ok = (
        "network_routing" in domains
        and "network_routing" in lookup_terms
        and bool(policy.get("enabled"))
        and "network_doctor" in str(policy.get("query_rule", ""))
    )
    semantics_ok = (
        domain_detail.get("match_quality") == "strong"
        and bool(domain_detail.get("drives_execution"))
        and float(domain_detail.get("route_confidence", 0.0)) >= 0.5
        and plan.get("profile", {}).get("profile") == "diagnose_only"
    )
    return {
        "routing_ok": routing_ok,
        "semantics_ok": semantics_ok,
        "domains": domains,
        "lookup_terms": lookup_terms,
        "policy": policy,
        "domain_detail": domain_detail,
        "profile": plan.get("profile", {}),
    }


def external_docs_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("联网搜索 Node.js 代理环境变量相关知识，优先使用官方文档 MCP")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
    policy = plan.get("tools", {}).get("external_docs_policy", {})
    domain_detail = next((item for item in plan.get("domains", []) if item.get("key") == "external_docs_research"), {})
    routing_ok = (
        "external_docs_research" in domains
        and any(term in lookup_terms for term in ("context7", "microsoftdocs"))
        and bool(policy.get("enabled"))
        and "resource-layer request first" in str(policy.get("query_rule", ""))
        and "Deferred or insufficient results require a refined resource delegation" in str(policy.get("query_rule", ""))
        and "configured owner/Hub online route chain" in str(policy.get("query_rule", ""))
        and "resource_layer_receipt" in policy.get("closeout_required_evidence", [])
        and "owner_route_used_inside_resource_layer" in policy.get("closeout_required_evidence", [])
        and "online_access_gate_exception_if_generic_web_used" in policy.get("closeout_required_evidence", [])
        and "resource_layer_unavailable" in policy.get("generic_web_search_allowed_only_with", [])
        and "predefined_online_route_exhausted" in policy.get("generic_web_search_allowed_only_with", [])
        and "explicit_user_direct_web" in policy.get("generic_web_search_allowed_only_with", [])
    )
    semantics_ok = (
        domain_detail.get("match_quality") == "strong"
        and bool(domain_detail.get("drives_execution"))
        and float(domain_detail.get("route_confidence", 0.0)) >= 0.5
        and plan.get("profile", {}).get("profile") == "research"
    )
    return {
        "routing_ok": routing_ok,
        "semantics_ok": semantics_ok,
        "domains": domains,
        "lookup_terms": lookup_terms,
        "policy": policy,
        "domain_detail": domain_detail,
        "profile": plan.get("profile", {}),
    }


def resource_acquisition_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("资源层继续完善大文件断点续传 curl aria2 后端")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
    domain_detail = next((item for item in plan.get("domains", []) if item.get("key") == "resource_acquisition"), {})
    routing_ok = (
        "resource_acquisition" in domains
        and "resource_cli" in lookup_terms
        and "_bridge/resource_broker.py" in plan.get("tools", {}).get("maintenance", [])
    )
    semantics_ok = (
        domain_detail.get("match_quality") == "strong"
        and bool(domain_detail.get("drives_execution"))
        and float(domain_detail.get("route_confidence", 0.0)) >= 0.5
        and plan.get("profile", {}).get("profile") in {"repair_or_code_change", "maintenance_governance"}
    )
    return {
        "routing_ok": routing_ok,
        "semantics_ok": semantics_ok,
        "domains": domains,
        "lookup_terms": lookup_terms,
        "domain_detail": domain_detail,
        "profile": plan.get("profile", {}),
    }


def workflow_routing_convergence_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("完善工具路由收敛、资源层自然主入口、记录索引优先、工作流分类触发", detail="full")
    evidence_plan = build_plan(
        "目前触发工作流的时机还没有覆盖完全，而且工作流分流机制还是不够成熟，联网搜索相关成熟知识，完善这些",
        detail="full",
    )
    repair_plan = build_plan(
        "修复 OpenAI 官方文档请求被误路由到 Microsoft Docs/Context7，且缺少 OpenAI 产品文档执行适配器的问题",
        detail="full",
    )
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    evidence_domains = [str(item.get("key") or "") for item in evidence_plan.get("domains", [])]
    repair_domains = [str(item.get("key") or "") for item in repair_plan.get("domains", [])]
    domain_details = {
        str(item.get("key") or ""): item
        for item in plan.get("domains", [])
        if isinstance(item, dict)
    }
    route_decision = plan.get("execution_route_pack", {}).get("route_decision", {})
    evidence_decision = evidence_plan.get("execution_route_pack", {}).get("route_decision", {})
    evidence_gate = evidence_plan.get("execution_route_pack", {}).get("resource_gate", {})
    repair_decision = repair_plan.get("execution_route_pack", {}).get("route_decision", {})
    repair_profile = repair_plan.get("profile", {})
    structured_policy = plan.get("tools", {}).get("structured_state_policy", {})
    maintenance_policy = plan.get("tools", {}).get("maintenance_upgrade_policy", {})
    primary_domain = str(route_decision.get("primary_domain") or "")
    evidence_primary = str(evidence_decision.get("primary_domain") or "")
    resource_detail = domain_details.get("resource_acquisition", {})
    workflow_detail = domain_details.get("workflow_governance", {})
    records_detail = domain_details.get("records_resources", {})
    ok = (
        primary_domain == "workflow_governance"
        and workflow_detail.get("match_quality") == "strong"
        and "records_resources" in domains
        and bool(structured_policy.get("enabled"))
        and bool(maintenance_policy.get("enabled"))
        and resource_detail.get("drives_execution") is not True
        and evidence_primary == "workflow_governance"
        and "external_docs_research" in evidence_domains
        and evidence_decision.get("resource_delegation_required") is True
        and evidence_gate.get("source_discovery_owner") == "resource_layer"
        and repair_decision.get("primary_domain") == "code_maintainability"
        and repair_profile.get("profile") == "repair_or_code_change"
        and repair_profile.get("state_change_expected") is True
        and "external_docs_research" in repair_domains
    )
    return {
        "ok": ok,
        "domains": domains,
        "evidence_domains": evidence_domains,
        "primary_domain": primary_domain,
        "evidence_primary_domain": evidence_primary,
        "repair_domains": repair_domains,
        "repair_primary_domain": repair_decision.get("primary_domain"),
        "repair_profile": repair_profile,
        "workflow_detail": workflow_detail,
        "records_detail": records_detail,
        "resource_detail": resource_detail,
        "evidence_resource_required": evidence_decision.get("resource_delegation_required"),
        "evidence_source_owner": evidence_gate.get("source_discovery_owner"),
        "structured_policy_enabled": structured_policy.get("enabled"),
        "maintenance_policy_enabled": maintenance_policy.get("enabled"),
    }


def github_owner_mcp_research_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("联网搜索 GitHub 上适合本机的网络网关项目")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
    policy = plan.get("tools", {}).get("external_docs_policy", {})
    intent_route = plan.get("tools", {}).get("intent_resource_route", {})
    execution_gate = plan.get("tools", {}).get("execution_gate", {})
    route_pack = plan.get("execution_route_pack", {})
    resource_gate = route_pack.get("resource_gate", {}) if isinstance(route_pack.get("resource_gate"), dict) else {}
    owner_routes = intent_route.get("owner_routes", []) if isinstance(intent_route, dict) else []
    github_owner_route = next((item for item in owner_routes if item.get("resource") == "github"), {})
    validation_tiers = plan.get("validation_tiers", {})
    quick_validation = validation_tiers.get("quick", []) if isinstance(validation_tiers.get("quick"), list) else []
    policy_validation = str(policy.get("validation", ""))
    routing_ok = (
        "external_docs_research" in domains
        and "github" in domains
        and "github" in lookup_terms
        and "github" in policy.get("owner_mcp_candidates", [])
        and ("resource-layer receipt" in policy_validation or bool(resource_gate.get("enabled")))
        and "resource_layer_receipt" in policy.get("closeout_required_evidence", [])
        and resource_gate.get("resource_layer_source_discovery_required") is True
        and resource_gate.get("source_discovery_owner") == "resource_layer"
        and github_owner_route.get("owner_mcp") == "github"
        and "get_me" in github_owner_route.get("read_tools_first", [])
        and "pull_request_review_write" in github_owner_route.get("write_tools_blocked_by_default", [])
        and bool(execution_gate.get("generic_web_first_violation"))
    )
    return {
        "ok": routing_ok,
        "domains": domains,
        "lookup_terms": lookup_terms,
        "owner_mcp_candidates": policy.get("owner_mcp_candidates", []),
        "intent_owner_routes": owner_routes,
        "resource_gate": resource_gate,
        "execution_gate": execution_gate,
        "quick_validation": quick_validation,
    }


def memory_governance_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("目前记忆系统的利用方式是什么，继续优化重构记忆治理", detail="full")
    domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
    domain_detail = next((item for item in plan.get("domains", []) if item.get("key") == "memory"), {})
    route_pack = plan.get("execution_route_pack", {})
    route_decision = route_pack.get("route_decision", {}) if isinstance(route_pack.get("route_decision"), dict) else {}
    memory_route = plan.get("memory", {}).get("route", {}) if isinstance(plan.get("memory"), dict) else {}
    phase2 = next((phase for phase in plan.get("machine_phases", []) if phase.get("id") == "phase_2_recall"), {})
    ok = (
        "memory" in domains
        and domain_detail.get("match_quality") == "strong"
        and bool(domain_detail.get("drives_execution"))
        and route_decision.get("primary_domain") == "memory"
        and route_decision.get("required_next_action")
        in {"run_memory_governance_route_and_recall", "run_structured_state_read_only_query"}
        and memory_route.get("primary") in {"pmb_recall", "pmb_prepare", "quick_pass"}
        and phase2.get("owner") == "memory_governance"
        and phase2.get("enabled") is True
        and len(phase2.get("commands", []) or []) >= 1
    )
    return {
        "ok": ok,
        "domains": domains,
        "domain_detail": domain_detail,
        "route_decision": route_decision,
        "memory_route": memory_route,
        "phase2": phase2,
    }


def mobile_delegation_protocol_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    messages = [
        "又出现了只ack不思考的问题，找到根本原因",
        "codex_delegation mobile_ack 后没有 mobile_result_begin",
    ]
    cases: list[dict[str, Any]] = []
    ok = True
    for message in messages:
        plan = build_plan(message)
        domains = [str(item.get("key") or "") for item in plan.get("domains", [])]
        selected_skills = plan.get("skills", {}).get("selected", [])
        slash_templates = plan.get("slash_templates", {}).get("selected", [])
        lookup_terms = plan.get("tools", {}).get("lookup_terms", [])
        item_ok = (
            "bridge" in domains
            and plan.get("profile", {}).get("profile") == "mobile_delegation"
            and "mobile-weixin-bridge-ops" in selected_skills
            and "mobile-bridge-task" in slash_templates
            and "mobile-openclaw-bridge" in lookup_terms
        )
        if not item_ok:
            ok = False
        cases.append(
            {
                "message": message,
                "ok": item_ok,
                "domains": domains,
                "profile": plan.get("profile", {}),
                "selected_skills": selected_skills,
                "slash_templates": slash_templates,
                "lookup_terms": lookup_terms,
            }
        )
    return {"ok": ok, "cases": cases}


def mechanism_conflict_routing_contract(build_plan: BuildPlan) -> dict[str, Any]:
    governance = build_plan("执行已批准的治理计划，修复 mobile/mail/MCP 路由冲突")
    mobile = build_plan("<codex_delegation> prompt_schema=mobile-openclaw-final-reply/v2 mobile_ack")
    email = build_plan("查询邮件待处理回信状态")
    governance_route = governance.get("execution_route_pack", {}).get("route_decision", {})
    mobile_route = mobile.get("execution_route_pack", {}).get("route_decision", {})
    email_route = email.get("execution_route_pack", {}).get("route_decision", {})
    ok = (
        governance.get("profile", {}).get("profile") == "maintenance_governance"
        and governance_route.get("primary_domain") == "workflow_governance"
        and governance_route.get("required_next_action") == "execute_primary_workflow_phase"
        and mobile_route.get("task_contract", {}).get("task_mode") == "mobile_delegation"
        and mobile_route.get("required_next_action") == "execute_mobile_delegation_contract"
        and email_route.get("task_contract", {}).get("business_owner") == "email_scheduler"
        and email_route.get("required_next_action") == "execute_email_owner_route"
    )
    return {"ok": ok, "governance": governance_route, "mobile": mobile_route, "email": email_route}


def closeout_surface_contract() -> dict[str, Any]:
    external_candidate = {
        "source_item_id": "ek_validation_candidate",
        "title": "Validation external knowledge candidate",
        "summary": "Candidate notes generated from external knowledge must be surfaced as final review cards before long-term absorption.",
        "source_url": "https://example.invalid/source",
        "trust_tier": "official",
        "freshness_class": "stable",
        "path": "C:/tmp/external-knowledge-ek-validation-candidate.md",
        "proposed_destination_namespace": "tools.mcp.stability",
        "approval_action": "review_absorb_plan_then_apply_approved",
        "required_checks": ["deduplicate before absorption"],
    }
    package = build_closeout_package(
        {
            "record_path": "",
            "task_kind": "validation",
            "outcome": "ok",
            "notes": [],
            "proposals": [],
            "profile_candidates": {"schema": "memory_governance.profile_plan.v1", "ok": True, "candidate_count": 0, "candidates": []},
            "external_candidates": {
                "schema": "external_knowledge.pending_memory_candidates.v1",
                "ok": True,
                "exists": True,
                "trigger": "validation",
                "candidate_count": 1,
                "selected_count": 1,
                "would_write": [external_candidate],
                "requires_user_review": True,
            },
            "fallback_tools": [],
            "negative_items": [],
            "unverified_items": [
                {
                    "key": "external_research_owner_mcp_missing",
                    "value": "generic web search used without owner MCP evidence or fallback reason",
                }
            ],
            "used": {"memory": [], "skills": [], "slash_templates": [], "mcp": [], "local_tools": []},
            "skill_usage": {"selected": [], "used": [], "outcome": "ok", "record_command": ""},
            "tool_evidence": {
                "current_turn_callable": [],
                "protocol_ok_only": [],
                "fallback_used": [],
                "negative_observations": [],
                "unverified": [],
                "external_research": {
                    "web_search_used": True,
                    "owner_mcp_used": [],
                    "owner_mcp_fallback_reason": "",
                },
            },
            "work_notes": {"active_count": 0, "entries": []},
            "memory_routing": {"route_decisions": [], "layers_used": []},
            "validation": {"items": [], "required_before_final_reply": True},
        }
    )
    review_summary = package.get("final_reply_must_show", {}) if isinstance(package.get("final_reply_must_show"), dict) else {}
    cards = review_summary.get("cards", []) if isinstance(review_summary.get("cards"), list) else []
    markdown = str(review_summary.get("markdown") or "")
    card_ids = {str(card.get("id") or "") for card in cards if isinstance(card, dict)}
    kinds = {str(card.get("kind") or "") for card in cards if isinstance(card, dict)}
    ok = (
        "ek_validation_candidate" in card_ids
        and "external_research_owner_mcp_missing" in card_ids
        and "external_knowledge_memory_candidates" in kinds
        and "tool_evidence" in kinds
        and int(review_summary.get("total_review_cards") or 0) >= 2
        and "### Review Card 1:" in markdown
        and "Validation external knowledge candidate" in markdown
        and "Approval action:" in markdown
        and review_summary.get("display_contract", {}).get("show_markdown_cards") is True
    )
    return {
        "ok": ok,
        "total_review_cards": review_summary.get("total_review_cards"),
        "card_ids": sorted(card_ids),
        "kinds": sorted(kinds),
        "markdown_preview": markdown[:500],
        "display_contract": review_summary.get("display_contract", {}),
    }


def classification_fallback(build_plan: BuildPlan) -> dict[str, Any]:
    low_confidence_plan = build_plan("看看这个情况")
    low_confidence_domains = [str(item.get("key") or "") for item in low_confidence_plan.get("domains", [])]
    ambiguous_plan = build_plan("检查工具和记忆")
    ambiguous_domains = [str(item.get("key") or "") for item in ambiguous_plan.get("domains", [])]
    ambiguous_quality = [str(item.get("match_quality") or "") for item in ambiguous_plan.get("domains", [])]
    ambiguous_drivers = {
        str(item.get("key") or ""): bool(item.get("drives_execution"))
        for item in ambiguous_plan.get("domains", [])
    }
    precision_plan = build_plan(
        "目前工作环境中的关键词分流机制太粗糙了，联网获取相关成熟做法、项目，完善相关机制，让codex后续工作中命中率大幅提高"
    )
    precision_domains = [str(item.get("key") or "") for item in precision_plan.get("domains", [])]
    negated_install_plan = build_plan("查询官方文档，不要安装 package 或依赖")
    negated_install_domains = [str(item.get("key") or "") for item in negated_install_plan.get("domains", [])]
    ok = (
        low_confidence_domains == ["general"]
        and ambiguous_domains[:2] == ["memory", "mcp_tools"]
        and "general" not in ambiguous_domains
        and ambiguous_drivers.get("mcp_tools") is False
        and ambiguous_drivers.get("memory") is True
        and low_confidence_plan.get("classification", {}).get("strategy") == "weighted_signal_with_negation_and_abstention"
        and precision_domains[:2] == ["workflow_governance", "external_docs_research"]
        and "resource_acquisition" not in negated_install_domains
    )
    return {
        "ok": ok,
        "low_confidence_domains": low_confidence_domains,
        "ambiguous_domains": ambiguous_domains,
        "ambiguous_quality": ambiguous_quality,
        "ambiguous_drivers": ambiguous_drivers,
        "strategy": low_confidence_plan.get("classification", {}).get("strategy"),
        "precision_domains": precision_domains,
        "negated_install_domains": negated_install_domains,
    }


def detail_tier_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    workflow_governance_plan = build_plan("目前的工作机制需要优化精简，减少上下文消耗", detail="auto")
    micro_plan = build_plan("检查工具和记忆", detail="micro")
    standard_plan = build_plan("目前的工作机制需要优化精简，减少上下文消耗", detail="standard")
    full_plan = build_plan("检查工具和记忆", detail="full")
    codegraph_micro = build_plan("使用 CodeGraph 分析代码调用路径和影响范围", detail="micro")
    codegraph_owner_route = codegraph_micro.get("execution_route_pack", {}).get("route_decision", {}).get("owner_route", {})
    micro_skills = micro_plan.get("skill_orchestration", {}).get("selected_skills", [])
    micro_size = len(json.dumps(micro_plan, ensure_ascii=False, separators=(",", ":")))
    full_size = len(json.dumps(full_plan, ensure_ascii=False, separators=(",", ":")))
    ok = (
        workflow_governance_plan.get("detail_level") == "standard"
        and micro_plan.get("detail_level") == "micro"
        and standard_plan.get("detail_level") == "standard"
        and micro_plan.get("execution_route_pack", {}).get("schema") == "execution_route_pack.projection.v2"
        and standard_plan.get("execution_route_pack", {}).get("schema") == "execution_route_pack.projection.v2"
        and full_plan.get("execution_route_pack", {}).get("schema") == "execution_route_pack.v1"
        and "tools" not in micro_plan
        and micro_size < full_size * 0.30
        and all("path" not in item for item in micro_skills)
        and codegraph_owner_route.get("execution_affinity") == "hub_first"
        and codegraph_owner_route.get("session_binding") == "none"
        and codegraph_owner_route.get("hub_tool") == "codegraph.explore"
        and any(
            "commands" in phase and "action_contract" not in phase
            for phase in standard_plan.get("machine_phases", [])
            if isinstance(phase, dict)
        )
    )
    return {
        "ok": ok,
        "workflow_auto_detail": workflow_governance_plan.get("detail_level"),
        "micro_detail": micro_plan.get("detail_level"),
        "standard_detail": standard_plan.get("detail_level"),
        "micro_skill_fields": list((micro_skills or [{}])[0].keys()),
        "micro_size": micro_size,
        "full_size": full_size,
        "reduction_ratio": round(1 - (micro_size / full_size), 4) if full_size else 0,
        "codegraph_micro_owner_route": codegraph_owner_route,
    }


def execution_route_pack_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    research_plan = build_plan("联网搜索 GitHub issue 并获取一个资源，网络层需要给资源层路由建议", detail="full")
    unresolved_research_plan = build_plan("联网搜索相关知识，完善资源层设计", detail="full")
    narrow_iteration_plan = build_plan("修复资源层研究委托结果过窄时改为细调委托并扩展联网工具", detail="full")
    candidate_download_plan = build_plan("下载一张苹果总部建筑照片", detail="full")
    resource_governance_plan = build_plan("测试资源委托任务生命周期，发现漏洞和可优化点", detail="full")
    state_plan = build_plan("只检查桥接队列状态", detail="full")
    research_pack = research_plan.get("execution_route_pack", {})
    unresolved_pack = unresolved_research_plan.get("execution_route_pack", {})
    narrow_iteration_domains = {
        item.get("key")
        for item in narrow_iteration_plan.get("domains", [])
        if isinstance(item, dict) and item.get("drives_execution")
    }
    candidate_pack = candidate_download_plan.get("execution_route_pack", {})
    governance_pack = resource_governance_plan.get("execution_route_pack", {})
    state_pack = state_plan.get("execution_route_pack", {})
    research_owner_routes = research_pack.get("resource_gate", {}).get("owner_routes", [])
    unresolved_owner_routes = unresolved_pack.get("resource_gate", {}).get("owner_routes", [])
    research_decision = research_pack.get("route_decision", {})
    unresolved_decision = unresolved_pack.get("route_decision", {})
    candidate_gate = candidate_pack.get("resource_gate", {})
    candidate_contract = candidate_gate.get("completion_contract", {})
    candidate_policy = candidate_gate.get("candidate_review_policy", {})
    governance_decision = governance_pack.get("route_decision", {})
    state_decision = state_pack.get("route_decision", {})
    stop_if = set(research_pack.get("stop_if", []))
    ok = (
        research_pack.get("schema") == "execution_route_pack.v1"
        and unresolved_pack.get("schema") == "execution_route_pack.v1"
        and candidate_pack.get("schema") == "execution_route_pack.v1"
        and state_pack.get("schema") == "execution_route_pack.v1"
        and research_decision.get("schema") == "workflow_route_decision.v1"
        and unresolved_decision.get("schema") == "workflow_route_decision.v1"
        and state_decision.get("schema") == "workflow_route_decision.v1"
        and research_decision.get("required_next_action") == "submit_resource_request_and_wait_for_receipt"
        and unresolved_decision.get("required_next_action") == "submit_resource_request_and_wait_for_receipt"
        and unresolved_decision.get("resource_delegation_required") is True
        and "custom --request-json" in str(unresolved_pack.get("resource_gate", {}).get("submit_entrypoint", ""))
        and "job" in unresolved_pack.get("resource_gate", {}).get("job_run_command", [])
        and "run" in unresolved_pack.get("resource_gate", {}).get("job_run_command", [])
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("codex_waits_for_receipt") is True
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("not_complete_on_delegate_payload_only") is True
        and unresolved_pack.get("resource_gate", {}).get("resource_layer_source_discovery_required") is True
        and unresolved_pack.get("resource_gate", {}).get("source_discovery_owner") == "resource_layer"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("source_discovery_required") is True
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("source_discovery_owner") == "resource_layer"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("result_iteration_policy", {}).get("resource_layer_keeps_first_priority") is True
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("task_starts_at") == "job_run"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("task_ends_at") == "resource_consumed_or_resource_layer_released"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("completed_receipt_is_not_final") is True
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("consume_required_field") == "consume_required"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_consume_paths_field") == "required_consume_paths"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("consume_contract_field") == "consume_contract"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("consume_done_condition") == "codex_reads_or_evaluates_one_required_consume_path_or_records_no_read_needed_reason"
        and "job run" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("blocking_command", "")
        and "acquisition_owner" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "resource_need_satisfied" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "same_need_fetch_allowed" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "same_need_independent_direct_fetch_allowed" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "direct_generic_web_allowed" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "refine_resource_delegation_required" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "configured_online_route_chain_allowed" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "consume_required" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and "required_consume_paths" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_job_fields", [])
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("deferred_status") == "refine_resource_delegation_and_retry"
        and unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("failed_or_blocked_status") == "use_configured_owner_hub_online_route_chain_before_direct_generic_web"
        and unresolved_pack.get("resource_gate", {}).get("ownership_contract", {}).get("acquisition_owner") == "resource_layer"
        and unresolved_pack.get("resource_gate", {}).get("ownership_contract", {}).get("handoff_keeps_ownership") is True
        and unresolved_pack.get("resource_gate", {}).get("ownership_contract", {}).get("duplicate_fetch_policy", {}).get("same_need") == "do_not_start_direct_fetch_while_resource_layer_owns_request"
        and "predefined_online_route_exhausted" in unresolved_pack.get("resource_gate", {}).get("ownership_contract", {}).get("duplicate_fetch_policy", {}).get("direct_generic_web_allowed_only_with", [])
        and unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("kind") == "resource_delegate_task"
        and unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("start_action") == "submit_job_to_resource_layer"
        and unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("running_state") == "resource_layer_owns_acquisition_until_receipt"
        and "completed" in unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("end_to_end_terminal_statuses", [])
        and "handoff_required" in unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("resource_layer_receipt_statuses", [])
        and "handoff_required" not in unresolved_pack.get("resource_gate", {}).get("task_lifecycle", {}).get("end_to_end_terminal_statuses", [])
        and "request_id" in unresolved_pack.get("resource_gate", {}).get("completion_contract", {}).get("required_receipt_fields", [])
        and unresolved_decision.get("resource_completion_contract", {}).get("handoff_required_status") == "call_owner_tool_and_attach_result_to_same_request_id"
        and unresolved_decision.get("resource_task_lifecycle", {}).get("codex_during_running") == "consume_job_run_process_output_or_poll_progress; do_not_start_independent_replacement_fetch_for_same_need"
        and "handoff_required" in unresolved_decision.get("resource_task_lifecycle", {}).get("resource_layer_receipt_statuses", [])
        and "handoff_required" not in unresolved_decision.get("resource_task_lifecycle", {}).get("end_to_end_terminal_statuses", [])
        and "resource_request_id_and_receipt_when_resource_gate_enabled" in unresolved_decision.get("evidence_required", [])
        and "resource_task_progress_polled_until_terminal_receipt" in unresolved_decision.get("evidence_required", [])
        and "resource_layer_source_discovery_receipt_for_external_lookup" in unresolved_decision.get("evidence_required", [])
        and "resource_completed_receipt_consumed_or_evaluated_when_consume_required" in unresolved_decision.get("evidence_required", [])
        and "same_boundary_hub_direct_before_complete_route_when_known" in unresolved_decision.get("evidence_required", [])
        and state_decision.get("resource_delegation_required") is False
        and research_pack.get("resource_gate", {}).get("enabled") is True
        and unresolved_pack.get("resource_gate", {}).get("enabled") is True
        and "resource_acquisition" in narrow_iteration_domains
        and unresolved_pack.get("resource_gate", {}).get("auto_fill_required") is True
        and unresolved_pack.get("resource_gate", {}).get("entrypoint", "").startswith("_bridge/resource_cli.py custom --request-json")
        and "custom --request-json" in str(unresolved_pack.get("resource_gate", {}).get("submit_entrypoint", ""))
        and unresolved_pack.get("resource_gate", {}).get("structured_request_seed", {}).get("schema") == "structured_task_envelope.v1"
        and "custom" in unresolved_pack.get("resource_gate", {}).get("primary_command", [])
        and "--request-json" in unresolved_pack.get("resource_gate", {}).get("primary_command", [])
        and unresolved_pack.get("resource_gate", {}).get("delegate_payload_seed", {}).get("target") == "联网搜索相关知识，完善资源层设计"
        and "delegate" in unresolved_pack.get("resource_gate", {}).get("delegate_command", [])
        and "resource_cli.py custom" in unresolved_decision.get("resource_submit_command", "")
        and unresolved_pack.get("resource_gate", {}).get("delegation_default") == "required_for_external_or_resource_acquisition"
        and unresolved_pack.get("resource_gate", {}).get("owner_selection_required") is True
        and unresolved_pack.get("resource_gate", {}).get("generic_web_allowed") is False
        and candidate_gate.get("enabled") is True
        and candidate_gate.get("task_class") == "materialization_needs_source_selection"
        and candidate_gate.get("resource_layer_source_selection_required") is True
        and candidate_gate.get("resource_layer_source_discovery_required") is True
        and candidate_gate.get("source_discovery_owner") == "resource_layer"
        and candidate_gate.get("candidate_review_before_materialization") is True
        and candidate_gate.get("candidate_review_owner") == "codex"
        and candidate_policy.get("default_action") == "return_candidates_before_download"
        and candidate_policy.get("codex_decides_next") is True
        and candidate_contract.get("candidate_review_before_materialization") is True
        and candidate_contract.get("candidate_review_owner") == "codex"
        and candidate_contract.get("candidate_review_policy", {}).get("default_action") == "return_candidates_before_download"
        and candidate_contract.get("result_iteration_policy", {}).get("candidate_review_before_materialization") is True
        and candidate_contract.get("task_starts_at") == "job_run"
        and candidate_contract.get("task_ends_at") == "resource_consumed_or_resource_layer_released"
        and governance_pack.get("resource_gate", {}).get("enabled") is False
        and governance_decision.get("resource_delegation_required") is False
        and state_pack.get("resource_gate", {}).get("enabled") is False
        and research_pack.get("external_research_gate", {}).get("generic_web_search_requires_owner_route") is True
        and any(item.get("owner_mcp") == "github" for item in research_owner_routes if isinstance(item, dict))
        and any(item.get("owner_mcp") == "resource_layer_owner_selector" for item in unresolved_owner_routes if isinstance(item, dict))
        and "native_tool_failed_without_same_boundary_hub_or_fallback_evidence" in stop_if
        and any(policy.get("key") == "network_policy" for policy in research_pack.get("tool_policies", []) if isinstance(policy, dict))
        and any(policy.get("key") == "structured_state_policy" for policy in state_pack.get("tool_policies", []) if isinstance(policy, dict))
    )
    return {
        "ok": ok,
        "research_pack": {
            "resource_gate_enabled": research_pack.get("resource_gate", {}).get("enabled"),
            "owner_routes": research_owner_routes,
            "external_research_gate": research_pack.get("external_research_gate", {}),
            "tool_policy_keys": [item.get("key") for item in research_pack.get("tool_policies", []) if isinstance(item, dict)],
            "stop_if": sorted(stop_if),
        },
        "unresolved_research_pack": {
            "resource_gate": unresolved_pack.get("resource_gate", {}),
            "route_decision": unresolved_decision,
            "external_research_gate": unresolved_pack.get("external_research_gate", {}),
        },
        "narrow_iteration_domains": sorted(narrow_iteration_domains),
        "candidate_download_pack": {
            "resource_gate": candidate_gate,
            "route_decision": candidate_pack.get("route_decision", {}),
        },
        "state_pack": {
            "resource_gate_enabled": state_pack.get("resource_gate", {}).get("enabled"),
            "tool_policy_keys": [item.get("key") for item in state_pack.get("tool_policies", []) if isinstance(item, dict)],
            "route_decision": state_decision,
        },
        "resource_governance_pack": {
            "resource_gate_enabled": governance_pack.get("resource_gate", {}).get("enabled"),
            "route_decision": governance_decision,
        },
    }


def route_decision_ambiguity_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    ambiguous_plan = build_plan("检查工具和记忆", detail="full")
    ambiguous_decision = ambiguous_plan.get("execution_route_pack", {}).get("route_decision", {})
    direct_plan = build_plan("GitHub 搜索 semantic-router 仓库", detail="full")
    direct_decision = direct_plan.get("execution_route_pack", {}).get("route_decision", {})
    direct_priority = direct_plan.get("execution_route_pack", {}).get("mcp_boundary", {}).get("call_priority", {})
    direct_steps = direct_priority.get("steps", []) if isinstance(direct_priority.get("steps"), list) else []
    discovery_step = next((step for step in direct_steps if isinstance(step, dict) and step.get("id") == "precise_tool_discovery"), {})
    native_step = next((step for step in direct_steps if isinstance(step, dict) and step.get("id") == "native_mcp"), {})
    hub_step = next((step for step in direct_steps if isinstance(step, dict) and step.get("id") == "hub_mcp_direct"), {})
    direct_discovery_query = str(discovery_step.get("query", ""))
    direct_hub_tools = hub_step.get("tools", [])
    ok = (
        ambiguous_decision.get("schema") == "workflow_route_decision.v1"
        and ambiguous_decision.get("primary_domain") == "memory"
        and ambiguous_decision.get("ambiguity", {}).get("is_ambiguous") is False
        and ambiguous_decision.get("required_next_action") in {
            "clarify_or_run_read_only_route_probe",
            "follow_mcp_call_priority_chain",
            "run_structured_state_read_only_query",
        }
        and direct_decision.get("resource_delegation_required") is True
        and direct_decision.get("owner_route", {}).get("mcp_profile") == "github"
        and direct_priority.get("execution_affinity") == "hub_first"
        and direct_steps and direct_steps[0].get("id") == "hub_mcp_direct"
        and not discovery_step
        and not native_step
        and direct_priority.get("continuation_policy", {}).get("backward_jump_allowed") is False
        and "github.api" in direct_hub_tools
        and "github.gh" in direct_hub_tools
    )
    return {
        "ok": ok,
        "ambiguous_decision": ambiguous_decision,
        "direct_decision": direct_decision,
        "direct_discovery_query": direct_discovery_query,
        "direct_hub_tools": direct_hub_tools,
    }


def automation_delegation_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("发送一封字段完整的普通邮件，并让环境处理简单重复步骤", detail="full")
    policy = plan.get("automation_delegation", {})
    route_policy = plan.get("execution_route_pack", {}).get("automation_delegation", {})
    workflow_steps = [str(item) for item in plan.get("workflow", [])]
    ok = (
        policy.get("schema") == "workflow_automation_delegation.v1"
        and route_policy.get("schema") == "workflow_automation_delegation.v1"
        and "auto_execute" in route_policy.get("decision_classes", [])
        and "codex_deferred" in route_policy.get("decision_classes", [])
        and "review_required" in route_policy.get("decision_classes", [])
        and "blocked" in route_policy.get("decision_classes", [])
        and "fields_complete" in route_policy.get("environment_gate", [])
        and "verification_result" in route_policy.get("evidence_required", [])
        and any("delegate complete" in item for item in workflow_steps)
    )
    return {
        "ok": ok,
        "policy": {
            "schema": policy.get("schema"),
            "codex_owns": policy.get("codex_owns", [])[:3],
            "environment_gate": policy.get("environment_owns_when_all_true", [])[:3],
        },
        "route_pack": route_policy,
        "workflow_steps": workflow_steps,
    }


def system_incident_governance_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("Codex Desktop 模型列表重启后不显示，找到根因并持久修复", detail="full")
    route_pack = plan.get("execution_route_pack", {})
    policies = route_pack.get("tool_policies", []) if isinstance(route_pack.get("tool_policies"), list) else []
    policy = next((item for item in policies if item.get("key") == "system_incident_policy"), {})
    principles = set(policy.get("principles", []) if isinstance(policy.get("principles"), list) else [])
    evidence_required = set(
        policy.get("evidence_required", []) if isinstance(policy.get("evidence_required"), list) else []
    )
    ok = (
        bool(policy)
        and "system_incident_chain" in policy.get("route_terms", [])
        and "protect_native_mechanism_before_parallel_replacement" in principles
        and "respect_lifecycle_reload_restart_cache_boundaries" in principles
        and "unify_start_recover_existing_process_paths" in principles
        and "make_cross_boundary_receipts_schema_stable_and_field_safe" in principles
        and "chain_map" in evidence_required
        and "per_layer_probe_or_reason_unavailable" in evidence_required
        and "all_entry_paths_share_the_repair_or_validation_path" in evidence_required
        and "post_fix_layered_validation" in evidence_required
    )
    return {
        "ok": ok,
        "policy": policy,
        "domain_keys": route_pack.get("domain_keys", []),
    }


def self_update_governance_semantics(build_plan: BuildPlan) -> dict[str, Any]:
    plan = build_plan("技能 工作流 记忆 过时，需要自我更新机制", detail="full")
    route_pack = plan.get("execution_route_pack", {})
    policies = route_pack.get("tool_policies", []) if isinstance(route_pack.get("tool_policies"), list) else []
    policy = next((item for item in policies if item.get("key") == "self_update_policy"), {})
    principles = set(policy.get("principles", []) if isinstance(policy.get("principles"), list) else [])
    evidence_required = set(
        policy.get("evidence_required", []) if isinstance(policy.get("evidence_required"), list) else []
    )
    ok = (
        bool(policy)
        and "self_update_governance" in policy.get("route_terms", [])
        and "detect_stale_surfaces_before_adding_new_rules" in principles
        and "route_repairs_to_owner_modules" in principles
        and "self_update_governance_doctor_or_validate" in evidence_required
        and "owner_surface_for_each_risk" in evidence_required
    )
    return {"ok": ok, "policy": policy, "domain_keys": route_pack.get("domain_keys", [])}


def core_validation_checks(
    doc: dict[str, Any],
    plans: list[dict[str, Any]],
    phase_contract_result: dict[str, Any],
    profile_result: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"name": "doctor_ok", "ok": bool(doc.get("ok")), "detail": doc.get("status")},
        {
            "name": "sample_plans_ok",
            "ok": all(plan.get("ok") for plan in plans),
            "detail": [plan.get("slash_templates", {}).get("missing", []) for plan in plans],
        },
        {
            "name": "plans_are_bounded",
            "ok": all(
                len(plan.get("skills", {}).get("selected", [])) <= 4
                and len(plan.get("slash_templates", {}).get("selected", [])) <= 3
                for plan in plans
            ),
            "detail": "skill/slash selection limits",
        },
        {
            "name": "machine_phases_contract_ok",
            "ok": phase_contract_result["ok"],
            "detail": phase_contract_result["detail"],
        },
        {
            "name": "profile_routing_ok",
            "ok": profile_result["profile_ok"],
            "detail": profile_result["profile_detail"],
        },
        {
            "name": "placement_gate_in_code_phase",
            "ok": profile_result["placement_ok"],
            "detail": profile_result["placement_detail"],
        },
    ]


def routing_validation_checks(
    state_result: dict[str, Any],
    network_result: dict[str, Any],
    external_docs_result: dict[str, Any],
    github_owner_result: dict[str, Any],
    memory_governance_result: dict[str, Any],
    mobile_protocol_result: dict[str, Any],
    closeout_surface_result: dict[str, Any],
    mechanism_conflict_result: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "structured_state_routes_to_sqlite",
            "ok": state_result["routing_ok"],
            "detail": {
                "domains": state_result["domains"],
                "lookup_terms": state_result["lookup_terms"],
                "policy": state_result["policy"],
            },
        },
        {
            "name": "network_routes_to_network_doctor",
            "ok": network_result["routing_ok"],
            "detail": {
                "domains": network_result["domains"],
                "lookup_terms": network_result["lookup_terms"],
                "policy": network_result["policy"],
            },
        },
        {
            "name": "external_docs_routes_to_resource_mcps",
            "ok": external_docs_result["routing_ok"],
            "detail": {
                "domains": external_docs_result["domains"],
                "lookup_terms": external_docs_result["lookup_terms"],
                "policy": external_docs_result["policy"],
            },
        },
        {
            "name": "github_research_routes_to_github_mcp",
            "ok": github_owner_result["ok"],
            "detail": {
                "domains": github_owner_result["domains"],
                "lookup_terms": github_owner_result["lookup_terms"],
                "owner_mcp_candidates": github_owner_result["owner_mcp_candidates"],
                "quick_validation": github_owner_result["quick_validation"],
            },
        },
        {
            "name": "memory_governance_routes_to_memory_layer",
            "ok": memory_governance_result["ok"],
            "detail": memory_governance_result,
        },
        {
            "name": "mobile_ack_protocol_routes_to_bridge",
            "ok": mobile_protocol_result["ok"],
            "detail": mobile_protocol_result["cases"],
        },
        {
            "name": "closeout_surfaces_owner_mcp_and_candidate_notes",
            "ok": closeout_surface_result["ok"],
            "detail": closeout_surface_result,
        },
        {
            "name": "mechanism_conflict_routing_contract_ok",
            "ok": mechanism_conflict_result["ok"],
            "detail": mechanism_conflict_result,
        },
    ]


def classification_validation_checks(
    state_result: dict[str, Any],
    network_result: dict[str, Any],
    external_docs_result: dict[str, Any],
    resource_result: dict[str, Any],
    workflow_convergence_result: dict[str, Any],
    fallback_result: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "classification_semantics_ok",
            "ok": state_result["semantics_ok"]
            and network_result["semantics_ok"]
            and external_docs_result["semantics_ok"]
            and resource_result["semantics_ok"],
            "detail": {
                "structured_state": state_result["domain_detail"],
                "network_routing": network_result["domain_detail"],
                "external_docs_research": external_docs_result["domain_detail"],
                "resource_acquisition": resource_result["domain_detail"],
                "profile": {
                    "structured_state": state_result["profile"],
                    "network_routing": network_result["profile"],
                    "external_docs_research": external_docs_result["profile"],
                    "resource_acquisition": resource_result["profile"],
                },
            },
        },
        {
            "name": "classification_confidence_fallback_ok",
            "ok": fallback_result["ok"],
            "detail": {
                "low_confidence_domains": fallback_result["low_confidence_domains"],
                "ambiguous_domains": fallback_result["ambiguous_domains"],
                "ambiguous_quality": fallback_result["ambiguous_quality"],
                "ambiguous_drivers": fallback_result["ambiguous_drivers"],
                "strategy": fallback_result["strategy"],
            },
        },
        {
            "name": "workflow_routing_convergence_ok",
            "ok": workflow_convergence_result["ok"],
            "detail": workflow_convergence_result,
        },
    ]


def contract_validation_checks(
    detail_result: dict[str, Any],
    route_pack_result: dict[str, Any],
    route_decision_result: dict[str, Any],
    automation_result: dict[str, Any],
    system_incident_result: dict[str, Any],
    self_update_result: dict[str, Any],
    machine_enum_result: dict[str, Any],
    online_gate_result: dict[str, Any],
    resource_broker_result: dict[str, Any],
    maintenance_upgrade_result: dict[str, Any],
    tool_utilization_result: dict[str, Any],
    plans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "detail_tiers_reduce_context",
            "ok": detail_result["ok"],
            "detail": {
                "workflow_auto_detail": detail_result["workflow_auto_detail"],
                "micro_detail": detail_result["micro_detail"],
                "standard_detail": detail_result["standard_detail"],
                "micro_skill_fields": detail_result["micro_skill_fields"],
            },
        },
        {
            "name": "execution_route_pack_contract_ok",
            "ok": route_pack_result["ok"],
            "detail": {
                "research_pack": route_pack_result["research_pack"],
                "state_pack": route_pack_result["state_pack"],
            },
        },
        {
            "name": "route_decision_contract_ok",
            "ok": route_decision_result["ok"],
            "detail": route_decision_result,
        },
        {
            "name": "automation_delegation_contract_ok",
            "ok": automation_result["ok"],
            "detail": automation_result,
        },
        {
            "name": "system_incident_governance_contract_ok",
            "ok": system_incident_result["ok"],
            "detail": system_incident_result,
        },
        {
            "name": "self_update_governance_contract_ok",
            "ok": self_update_result["ok"],
            "detail": self_update_result,
        },
        {
            "name": "machine_enum_cli_contract_ok",
            "ok": machine_enum_result["ok"],
            "detail": machine_enum_result,
        },
        {
            "name": "online_access_gate_contract_ok",
            "ok": online_gate_result["ok"],
            "detail": online_gate_result,
        },
        {
            "name": "resource_broker_contract_ok",
            "ok": resource_broker_result["ok"],
            "detail": resource_broker_result,
        },
        {
            "name": "maintenance_upgrade_governance_contract_ok",
            "ok": maintenance_upgrade_result["ok"],
            "detail": maintenance_upgrade_result,
        },
        {
            "name": "tool_utilization_audit_contract_ok",
            "ok": tool_utilization_result["ok"],
            "detail": tool_utilization_result,
        },
        {
            "name": "skill_orchestration_available",
            "ok": all(plan.get("skill_orchestration", {}).get("ok") for plan in plans),
            "detail": "dynamic skill preflight",
        },
    ]


def surface_validation_checks(
    maintenance_surface_map: Path,
    agents_mirror: Path,
    code_maintainability: Path,
) -> list[dict[str, Any]]:
    maintenance_upgrade_governance = code_maintainability.parent / "maintenance_upgrade_governance.py"
    return [
        {
            "name": "maintenance_surface_map_present",
            "ok": maintenance_surface_map.exists(),
            "detail": str(maintenance_surface_map),
        },
        {
            "name": "agents_rule_mirror_available",
            "ok": agents_mirror.exists(),
            "detail": str(agents_mirror),
        },
        {
            "name": "module_context_entrypoint_available",
            "ok": code_maintainability.exists(),
            "detail": str(code_maintainability),
        },
        {
            "name": "maintenance_upgrade_governance_entrypoint_available",
            "ok": maintenance_upgrade_governance.exists(),
            "detail": str(maintenance_upgrade_governance),
        },
    ]


def build_validation_checks(
    doc: dict[str, Any],
    plans: list[dict[str, Any]],
    build_plan: BuildPlan,
    maintenance_surface_map: Path,
    agents_mirror: Path,
    code_maintainability: Path,
) -> list[dict[str, Any]]:
    phase_contract_result = phase_contract(plans)
    profile_result = profile_and_placement(build_plan)
    state_result = structured_state_semantics(build_plan)
    network_result = network_routing_semantics(build_plan)
    external_docs_result = external_docs_semantics(build_plan)
    resource_result = resource_acquisition_semantics(build_plan)
    workflow_convergence_result = workflow_routing_convergence_semantics(build_plan)
    github_owner_result = github_owner_mcp_research_semantics(build_plan)
    memory_governance_result = memory_governance_semantics(build_plan)
    mobile_protocol_result = mobile_delegation_protocol_semantics(build_plan)
    mechanism_conflict_result = mechanism_conflict_routing_contract(build_plan)
    closeout_surface_result = closeout_surface_contract()
    fallback_result = classification_fallback(build_plan)
    detail_result = detail_tier_semantics(build_plan)
    route_pack_result = execution_route_pack_semantics(build_plan)
    route_decision_result = route_decision_ambiguity_semantics(build_plan)
    automation_result = automation_delegation_semantics(build_plan)
    system_incident_result = system_incident_governance_semantics(build_plan)
    self_update_result = self_update_governance_semantics(build_plan)
    machine_enum_result = machine_enum_cli_contract()
    online_gate_result = online_access_gate_contract()
    resource_broker_result = resource_broker_contract()
    maintenance_upgrade_result = maintenance_upgrade_governance_contract()
    tool_utilization_result = validate_tool_utilization_with_build_plan(build_plan)
    owner_facade_result = validate_owner_facade()
    return [
        *core_validation_checks(doc, plans, phase_contract_result, profile_result),
        *routing_validation_checks(
            state_result,
            network_result,
            external_docs_result,
            github_owner_result,
            memory_governance_result,
            mobile_protocol_result,
            closeout_surface_result,
            mechanism_conflict_result,
        ),
        *classification_validation_checks(
            state_result,
            network_result,
            external_docs_result,
            resource_result,
            workflow_convergence_result,
            fallback_result,
        ),
        *contract_validation_checks(
            detail_result,
            route_pack_result,
            route_decision_result,
            automation_result,
            system_incident_result,
            self_update_result,
            machine_enum_result,
            online_gate_result,
            resource_broker_result,
            maintenance_upgrade_result,
            tool_utilization_result,
            plans,
        ),
        *surface_validation_checks(maintenance_surface_map, agents_mirror, code_maintainability),
        {
            "name": "contract_driven_owner_facade_ok",
            "ok": bool(owner_facade_result.get("ok")),
            "detail": {
                "issues": owner_facade_result.get("issues", []),
                "no_central_queue": owner_facade_result.get("checks", {}).get("no_central_queue"),
                "no_business_state_database": owner_facade_result.get("checks", {}).get("no_business_state_database"),
            },
        },
    ]
