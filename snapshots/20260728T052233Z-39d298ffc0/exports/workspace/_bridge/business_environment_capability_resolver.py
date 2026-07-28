#!/usr/bin/env python3
"""Read-only business information and active-asset utilization projection.

Ownership: bounded composition of existing routing, membership, maintenance,
skill, MCP, scheduler, rule, and utilization owner results.
Non-goals: a second asset catalog, owner execution, lifecycle mutation, usage
recording, permission changes, scheduling, or retirement decisions.
State behavior: read-only; every projected fact keeps an authority reference.
Caller context: business_environment_control_plane and its workflow facade.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import mcp_capability_routes
import rule_governance
import skill_lifecycle_state
import skill_orchestrator
import system_membership
import tool_utilization_audit
import workflow_orchestrator
from maintenance_capability_registry import global_coverage
from shared import codex_scheduler_runner
from shared.json_cli import now_iso


SCHEMA = "business_environment_capability_resolver.v1"
REVIEW_BY = "within_30_days_of_detection"
ASSET_FIELDS = (
    "asset_id", "asset_kind", "owner", "representative_scenario", "trigger",
    "consumer", "health", "utilization_acceptance", "exit_strategy", "lifecycle",
)
SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "software_engineering", "terms": ("代码", "审查", "review", "code", "测试"), "owner": "codex_code_review_judgment"},
    {"id": "system_maintenance", "terms": ("维护", "诊断", "修复", "maintenance", "governance"), "owner": "maintenance_upgrade_governance"},
    {"id": "external_research", "terms": ("联网", "搜索", "研究", "资料", "research", "docs"), "owner": "resource_broker"},
    {"id": "content_production", "terms": ("内容", "文章", "演示文稿", "课件", "content", "presentation"), "owner": "selected_domain_skill"},
    {"id": "cross_system_automation", "terms": ("跨系统", "windows", "wsl", "自动化", "automation", "bridge"), "owner": "windows_execution_agent"},
)


def _category(message: str) -> dict[str, Any]:
    text = str(message or "").casefold()
    tokens = set(re.findall(r"[a-z0-9_.-]+", text))

    def matches(term: str) -> bool:
        value = term.casefold()
        return value in text if any(ord(character) > 127 for character in value) else value in tokens

    ranked = [(sum(matches(term) for term in row["terms"]), index, row) for index, row in enumerate(SCENARIOS)]
    score, _, selected = max(ranked, key=lambda item: (item[0], -item[1]))
    return dict(selected if score else SCENARIOS[1])


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _message_terms(message: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\w.\-]+", str(message or "").casefold())))[:16]


def _health_refs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    commands = contract.get("health_commands") if isinstance(contract.get("health_commands"), list) else []
    return [
        {"owner": str(row.get("owner") or row.get("name") or ""), "command_ref": " ".join(str(item) for item in row.get("args", []))}
        for row in commands if isinstance(row, dict)
    ]


def _member_assets(membership: dict[str, Any], maintenance: dict[str, Any]) -> list[dict[str, Any]]:
    contracts = membership.get("contracts") if isinstance(membership.get("contracts"), dict) else {}
    dispositions = {
        str(row.get("member_id") or ""): str(row.get("disposition") or "")
        for row in maintenance.get("member_dispositions", []) if isinstance(row, dict)
    }
    members = membership.get("mirror_source_projection", {}).get("members", [])
    assets = []
    for member in members if isinstance(members, list) else []:
        if not isinstance(member, dict) or str(member.get("lifecycle") or "active") != "active":
            continue
        member_id = str(member.get("member_id") or "")
        system = str(member.get("system") or "")
        health = _health_refs(contracts.get(system, {}))
        disposition = dispositions.get(member_id, "unmapped")
        accepted = disposition != "unmapped" and bool(member.get("owner"))
        assets.append({
            "asset_id": member_id,
            "asset_kind": str(member.get("kind") or "member"),
            "owner": str(member.get("owner") or ""),
            "representative_scenario": f"{system} business task routed through {member_id}",
            "trigger": {"system": system, "member_kind": str(member.get("kind") or "")},
            "consumer": "business_environment_control_plane -> domain owner",
            "health": {"state": "owner_checks_declared" if health else "owner_check_reference_missing", "refs": health},
            "utilization_acceptance": {
                "state": "owner_routable" if accepted else "installed_only",
                "maintenance_disposition": disposition,
                "authority_ref": "maintenance_capability_registry.coverage.member_dispositions",
                "blocker": "" if accepted else "maintenance disposition is missing",
                "review_by": "" if accepted else REVIEW_BY,
            },
            "exit_strategy": {
                "owner_ref": "system_membership.retirement-plan",
                "command_ref": f"python _bridge/system_membership.py retirement-plan --system {system} --member {member_id}",
                "auto_retire": False,
            },
            "lifecycle": "active-effective" if accepted else "blocked-unclassified",
        })
    return assets


def _activation_assets(skill_plan: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    selected = {str(item.get("name") or "") for item in skill_plan.get("selected_skills", []) if isinstance(item, dict)}
    assets = []
    for case in tool_utilization_audit.declared_activation_cases():
        skill = str(case.get("usage_skill") or case.get("expect_skill") or "")
        usage = quality.get("skills", {}).get(skill, {}) if skill else {}
        applied = int(usage.get("applied") or 0)
        completed = int(usage.get("completed") or 0)
        minimum = int(case.get("minimum_applied") or 0)
        accepted = bool(case.get("runtime_evidence", {}).get("owner") and case.get("result_consumption"))
        accepted = accepted and (not minimum or (applied >= minimum and completed >= minimum))
        assets.append({
            "asset_id": str(case.get("capability_id") or case.get("id") or ""),
            "asset_kind": "activation_capability",
            "owner": str(case.get("runtime_evidence", {}).get("owner") or ""),
            "representative_scenario": str(case.get("message") or ""),
            "trigger": {"workflow_domain": str(case.get("expect_domain") or ""), "skill": str(case.get("expect_skill") or "")},
            "consumer": str(case.get("result_consumption") or ""),
            "health": {"state": "declared", "validation_ref": str(case.get("runtime_evidence", {}).get("validation") or "")},
            "utilization_acceptance": {
                "state": "observed_consumed" if accepted else "installed_only",
                "selected_now": skill in selected if skill else False,
                "applied": applied,
                "completed": completed,
                "minimum_applied": minimum,
                "authority_ref": "tool_utilization_audit + skill_lifecycle_state.quality_summary",
                "blocker": "" if accepted else "representative use lacks consumed completion evidence",
                "review_by": "" if accepted else REVIEW_BY,
            },
            "exit_strategy": {"owner_ref": "external dependency or system membership owner", "auto_retire": False},
            "lifecycle": "active-effective" if accepted else "active-underused",
        })
    return assets


def build_information_package(
    message: str,
    *,
    workflow_plan: dict[str, Any],
    skill_plan: dict[str, Any],
    membership: dict[str, Any],
    maintenance: dict[str, Any],
    mcp: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    scheduler: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    category = _category(message)
    quality_result = quality if quality is not None else skill_lifecycle_state.quality_summary(limit=500)
    mcp_result = mcp if mcp is not None else mcp_capability_routes.lookup(_message_terms(message))
    scheduler_result = scheduler if scheduler is not None else codex_scheduler_runner.task_drift_snapshot()
    rules_result = rules if rules is not None else rule_governance.snapshot(full=False)
    assets = _member_assets(membership, maintenance) + _activation_assets(skill_plan, quality_result)
    selected_skills = [str(item.get("name") or "") for item in skill_plan.get("selected_skills", []) if isinstance(item, dict)]
    business_owner = str(category["owner"])
    if business_owner == "selected_domain_skill":
        business_owner = f"skill:{selected_skills[0]}" if selected_skills else ""
    selected_unused = sorted(
        name for name, row in quality_result.get("skills", {}).items()
        if int(row.get("selected") or 0) > int(row.get("applied") or 0)
    )
    used_unconsumed = sorted(
        name for name, row in quality_result.get("skills", {}).items()
        if int(row.get("applied") or 0) > int(row.get("completed") or 0)
    )
    installed_only = [row for row in assets if row["utilization_acceptance"]["state"] == "installed_only"]
    matches = mcp_result.get("matches") if isinstance(mcp_result.get("matches"), list) else []
    supporting_tools = [
        {
            "capability": str(row.get("capability") or ""),
            "owner_profile": str(row.get("owner_profile") or ""),
            "permission_boundary": str(row.get("permission_boundary") or ""),
            "role": "supporting_tool_not_business_owner",
        }
        for row in matches[:2] if isinstance(row, dict)
    ]
    route_decision = workflow_plan.get("execution_route_pack", {}).get("route_decision", {})
    gates = route_decision.get("required_gates") if isinstance(route_decision.get("required_gates"), list) else []
    permission_boundaries = [
        {"source": "workflow.route_decision.required_gates", "owner": str(row.get("owner") or ""), "fact": str(row.get("fact") or "")}
        for row in gates if isinstance(row, dict)
    ] + [
        {"source": "mcp_capability_routes.lookup", "boundary": row["permission_boundary"]}
        for row in supporting_tools if row["permission_boundary"]
    ]
    if not permission_boundaries:
        permission_boundaries = [{"source": "global_task_contract", "boundary": "no_permission_expansion"}]
    evidence = {
        "membership": str(membership.get("schema") or ""), "maintenance": str(maintenance.get("schema") or ""),
        "skill_usage": str(quality_result.get("schema") or ""), "mcp": str(mcp_result.get("schema") or ""),
        "scheduler": str(scheduler_result.get("schema") or ""), "rules": str(rules_result.get("schema") or ""),
    }
    evidence_freshness = {
        "state": "current_projection", "authority_schemas": evidence,
        "usage_last_recorded_at": str(quality_result.get("last_recorded_at") or ""),
        "scheduler_current": scheduler_result.get("ok") is True,
        "rule_activation_current": rules_result.get("activation", {}).get("coverage_complete") is True,
    }
    complete = [all(field in row and row[field] not in (None, "") for field in ASSET_FIELDS) for row in assets]
    utilization_gaps = [
        {"kind": "selected_but_unused", "asset": name, "owner_ref": "skill_lifecycle_state.quality_summary", "review_by": REVIEW_BY}
        for name in selected_unused
    ] + [
        {"kind": "used_but_unconsumed", "asset": name, "owner_ref": "skill_lifecycle_state.quality_summary", "review_by": REVIEW_BY}
        for name in used_unconsumed
    ] + [
        {"kind": "installed_only", "asset": row["asset_id"], "owner_ref": row["utilization_acceptance"]["authority_ref"], "review_by": row["utilization_acceptance"]["review_by"]}
        for row in installed_only
    ]
    return {
        "schema": f"{SCHEMA}.package",
        "ok": bool(assets) and all(complete) and maintenance.get("ok") is True,
        "generated_at": now_iso(),
        "read_only": True,
        "writes_authoritative_state": False,
        "business_category": category["id"],
        "completion_predicate": "domain owner result is accepted and consumed by the business caller",
        "minimal_capability_combination": {
            "business_owner": business_owner, "skills": selected_skills[:2], "supporting_tools": supporting_tools,
            "rule": "supporting skills and tools cannot replace the business or runtime owner",
        },
        "permission_boundaries": permission_boundaries,
        "evidence_freshness": evidence_freshness,
        "consumer": "business caller through codex_workflow_entry business-environment context",
        "critical_path": ["intent", "business_owner", "supporting_capabilities", "owner_result", "result_consumption"],
        "asset_summary": {
            "active_asset_count": len(assets),
            "field_coverage_percent": round(sum(complete) / len(complete) * 100.0, 2) if complete else 0.0,
            "installed_only_count": len(installed_only),
            "installed_only_blocked_with_deadline": all(row["utilization_acceptance"].get("blocker") and row["utilization_acceptance"].get("review_by") for row in installed_only),
            "selected_but_unused": selected_unused,
            "used_but_unconsumed": used_unconsumed,
            "utilization_gap_count": len(utilization_gaps),
            "utilization_gaps_actionable": all(row.get("owner_ref") and row.get("review_by") for row in utilization_gaps),
        },
        "utilization_gaps": utilization_gaps[:8],
        "assets": assets[:8],
        "asset_detail_ref": "command:python _bridge/business_environment_control_plane.py assets --message <task>",
        "evidence_signature": _stable_hash({
            "message": message,
            "business_category": category["id"],
            "business_owner": business_owner,
            "selected_skills": selected_skills,
            "supporting_tools": supporting_tools,
            "permission_boundaries": permission_boundaries,
            "evidence_freshness": evidence_freshness,
            "assets": assets,
            "utilization_gaps": utilization_gaps,
        }),
        "_all_assets": assets,
    }


def collect_information_package(message: str) -> dict[str, Any]:
    context = skill_orchestrator.prepare_readonly_routing_context()
    workflow = workflow_orchestrator.build_plan(message, detail="micro", skill_routing_context=context)
    skills = skill_orchestrator.build_plan(message, routing_context=context)
    membership = system_membership.snapshot()
    return build_information_package(
        message,
        workflow_plan=workflow,
        skill_plan=skills,
        membership=membership,
        maintenance=global_coverage(),
    )
