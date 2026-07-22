#!/usr/bin/env python3
"""Task-mode and owner precedence contract for workflow routing.

Ownership: resolve explicit task contracts before keyword-scored evidence routes.
Non-goals: classify every domain, execute tools, or replace owner-specific policy.
State behavior: pure and read-only.
Caller context: workflow_orchestrator and execution_route_pack use this contract.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from structured_task_envelope import resource_task_facts

from bounded_output import bounded_payload
from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, print_json


GOVERNANCE_ACTION_TERMS = (
    "批准", "执行修复", "修复计划", "治理计划", "统一", "重构", "架构", "持久化", "落地",
    "实施", "修复", "根治", "优化", "改进", "治理",
    "implement", "fix", "optimize", "improve", "refactor", "governance",
)

SYSTEM_CHANGE_ACTION_TERMS = (
    "新增", "添加", "引入", "接入", "注册", "集成", "纳入", "替换", "迁移",
    "重命名", "拆分", "合并", "重构", "退役", "淘汰", "降级", "移除", "删除", "启用", "停用",
    "部署", "发布", "卸载", "切换",
    "add", "introduce", "integrate", "register", "replace", "migrate",
    "rename", "split", "merge", "refactor", "retire", "remove", "decommission",
    "enable", "disable", "deploy", "publish", "uninstall", "switch",
)

SYSTEM_CHANGE_SOFT_ACTION_TERMS = ("安装", "完善", "优化", "升级", "install", "improve", "optimize", "upgrade")

SYSTEM_CHANGE_EXPLICIT_CONTEXT_TERMS = (
    "成员", "成员契约", "系统组件", "系统架构", "纳入工作环境", "生命周期", "激活边界",
    "wsl主环境", "wsl 主环境", "原生工作区", "工作git", "工作 git", "裸git仓库", "裸 git 仓库",
    "system membership", "system component", "architecture", "lifecycle", "activation boundary",
    "wsl primary workspace", "work git", "bare git",
)

SYSTEM_MEMBER_TERMS = (
    "成员", "成员契约", "系统组件", "模块", "路由", "适配器", "服务",
    "计划任务", "供应商", "模型提供商", "插件", "技能", "工作流", "工具", "mcp",
    "命令", "自定义命令", "cli", "脚本入口", "后台进程", "守护进程", "工作进程",
    "hook", "profile", "索引", "数据库", "协议", "契约", "工作区", "原生工作区",
    "wsl", "wsl工作区", "wsl 工作区", "工作git", "工作 git", "裸git仓库", "裸 git 仓库",
    "owner", "adapter", "service", "scheduled task", "provider", "plugin", "skill",
    "workflow", "tool", "module", "route", "schema", "daemon", "worker", "command",
    "index", "database", "protocol", "contract", "system component", "workspace",
    "worktree", "work git", "bare git",
)

READ_ONLY_OVERRIDE_TERMS = (
    "只分析", "仅分析", "不要修改", "不修改", "只读", "先做计划", "只做计划",
    "read only", "read-only", "do not modify", "analysis only", "plan only",
)

# Soft admission evidence only. These signals indicate that current or external
# knowledge may materially improve the answer, but they do not authorize
# network use and do not create a blocking resource gate by themselves.
EXTERNAL_KNOWLEDGE_CANDIDATE_TERMS = (
    "最新", "当前版本", "目前版本", "现在的", "今天", "近期", "推荐", "选型",
    "查找", "搜索", "调研", "校对", "核实", "验证一下", "开源项目", "github",
    "官方资料", "官方说明", "供应商", "价格", "政策", "法规", "兼容性",
    "latest", "current version", "today", "recent", "recommend", "compare",
    "research", "look up", "verify", "open source project", "github",
    "vendor", "pricing", "policy", "compatibility",
)

TASK_FACT_TERMS: dict[str, tuple[str, ...]] = {
    "local_write": (
        "修改", "修复", "执行", "落地", "实施", "创建", "新增", "删除", "移除", "更新", "重构", "写入",
        "modify", "fix", "implement", "apply", "create", "add", "delete", "remove", "update", "refactor", "write",
    ),
    "config_change": (
        "配置修改", "修改配置", "配置更新", "供应商切换", "模型配置", "启动配置", "环境变量",
        "config change", "configuration update", "provider switch", "environment variable",
    ),
    "external_network_read": (
        "联网", "联网搜索", "网上搜索", "外部资料", "官方文档", "github项目", "在线查询",
        "online research", "web research", "external docs", "official documentation", "search online",
    ),
    "external_write": (
        "发送邮件", "发布网页", "提交远程", "上传", "发消息", "外部写入",
        "send email", "publish", "post", "upload", "remote write", "submit remote",
    ),
    "resource_materialization": (
        "__structured_resource_materialization_only__",
    ),
    "package_install": (
        "安装", "安装软件", "安装工具", "安装包", "安装依赖", "卸载", "卸载软件", "升级依赖", "升级软件包",
        "install", "install package", "install tool", "install dependency", "uninstall", "uninstall package", "upgrade package",
    ),
    "database_write": (
        "写数据库", "更新数据库", "插入数据库", "删除记录", "清理数据库", "vacuum数据库",
        "database write", "update database", "insert into database", "delete records", "vacuum database",
    ),
    "gui_or_browser_state": (
        "桌面界面", "刷新页面", "浏览器操作", "点击", "可见", "ui", "gui", "browser", "refresh page", "click",
    ),
    "secret_or_permission_use": (
        "密钥", "令牌", "密码", "凭据", "授权", "提权", "管理员权限",
        "secret", "token", "password", "credential", "authorization", "elevated", "administrator",
    ),
    "destructive_or_high_risk": (
        "彻底删除", "永久禁用", "强制终止", "格式化", "清空", "批量删除", "系统网络策略",
        "permanently disable", "force kill", "format disk", "purge", "bulk delete", "system network policy",
    ),
    "reload_or_restart_required": (
        "重启 codex", "重启服务", "重启应用", "重启桌面", "重新启动服务", "重新启动应用",
        "配置修改并重载", "配置更新并重载", "重载服务", "重载应用", "刷新桌面",
        "restart codex", "restart service", "restart the service", "restart app", "restart the app",
        "restart application", "restart the application", "restart server", "restart the server",
        "reload app", "reload the app", "reload service", "reload the service", "reload server",
        "reload the server", "relaunch",
    ),
}

MUTATING_FACTS = {
    "local_write",
    "config_change",
    "system_member_change",
    "external_write",
    "resource_materialization",
    "package_install",
    "database_write",
    "destructive_or_high_risk",
}

FACT_GATE_CONTRACTS: dict[str, dict[str, str]] = {
    "local_write": {
        "owner": "workspace_editing_and_backup",
        "completion": "specific write is authorized, routed backup exists, owner boundary is known, and changed files are read back",
        "stop_if": "write_without_authorization_backup_or_owner",
    },
    "config_change": {
        "owner": "configuration_owner_and_startup_baseline",
        "completion": "authoritative config, generated/runtime projections, reload boundary, validation, and baseline adoption are reconciled",
        "stop_if": "config_write_without_authoritative_owner_or_reload_plan",
    },
    "system_member_change": {
        "owner": "system_membership",
        "completion": "membership admission, changed-file impact, owner validation, activation boundary, and closeout receipt are complete",
        "stop_if": "system_member_change_without_membership_reconciliation",
    },
    "external_network_read": {
        "owner": "resource_layer",
        "completion": "structured resource request reaches a useful result or the configured owner/fallback route is terminally exhausted",
        "stop_if": "direct_generic_web_without_resource_or_explicit_exception",
    },
    "external_write": {
        "owner": "external_action_owner",
        "completion": "destination, payload, authorization, remote receipt, and readback are explicit",
        "stop_if": "external_write_without_explicit_authorization_or_receipt",
    },
    "resource_materialization": {
        "owner": "resource_layer",
        "completion": "destination policy, source, size/hash when applicable, saved path, and consumption receipt are present",
        "stop_if": "materialization_without_destination_or_receipt",
    },
    "package_install": {
        "owner": "resource_package_owner",
        "completion": "install approval, source, version, package-manager receipt, executable verification, and rollback boundary are present",
        "stop_if": "package_install_without_explicit_install_approval",
    },
    "database_write": {
        "owner": "database_business_owner",
        "completion": "business owner performs the write and indexed readback proves the intended state transition",
        "stop_if": "production_database_write_through_read_only_or_ad_hoc_surface",
    },
    "gui_or_browser_state": {
        "owner": "gui_or_browser_owner",
        "completion": "visible or machine-readable state is verified after the action",
        "stop_if": "gui_success_inferred_from_source_or_click_only",
    },
    "secret_or_permission_use": {
        "owner": "permission_and_secret_owner",
        "completion": "least-privilege permission boundary is preserved and secret values are not exposed",
        "stop_if": "secret_or_permission_boundary_unclear",
    },
    "destructive_or_high_risk": {
        "owner": "risk_and_rollback_owner",
        "completion": "target scope, explicit confirmation, backup/rollback, and post-action validation are complete",
        "stop_if": "high_risk_action_without_confirmation_or_rollback",
    },
    "reload_or_restart_required": {
        "owner": "runtime_activation_owner",
        "completion": "required reload/restart boundary is stated and activation is validated after that boundary",
        "stop_if": "claim_activation_before_required_reload_or_restart",
    },
    "durable_closeout_required": {
        "owner": "workflow_finalization",
        "completion": "changed files, owner validators, durable lessons/config state, and required receipts are reconciled",
        "stop_if": "durable_change_without_closeout_reconciliation",
    },
    "explicit_mobile_envelope": {
        "owner": "mobile_openclaw_bridge",
        "completion": "ack, supplement consumption, permission contract, work result, and exact result markers are satisfied",
        "stop_if": "mobile_contract_incomplete",
    },
}


def derive_task_facts(
    message: str,
    domain_keys: list[str],
    structured_facts: Mapping[str, Any] | None = None,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Derive mandatory task facts with explicit structured values taking precedence."""

    text = str(message or "").lower()
    explicit = dict(structured_facts or {})
    facts: dict[str, bool] = {}
    provenance: dict[str, Any] = {}
    read_only_override = bool(matched_terms(text, READ_ONLY_OVERRIDE_TERMS))
    for fact, terms in TASK_FACT_TERMS.items():
        if fact in explicit:
            facts[fact] = bool(explicit[fact])
            provenance[fact] = {"source": "explicit_structured_field", "value": facts[fact], "matched": []}
            continue
        hits = matched_terms(text, terms)
        value = bool(hits)
        if fact == "local_write" and read_only_override and not matched_terms(text, ("执行修复", "批准执行", "开始执行", "实施修复")):
            value = False
        facts[fact] = value
        provenance[fact] = {"source": "natural_language_candidate", "value": value, "matched": hits[:6]}
    knowledge_hits = matched_terms(text, EXTERNAL_KNOWLEDGE_CANDIDATE_TERMS)
    knowledge_candidate = bool(facts.get("external_network_read") or knowledge_hits)
    if "external_knowledge_candidate" in explicit:
        knowledge_candidate = bool(explicit["external_knowledge_candidate"])
    facts["external_knowledge_candidate"] = knowledge_candidate
    provenance["external_knowledge_candidate"] = {
        "source": "explicit_structured_field" if "external_knowledge_candidate" in explicit else "soft_admission_evidence",
        "value": knowledge_candidate,
        "matched": knowledge_hits[:6],
        "non_blocking": True,
    }
    actual_mobile = "<codex_delegation>" in text or "prompt_schema=mobile-openclaw-final-reply" in text
    facts["explicit_mobile_envelope"] = bool(explicit.get("explicit_mobile_envelope", actual_mobile))
    provenance["explicit_mobile_envelope"] = {
        "source": "explicit_structured_field" if "explicit_mobile_envelope" in explicit else "protocol_envelope",
        "value": facts["explicit_mobile_envelope"],
        "matched": ["mobile_envelope"] if actual_mobile else [],
    }
    system_change = build_system_change_gate(message) is not None
    facts["system_member_change"] = bool(explicit.get("system_member_change", system_change))
    provenance["system_member_change"] = {
        "source": "explicit_structured_field" if "system_member_change" in explicit else "system_change_contract",
        "value": facts["system_member_change"],
        "matched": ["system_change_gate"] if system_change else [],
    }
    durable = any(facts.get(key, False) for key in MUTATING_FACTS)
    facts["durable_closeout_required"] = bool(explicit.get("durable_closeout_required", durable))
    provenance["durable_closeout_required"] = {
        "source": "explicit_structured_field" if "durable_closeout_required" in explicit else "derived_from_mutating_facts",
        "value": facts["durable_closeout_required"],
        "matched": [key for key in MUTATING_FACTS if facts.get(key)][:8],
    }
    return facts, provenance


def structured_facts_from_envelope(envelope: Mapping[str, Any] | None) -> dict[str, bool]:
    """Delegate resource-field interpretation to the structured-envelope owner."""
    return resource_task_facts(dict(envelope or {}))


def task_fact_gates(facts: Mapping[str, bool]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for fact, value in facts.items():
        contract = FACT_GATE_CONTRACTS.get(fact)
        if not value or not contract:
            continue
        gates.append(
            {
                "schema": "task_fact_gate.v1",
                "fact": fact,
                "required": True,
                "owner": contract["owner"],
                "completion": contract["completion"],
                "stop_if": [contract["stop_if"]],
            }
        )
    return gates


@dataclass(frozen=True)
class TaskRouteContract:
    task_mode: str
    business_owner: str
    evidence_owner: str
    primary_domain_override: str
    profile_override: str
    required_next_action: str
    reason: str
    system_change_gate: dict[str, Any] | None = None
    task_facts: dict[str, bool] | None = None
    matched_signals: dict[str, Any] | None = None
    required_gates: list[dict[str, Any]] | None = None
    validation: list[str] | None = None
    closeout: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"schema": "task_route_contract.v1", **asdict(self)}
        if not payload.get("system_change_gate"):
            payload.pop("system_change_gate", None)
        for key in ("task_facts", "matched_signals", "required_gates", "validation", "closeout"):
            if payload.get(key) in (None, {}, []):
                payload.pop(key, None)
        return payload


def build_system_change_gate(message: str) -> dict[str, Any] | None:
    """Return a compact admission/reconciliation gate for member changes."""

    text = str(message or "").lower()
    action_hits = matched_terms(text, SYSTEM_CHANGE_ACTION_TERMS)
    soft_action_hits = matched_terms(text, SYSTEM_CHANGE_SOFT_ACTION_TERMS)
    member_hits = matched_terms(text, SYSTEM_MEMBER_TERMS)
    explicit_context_hits = matched_terms(text, SYSTEM_CHANGE_EXPLICIT_CONTEXT_TERMS)
    if not member_hits or not (action_hits or (soft_action_hits and explicit_context_hits)):
        return None
    return {
        "schema": "system_change_gate.v1",
        "triggered": True,
        "matched_actions": [*action_hits, *soft_action_hits][:5],
        "matched_members": member_hits[:5],
        "matched_explicit_context": explicit_context_hits[:5],
        "pre_change": {
            "required": True,
            "command": "python _bridge\\system_membership.py plan --system <system> --member <member> [--kind <supported-kind>]",
            "completion": "owner, lifecycle, authoritative state, activation, dependency, maintenance, and retirement obligations are identified",
        },
        "post_change": {
            "required": True,
            "command": "python _bridge\\system_membership.py impact --changed <changed-file>",
            "completion": "every affected surface is updated, validated, or explicitly recorded as not applicable",
        },
        "closeout": {
            "required": True,
            "command": "python _bridge\\codex_workflow_entry.py closeout ... --finalization-changed-file <file> --validation-receipt system_membership=ok",
            "completion": "workflow finalization re-runs changed-file impact, validates the membership contract, and refuses a successful closeout when required evidence is missing",
            "required_receipt": "system_membership=ok",
            "fallback_guard": "changed-file reconciliation still runs when the pre-change text classifier missed the member change",
        },
        "activation_rule": "do not claim the new or changed member integrated until the pre-change plan, post-change impact, owner validation, and reload/restart boundary are complete",
        "stop_if": [
            "member_identity_or_owner_unknown",
            "required_membership_surface_unresolved",
            "activation_claim_without_runtime_or_reload_evidence",
            "successful_closeout_without_changed_file_membership_reconciliation",
        ],
    }


def resolve_task_route_contract(
    message: str,
    domain_keys: list[str],
    structured_facts: Mapping[str, Any] | None = None,
) -> TaskRouteContract:
    """Resolve explicit task semantics before generic evidence/tool policies."""

    text = str(message or "").lower()
    domains = set(domain_keys)
    system_change_gate = build_system_change_gate(message)
    facts, signals = derive_task_facts(message, domain_keys, structured_facts)
    gates = task_fact_gates(facts)
    validation = [
        "validate every triggered task_fact gate through its owner",
        "consume owner results against caller acceptance predicates",
    ]
    closeout = {
        "required": bool(facts.get("durable_closeout_required")),
        "owner": "workflow_finalization",
        "required_receipts": [
            gate.get("owner") for gate in gates if gate.get("fact") in MUTATING_FACTS
        ][:12],
    }
    governance_change = bool(matched_terms(text, GOVERNANCE_ACTION_TERMS)) and bool(
        matched_terms(text, ("系统治理", "规则治理", "规范重构", "全局规范", "机制冲突", "路由冲突", "工作环境", "资源层", "workflow governance", "rule governance", "resource layer", "mcp routing"))
    )
    wsl_workspace_change = bool(
        matched_terms(
            text,
            (
                "wsl主环境",
                "wsl 主环境",
                "wsl作为主环境",
                "wsl 作为主环境",
                "wsl工作区",
                "wsl 工作区",
                "淘汰原生工作区",
                "原生工作区",
                "裸git仓库",
                "裸 git 仓库",
                "工作git",
                "工作 git",
                "work git",
                "bare git",
                "wsl primary workspace",
                "primary wsl workspace",
                "declarative work git",
            ),
        )
        or "wsl_workspace" in domains
    )
    actual_mobile_delegation = "<codex_delegation>" in text or "prompt_schema=mobile-openclaw-final-reply" in text
    if wsl_workspace_change and not actual_mobile_delegation:
        return TaskRouteContract(
            "wsl_workspace_lifecycle", "wsl_workspace", "work_git_and_wsl_workspace_receipts",
            "wsl_workspace", "workspace_lifecycle", "execute_wsl_workspace_owner_route",
            "explicit_wsl_workspace_lifecycle_contract", system_change_gate, facts, signals, gates,
            [*validation, "wsl workspace owner status/plan/validate must prove Work Git, bare Git, and mirror boundary readiness"], closeout,
        )
    if governance_change and "resource_acquisition" in domains and not actual_mobile_delegation:
        return TaskRouteContract(
            "resource_governance", "resource_broker", "resource_receipts",
            "resource_acquisition", "maintenance_governance", "execute_primary_workflow_phase",
            "explicit_resource_governance_contract", system_change_gate, facts, signals, gates,
            [*validation, "resource acceptance and total-budget validators must pass"], closeout,
        )
    if governance_change and not actual_mobile_delegation:
        return TaskRouteContract(
            "system_governance", "workflow_governance", "conditional_by_change_surface",
            "workflow_governance", "maintenance_governance", "execute_primary_workflow_phase",
            "explicit_governance_change_contract", system_change_gate, facts, signals, gates, validation, closeout,
        )
    if actual_mobile_delegation:
        return TaskRouteContract(
            "mobile_delegation", "mobile_openclaw_bridge", "mobile_bridge_state",
            "bridge", "mobile_delegation", "execute_mobile_delegation_contract",
            "explicit_mobile_delegation_envelope", system_change_gate, facts, signals, gates, validation, closeout,
        )
    if "email" in domains:
        return TaskRouteContract(
            "email", "email_scheduler", "email_state_sqlite",
            "email", "", "execute_email_owner_route",
            "email_business_owner_precedes_structured_state", system_change_gate, facts, signals, gates, validation, closeout,
        )
    return TaskRouteContract(
        "general", "", "", "", "", "", "no_explicit_contract", system_change_gate,
        facts, signals, gates, validation, closeout,
    )


def validate() -> dict[str, Any]:
    governance = resolve_task_route_contract(
        "执行已批准的治理计划，修复 mobile/mail/MCP 路由冲突",
        ["workflow_governance", "bridge", "email"],
    )
    mobile = resolve_task_route_contract(
        "<codex_delegation> prompt_schema=mobile-openclaw-final-reply/v2", ["bridge"]
    )
    email = resolve_task_route_contract("检查邮件待处理回信状态", ["email", "structured_state"])
    membership = resolve_task_route_contract("新增一个 MCP server 并纳入工作环境", ["mcp_tools", "workflow_governance"])
    ordinary = resolve_task_route_contract("查询一个 GitHub 项目", ["github"])
    package_install = resolve_task_route_contract("安装 aria2 工具", ["resource_acquisition"])
    command_member = resolve_task_route_contract("启用新的自定义命令并发布到工作环境", ["workflow_governance"])
    wsl_workspace = resolve_task_route_contract("逐步淘汰原生工作区，将 WSL 作为主环境", ["wsl_workspace"])
    governance_rebuild = resolve_task_route_contract(
        "实施全局规范重构并联网搜索成熟做法，修改 AGENTS 后完成收口",
        ["workflow_governance", "external_docs_research"],
    )
    read_only = resolve_task_route_contract("只分析当前规则，不要修改文件", ["workflow_governance"])
    explicit_override = resolve_task_route_contract(
        "分析当前规则",
        ["workflow_governance"],
        {"local_write": True, "external_network_read": True},
    )
    resource_governance = resolve_task_route_contract(
        "根治资源层空结果假完成和总预算耗尽问题",
        ["resource_acquisition", "workflow_governance"],
    )
    ok = (
        governance.task_mode == "system_governance"
        and mobile.task_mode == "mobile_delegation"
        and email.business_owner == "email_scheduler"
        and bool(membership.system_change_gate and membership.system_change_gate.get("triggered"))
        and ordinary.system_change_gate is None
        and package_install.system_change_gate is None
        and bool(command_member.system_change_gate and command_member.system_change_gate.get("triggered"))
        and wsl_workspace.business_owner == "wsl_workspace"
        and wsl_workspace.primary_domain_override == "wsl_workspace"
        and wsl_workspace.system_change_gate is not None
        and governance_rebuild.task_facts.get("local_write") is True
        and governance_rebuild.task_facts.get("external_network_read") is True
        and governance_rebuild.task_facts.get("durable_closeout_required") is True
        and read_only.task_facts.get("local_write") is False
        and explicit_override.task_facts.get("local_write") is True
        and explicit_override.matched_signals.get("local_write", {}).get("source") == "explicit_structured_field"
        and resource_governance.task_mode == "resource_governance"
        and resource_governance.business_owner == "resource_broker"
        and resource_governance.primary_domain_override == "resource_acquisition"
        and resource_governance.profile_override == "maintenance_governance"
    )
    return {
        "schema": "task_route_contract.validate.v1",
        "ok": ok,
        "governance": governance.to_dict(),
        "mobile": mobile.to_dict(),
        "email": email.to_dict(),
        "membership": membership.to_dict(),
        "ordinary": ordinary.to_dict(),
        "package_install": package_install.to_dict(),
        "command_member": command_member.to_dict(),
        "wsl_workspace": wsl_workspace.to_dict(),
        "governance_rebuild": governance_rebuild.to_dict(),
        "read_only": read_only.to_dict(),
        "explicit_override": explicit_override.to_dict(),
        "resource_governance": resource_governance.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Validate explicit task route contracts.")
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    payload = validate() if args.command == "validate" else {"ok": False, "reason": "unknown_command"}
    if args.full:
        output = payload
    else:
        cases = {
            key: {
                "task_mode": value.get("task_mode"),
                "business_owner": value.get("business_owner"),
                "true_facts": [fact for fact, enabled in (value.get("task_facts") or {}).items() if enabled],
                "gate_facts": [gate.get("fact") for gate in value.get("required_gates") or [] if isinstance(gate, dict)],
                "reason": value.get("reason"),
            }
            for key, value in payload.items()
            if isinstance(value, dict) and key not in {"schema"}
        }
        output = bounded_payload(
            {"schema": payload.get("schema"), "ok": payload.get("ok"), "cases": cases},
            max_bytes=12 * 1024,
            artifact_ref="command:python _bridge/task_route_contract.py validate --full",
        )
        output["raw_result_ref"] = "command:python _bridge/task_route_contract.py validate --full"
    print_json(output)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
