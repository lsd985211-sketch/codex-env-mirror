#!/usr/bin/env python3
"""Read-only planner for system maintenance and upgrade governance.

Ownership: builds a batchable, evidence-aware plan for changing an existing
system without treating module split, CodeGraph, SQLite, or any validator as a
fixed ritual.
Non-goals: applying repairs, mutating state, replacing owner validators, or
forcing every task through the same tool chain.
State behavior: read-only; emits machine-readable plans and validation checks.
Caller context: Codex workflow phase 6, system membership closeout, and broad
maintenance/upgrade work where Codex needs to decide where to edit first.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
SCHEMA = "maintenance_upgrade_governance.v1"


def uniq(values: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def text_terms(message: str, explicit_terms: list[str]) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./\\-]+|[\u4e00-\u9fff]{2,}", message)
    return uniq([*explicit_terms, *[word.lower() for word in words]], 16)


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return bool(matched_terms(text, needles))


SYSTEM_RULES: list[dict[str, Any]] = [
    {
        "system": "workflow",
        "keywords": ("workflow", "工作流", "编排", "准则", "route", "phase", "closeout", "preflight"),
        "owner_modules": [
            "_bridge/workflow_orchestrator.py",
            "_bridge/workflow_validation.py",
            "_bridge/execution_route_pack.py",
            "_bridge/task_route_contract.py",
            "_bridge/codex_workflow_entry.py",
        ],
        "owner_validators": [
            "python _bridge\\workflow_orchestrator.py validate",
            "python _bridge\\task_route_contract.py validate",
        ],
    },
    {
        "system": "mcp",
        "keywords": ("mcp", "hub", "tool", "工具", "current-turn", "transport closed"),
        "owner_modules": [
            "_bridge/mcp_session_doctor.py",
            "_bridge/local_mcp_hub.py",
            "_bridge/mcp_capability_routes.py",
        ],
        "owner_validators": [
            "python _bridge\\mcp_session_doctor.py validate",
            "python _bridge\\local_mcp_hub.py validate",
        ],
    },
    {
        "system": "resource",
        "keywords": ("resource", "资源层", "资源获取", "broker", "download", "handoff_required"),
        "owner_modules": [
            "_bridge/resource_broker.py",
            "_bridge/resource_cli.py",
            "_bridge/resource_router.py",
            "_bridge/resource_fetcher.py",
        ],
        "owner_validators": ["python _bridge\\resource_process_doctor.py validate"],
    },
    {
        "system": "network",
        "keywords": ("network", "proxy", "dns", "网关", "网络", "代理", "lease"),
        "owner_modules": [
            "_bridge/codex_network_gateway.py",
            "_bridge/network_doctor.py",
            "_bridge/network_policy.py",
        ],
        "owner_validators": ["python _bridge\\codex_network_gateway.py validate"],
    },
    {
        "system": "bridge",
        "keywords": ("mobile", "weixin", "openclaw", "ack", "微信", "手机", "桥接", "只ack"),
        "owner_modules": [
            "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
            "_bridge/mobile_openclaw_bridge/mobile_maintenance.py",
            "_bridge/mobile_openclaw_bridge/mobile_prompt_contract.py",
        ],
        "owner_validators": ["mobile bridge owner validate or maintenance summary"],
    },
    {
        "system": "mail",
        "keywords": ("email", "mail", "imap", "smtp", "邮件", "邮箱", "附件", "回信"),
        "owner_modules": [
            "_bridge/shared/email_scheduler.py",
            "_bridge/email_state_query.py",
        ],
        "owner_validators": ["email scheduler validate / mailbox queue readback"],
    },
    {
        "system": "memory",
        "keywords": ("memory", "pmb", "记忆", "画像", "候选记忆", "work note"),
        "owner_modules": [
            "_bridge/memory_router.py",
            "_bridge/memory_governance.py",
            "_bridge/local_pmb_memory.py",
        ],
        "owner_validators": ["python _bridge\\memory_router.py validate", "python _bridge\\memory_governance.py validate"],
    },
    {
        "system": "records",
        "keywords": ("record", "记录", "索引", "归档", "sqlite", "resource library"),
        "owner_modules": [
            "_bridge/shared/record_store_maintenance.py",
        ],
        "owner_validators": ["python _bridge\\shared\\record_store_maintenance.py validate"],
    },
    {
        "system": "startup",
        "keywords": ("startup", "启动", "重启", "session store", "baseline", "config guard"),
        "owner_modules": ["_bridge/codex_config_guard.py", "_bridge/codex_session_store_doctor.py", "_bridge/codex_baseline_update.py"],
        "owner_validators": ["python _bridge\\codex_config_guard.py validate", "python _bridge\\codex_session_store_doctor.py validate"],
    },
    {
        "system": "hardware",
        "keywords": ("hardware", "device", "pnp", "usb", "硬件", "设备", "外设", "热插拔"),
        "owner_modules": [
            "_bridge/windows_hardware_owner.py",
            "_bridge/usb_device_owner.py",
            "_bridge/usb_device_control.py",
        ],
        "owner_validators": [
            "python _bridge\\windows_hardware_owner.py validate",
            "python _bridge\\usb_device_owner.py validate",
            "python _bridge\\usb_device_control.py validate",
        ],
    },
    {
        "system": "skills",
        "keywords": ("skill", "技能", "skill lifecycle", "skill router"),
        "owner_modules": ["_bridge/skill_lifecycle_governance.py", "_bridge/skill_orchestrator.py"],
        "owner_validators": ["python _bridge\\skill_lifecycle_governance.py doctor", "python _bridge\\skill_orchestrator.py validate"],
    },
    {
        "system": "drafts",
        "keywords": ("draft", "草案", "retained_reference", "pending_review", "artifact_ref"),
        "owner_modules": ["_bridge/draft_governance.py", "_bridge/workflow_review_queue.py"],
        "owner_validators": ["python _bridge\\draft_governance.py validate"],
    },
]


EVIDENCE_RULES: list[dict[str, Any]] = [
    {
        "key": "module_context",
        "when": "non-simple code, refactor, module boundary, owner placement, or reusable module lookup is involved",
        "triggers": ("code", "代码", "模块", "module", "refactor", "重构", "治理", "upgrade", "升级"),
        "policy_ref": "code_maintainability.py module-context/lookup-module/placement-plan",
        "route_source": "maintenance_surface_map.md code_maintainability row",
        "validation_goal": "owner module and placement decision are explicit before edits",
    },
    {
        "key": "codegraph",
        "when": "source structure, symbol flow, callers/callees, or blast-radius evidence is needed",
        "triggers": ("call", "caller", "callee", "impact", "symbol", "结构", "调用", "影响", "blast", "codegraph"),
        "policy_ref": "workflow_tools_contract.codegraph_policy",
        "route_source": "mcp_capability_matrix.md and CodeGraph current-turn policy",
        "validation_goal": "source-structure evidence is current or a same-boundary fallback reason is recorded",
    },
    {
        "key": "sqlite_state",
        "when": "queues, receipts, scheduler state, record indexes, inbox/outbox, .sqlite/.db, or database-backed status matters",
        "triggers": ("sqlite", "db", "queue", "status", "receipt", "状态", "队列", "回执", "索引", "数据库"),
        "policy_ref": "workflow_tools_contract.structured_state_policy",
        "route_source": "SQLite MCP/Hub plus owning business query surface",
        "validation_goal": "structured state evidence comes from read-only query or an explicit indexed-route miss",
    },
    {
        "key": "network_route",
        "when": "connectivity, proxy, DNS, slow download, external docs, package manager, browser, or API route behavior matters",
        "triggers": ("network", "proxy", "dns", "联网", "网络", "代理", "下载", "timeout", "slow", "package"),
        "policy_ref": "workflow_tools_contract.network_policy",
        "route_source": "codex_network_gateway.py and network_doctor.py surfaces",
        "validation_goal": "network route evidence is per-target and does not mutate global proxy/DNS by default",
    },
    {
        "key": "resource_layer",
        "when": "external resource acquisition, docs research, URL discovery/materialization, downloads, or package metadata is the task",
        "triggers": ("resource acquisition", "resource request", "资源获取", "资源委托", "联网", "搜索", "download", "docs", "github", "package", "下载"),
        "policy_ref": "workflow_tools_contract.external_docs_policy and resource acquisition surface",
        "route_source": "resource_broker.py/resource_cli.py plus owner MCP boundaries",
        "validation_goal": "resource request has a terminal receipt or documented ownership release",
    },
    {
        "key": "owner_maintenance",
        "when": "the changed system has an owning doctor, validate, metrics, repair-plan, or snapshot surface",
        "triggers": ("治理", "维护", "修复", "validate", "doctor", "repair", "升级", "优化"),
        "policy_ref": "maintenance_surface_map.md owning surface row",
        "route_source": "owner snapshot/doctor/repair-plan/validate where defined",
        "validation_goal": "the owning surface, not this planner, proves the change",
    },
    {
        "key": "system_membership",
        "when": "a system member, route, module, MCP, startup surface, or architecture contract changes",
        "triggers": ("member", "成员", "契约", "architecture", "架构", "mcp", "route", "模块"),
        "policy_ref": "system_membership.py impact/upgrade-plan",
        "route_source": "system membership contract",
        "validation_goal": "architecture/member synchronization obligations are visible at closeout",
    },
]


def detect_systems(message: str, target_system: str, targets: list[str]) -> list[dict[str, Any]]:
    if target_system:
        rules = [rule for rule in SYSTEM_RULES if rule["system"] == target_system]
        if not rules:
            return [{"system": target_system, "confidence": "explicit", "owner_modules": targets, "owner_validators": []}]
        return [{**rules[0], "confidence": "explicit"}]
    matched: list[dict[str, Any]] = []
    haystack = " ".join([message, *targets])
    for rule in SYSTEM_RULES:
        score = sum(1 for keyword in rule["keywords"] if keyword.lower() in haystack.lower())
        if score:
            matched.append({**rule, "confidence": "high" if score >= 2 else "medium", "match_score": score})
    if not matched and targets:
        validators = ["targeted readback"]
        if any(str(target).endswith(".py") for target in targets):
            validators.append("python -m py_compile <changed-files>")
        matched.append({"system": "targeted_surface", "confidence": "target_path", "owner_modules": targets, "owner_validators": validators})
    if not matched:
        matched.append({"system": "unknown", "confidence": "low", "owner_modules": [], "owner_validators": []})
    return sorted(matched, key=lambda item: (-int(item.get("match_score") or 0), str(item.get("system") or "")))[:4]


def evidence_chain(message: str, systems: list[dict[str, Any]], targets: list[str]) -> list[dict[str, Any]]:
    # Evidence classes should be selected from task semantics and identified
    # systems. Target paths are used later as candidate files; letting path
    # fragments such as "docs" trigger resource/web evidence creates noisy plans.
    haystack = " ".join([message, *[str(item.get("system") or "") for item in systems]])
    has_python_target = any(str(target).lower().endswith(".py") for target in targets)
    selected: list[dict[str, Any]] = []
    for rule in EVIDENCE_RULES:
        matched = [trigger for trigger in rule["triggers"] if trigger.lower() in haystack.lower()]
        default_selected = rule["key"] == "owner_maintenance" or (rule["key"] == "module_context" and has_python_target)
        if matched or default_selected:
            selected.append(
                {
                    "key": rule["key"],
                    "selected": bool(matched or default_selected),
                    "matched_triggers": matched,
                    "when": rule["when"],
                    "policy_ref": rule["policy_ref"],
                    "route_source": rule["route_source"],
                    "validation_goal": rule["validation_goal"],
                }
            )
    return selected


def target_files(systems: list[dict[str, Any]], explicit_targets: list[str]) -> list[str]:
    files: list[str] = [*explicit_targets]
    for system in systems:
        files.extend(str(item) for item in system.get("owner_modules", []) if str(item).strip())
    return uniq(files, 12)


def batch_plan(message: str, systems: list[dict[str, Any]], targets: list[str], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    owner_validators = uniq(
        [
            command
            for system in systems
            for command in system.get("owner_validators", [])
            if str(command).strip()
        ],
        12,
    )
    files = target_files(systems, targets)
    evidence_keys = [str(item.get("key") or "") for item in evidence if item.get("selected")]
    route_actions = [
        "run workflow_orchestrator.py plan for routing context if not already done",
        "run maintenance_upgrade_governance.py plan with explicit --target values when known",
    ]
    if "module_context" in evidence_keys:
        route_actions.append("run code_maintainability placement-plan only when code/module placement is involved")
    validation_actions = [*owner_validators]
    if any(path.lower().endswith(".py") for path in files):
        validation_actions.append("python -m py_compile <changed-files>")
    batches = [
        {
            "id": "batch_1_route_and_scope",
            "purpose": "Confirm owner system, candidate modules, boundaries, and what evidence is actually relevant for this task.",
            "read_only": True,
            "suggested_actions": route_actions,
            "evidence_keys": [key for key in ("module_context", "system_membership") if key in evidence_keys],
            "stop_conditions": ["unknown_owner_system_without_explicit_target", "task_requires_write_but_no_backup_or_approval"],
        },
        {
            "id": "batch_2_task_specific_evidence",
            "purpose": "Collect only the evidence classes triggered by the task, current environment, and affected system.",
            "read_only": True,
            "suggested_actions": [
                f"collect {item['key']} evidence via {item['policy_ref']} when: {item['when']}"
                for item in evidence
                if item.get("selected") and item["key"] not in {"module_context", "owner_maintenance"}
            ][:8],
            "evidence_keys": evidence_keys,
            "stop_conditions": ["required_evidence_route_unavailable_without_same_boundary_fallback"],
        },
        {
            "id": "batch_3_implementation_slice",
            "purpose": "Make one bounded semantic change in the owning module or purpose-owned peer module.",
            "read_only": False,
            "candidate_files": files,
            "suggested_actions": [
                "create a routed backup before edits",
                "preserve owner facade/CLI compatibility while moving independent lifecycle or state logic",
                "avoid creating a new module unless the boundary and validator owner are explicit",
            ],
            "stop_conditions": ["edit_target_conflicts_with_owner_route", "new_module_without_docstring_boundary_or_validator"],
        },
        {
            "id": "batch_4_owner_validation",
            "purpose": "Validate through the owning maintenance surface and smallest relevant regression checks.",
            "read_only": True,
            "suggested_actions": uniq(validation_actions, 10),
            "evidence_keys": ["owner_maintenance"],
            "stop_conditions": ["no_owner_validator_or_equivalent_readback"],
        },
        {
            "id": "batch_5_contract_and_closeout",
            "purpose": "Update discoverability contracts only if the change altered a member, route, module boundary, or maintenance surface.",
            "read_only": True,
            "suggested_actions": [
                "python _bridge\\system_membership.py impact --changed <changed-file>",
                "targeted maintenance_surface_map.md readback if a surface changed",
                "python _bridge\\codex_workflow_entry.py closeout --task-kind maintenance_upgrade_governance --outcome ok ...",
            ],
            "evidence_keys": ["system_membership"],
            "stop_conditions": ["architecture_change_without_membership_or_surface_update"],
        },
    ]
    # If the task is explicitly non-code and no target files are known, make the
    # implementation slice conditional so callers do not mistake planning for a write requirement.
    if not files:
        batches[2]["read_only"] = True
        batches[2]["purpose"] = "No implementation files were identified yet; first refine owner route or target system."
        batches[2]["suggested_actions"] = ["refine --target-system or --target before editing"]
    return batches


def plan(args: argparse.Namespace) -> dict[str, Any]:
    message = str(getattr(args, "message", "") or "")
    targets = uniq(list(getattr(args, "target", []) or []), 12)
    explicit_terms = [str(item).lower() for item in (getattr(args, "term", []) or []) if str(item).strip()]
    terms = text_terms(message, explicit_terms)
    systems = detect_systems(message, str(getattr(args, "target_system", "") or ""), targets)
    evidence = evidence_chain(message, systems, targets)
    batches = batch_plan(message, systems, targets, evidence)
    blockers: list[dict[str, Any]] = []
    if systems and systems[0].get("system") == "unknown":
        blockers.append(
            {
                "code": "owner_system_ambiguous",
                "message": "Refine --target-system, --target, or task wording before changing files.",
            }
        )
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "generated_at": now_iso(),
        "read_only": True,
        "message": message,
        "terms": terms,
        "target_system": str(getattr(args, "target_system", "") or ""),
        "targets": targets,
        "detected_systems": systems,
        "conditional_evidence_chain": evidence,
        "recommended_batches": batches,
        "principles": [
            "module systems exist to make governance, upgrades, validation, and reuse easier; do not split for splitting's sake",
            "select evidence by task and configured environment; tool-call order stays in existing route policies",
            "Codex owns judgment and exceptions; owning tools provide repeatable evidence and execution",
            "production state repairs must go through owner maintenance surfaces, not direct database writes",
        ],
        "blockers": blockers,
    }


def snapshot() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "known_systems": [item["system"] for item in SYSTEM_RULES],
        "evidence_rules": [{"key": item["key"], "when": item["when"]} for item in EVIDENCE_RULES],
        "entrypoints": ["plan", "snapshot", "validate"],
    }


def validate() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    code_args = argparse.Namespace(
        message="优化模块系统，让维护升级治理根据任务选择 CodeGraph SQLite owner validator 等证据",
        target_system="workflow",
        target=["_bridge/workflow_orchestrator.py"],
        term=[],
    )
    code_plan = plan(code_args)
    keys = [item.get("key") for item in code_plan.get("conditional_evidence_chain", [])]
    for key in ("module_context", "codegraph", "owner_maintenance", "system_membership"):
        if key not in keys:
            issues.append({"severity": "risk", "code": "code_upgrade_missing_evidence_key", "key": key})
    state_args = argparse.Namespace(
        message="检查邮件队列状态和回执",
        target_system="",
        target=[],
        term=[],
    )
    state_plan = plan(state_args)
    state_keys = [item.get("key") for item in state_plan.get("conditional_evidence_chain", [])]
    if "sqlite_state" not in state_keys:
        issues.append({"severity": "risk", "code": "state_task_missing_sqlite_evidence"})
    simple_args = argparse.Namespace(
        message="只修改文档措辞",
        target_system="",
        target=["_bridge/docs/maintenance_surface_map.md"],
        term=[],
    )
    simple_plan = plan(simple_args)
    simple_keys = [item.get("key") for item in simple_plan.get("conditional_evidence_chain", [])]
    if "sqlite_state" in simple_keys or "network_route" in simple_keys:
        issues.append({"severity": "risk", "code": "simple_doc_task_overselected_runtime_evidence", "keys": simple_keys})
    hardware_args = argparse.Namespace(
        message="优化 Windows PnP 和 USB 硬件诊断",
        target_system="hardware",
        target=["_bridge/windows_hardware_owner.py"],
        term=[],
    )
    hardware_plan = plan(hardware_args)
    hardware_validators = {
        str(command)
        for batch in hardware_plan.get("recommended_batches", [])
        if batch.get("id") == "batch_4_owner_validation"
        for command in batch.get("suggested_actions", [])
    }
    for command in (
        "python _bridge\\windows_hardware_owner.py validate",
        "python _bridge\\usb_device_owner.py validate",
        "python _bridge\\usb_device_control.py validate",
    ):
        if command not in hardware_validators:
            issues.append({"severity": "risk", "code": "hardware_owner_validator_missing", "command": command})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "read_only": True,
        "issues": issues,
        "probes": {
            "code_plan_ok": code_plan.get("ok"),
            "code_plan_evidence_keys": keys,
            "state_plan_evidence_keys": state_keys,
            "simple_plan_evidence_keys": simple_keys,
            "hardware_owner_validators": sorted(hardware_validators),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only maintenance upgrade governance planner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("validate")
    p = sub.add_parser("plan")
    p.add_argument("--message", required=True)
    p.add_argument("--target-system", default="")
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--term", action="append", default=[])
    return parser


def main() -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "validate":
        payload = validate()
    else:
        payload = plan(args)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
