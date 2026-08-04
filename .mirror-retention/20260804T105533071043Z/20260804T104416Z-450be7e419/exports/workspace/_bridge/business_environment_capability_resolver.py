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
from typing import Any, Callable, Mapping

import mcp_capability_routes
import rule_governance
import semantic_capability_owner
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
    {"id": "software_engineering", "terms": ("代码", "审查", "review", "code", "测试", "模块", "module", "python", "回归", "regression"), "owner": "codex_code_review_judgment"},
    {"id": "textbook_knowledge", "terms": ("医学教材", "教材概念", "教材知识包", "medical textbook", "textbook concept"), "owner": "skill:medical-textbook-study"},
    {"id": "document_processing", "terms": ("扫描pdf", "扫描 pdf", "扫描件", "ocr", "pdf", "光学字符识别"), "owner": "pdf_owner"},
    {"id": "semantic_retrieval", "terms": ("语义检索", "语义搜索", "向量检索", "向量搜索", "embedding", "bge", "bge-m3", "重排", "概念归一", "semantic retrieval", "vector retrieval", "reranking"), "owner": "semantic_capability_owner"},
    {"id": "system_maintenance", "terms": ("维护", "诊断", "修复", "maintenance", "governance"), "owner": "maintenance_upgrade_governance"},
    {"id": "external_research", "terms": ("联网", "搜索", "研究", "资料", "research", "docs"), "owner": "resource_broker"},
    {"id": "content_production", "terms": ("内容", "文章", "演示文稿", "课件", "content", "presentation"), "owner": "selected_domain_skill"},
    {"id": "cross_system_automation", "terms": ("跨系统", "windows", "wsl", "自动化", "automation", "bridge"), "owner": "windows_execution_agent"},
)


def _default_semantic_selector(query: str, candidates: list[dict[str, Any]]) -> Mapping[str, Any]:
    """Use the existing owner with a short routing deadline and no persistence."""

    return semantic_capability_owner.select(query, candidates, timeout=2.0)


def _category(message: str) -> dict[str, Any]:
    text = str(message or "").casefold()
    tokens = set(re.findall(r"[a-z0-9_.-]+", text))

    def matches(term: str) -> bool:
        value = term.casefold()
        return value in text if any(ord(character) > 127 for character in value) else value in tokens

    # An explicitly requested external source keeps the resource owner even
    # when its subject happens to be a semantic model.  Conversely, local
    # textbook/PDF and generic semantic work must not leak into maintenance
    # merely because no lifecycle mutation is present.
    external = SCENARIOS[5]
    if any(matches(term) for term in external["terms"]):
        return dict(external)
    for category in SCENARIOS[1:4]:
        if any(matches(term) for term in category["terms"]):
            return dict(category)

    # Repair is not itself a code signal: keep generic maintenance in its
    # existing owner lane.  A concrete source cue, however, must not fall
    # through to that generic lane merely because the workflow classifier is
    # intentionally conservative.
    chinese_or_file_signal = any(term in text for term in ("代码", "源码", "模块", "函数", "python", ".py"))
    english_source_signal = bool(tokens.intersection({"code", "source", "module", "function"}))
    if chinese_or_file_signal or english_source_signal:
        return dict(SCENARIOS[0])

    ranked = [(sum(matches(term) for term in row["terms"]), index, row) for index, row in enumerate(SCENARIOS)]
    score, _, selected = max(ranked, key=lambda item: (item[0], -item[1]))
    return dict(selected if score else SCENARIOS[1])


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _message_terms(message: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[\w.\-]+", str(message or "").casefold())))[:16]


def _bundle_assets(kind: str, role: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets = []
    for value in values:
        name = str(value.get("name") or value.get("system") or "").strip()
        if not name:
            continue
        assets.append({
            "kind": kind,
            "name": name,
            "role": role,
            "authority_ref": str(value.get("path") or value.get("source") or f"{kind}:{name}"),
            "selection_reason": value.get("reason", "selected by existing authority"),
            "expand_ref": str(value.get("path") or value.get("action") or f"{kind}:{name}"),
        })
    return assets


def _domain_bundle_assets(projection: dict[str, Any] | None) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for value in (projection or {}).get("assets", []):
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "").strip()
        authority_ref = str(value.get("authority_ref") or "").strip()
        if not name or not authority_ref:
            continue
        assets.append({
            "kind": "domain_asset",
            "name": name,
            "role": str(value.get("role") or "domain_support"),
            "authority_ref": authority_ref,
            "selection_reason": "selected by the existing domain authority",
            "expand_ref": str(value.get("action_ref") or authority_ref),
            "acceptance_ref": str(value.get("acceptance_ref") or ""),
        })
    return assets


def build_capability_bundle(
    message: str,
    *,
    workflow_plan: dict[str, Any],
    route_decision: dict[str, Any],
    rules: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    domain_projection: dict[str, Any] | None = None,
    environment_decision_frame: dict[str, Any] | None = None,
    capability_view: dict[str, Any] | None = None,
    simple_fast_path: bool = False,
    semantic_probe_result: Mapping[str, Any] | None = None,
    semantic_selector: Callable[[str, list[dict[str, Any]]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a bounded, non-authoritative admission projection.

    It only composes route-owned facts already selected for this task.  It does
    not query or persist any asset authority, which keeps simple tasks quiet and
    avoids turning this module into a second catalog.
    """

    if simple_fast_path:
        return {"schema": "business_environment.capability_bundle.v1", "simple_fast_path": True, "assets": []}

    category = _category(message)
    decision_frame = dict(environment_decision_frame or {})
    domains = [str(row.get("key") or "") for row in workflow_plan.get("domains", []) if isinstance(row, dict)]
    selected_skills = [str(row.get("name") or "") for row in skills if str(row.get("name") or "")]
    explicit_owner = str(route_decision.get("primary_owner") or "").strip()
    structured_resource_owner = bool(
        route_decision.get("resource_delegation_required")
        and str(route_decision.get("primary_domain") or "") == "external_docs_research"
    )
    if explicit_owner and explicit_owner != "general":
        business_owner = explicit_owner
        owner_authority_ref = "workflow.route_decision.primary_owner"
        owner_selection_reason = "explicit route owner"
    elif structured_resource_owner:
        business_owner = "resource_broker"
        owner_authority_ref = "workflow.route_decision.resource_delegation_required"
        owner_selection_reason = "structured external research fast path"
    else:
        business_owner = str(category["owner"])
        owner_authority_ref = "business_environment_capability_resolver.category"
        owner_selection_reason = f"bounded automatic fallback:business category:{category['id']}"
    if business_owner == "selected_domain_skill" and selected_skills:
        business_owner = f"skill:{selected_skills[0]}"
    primary_owner = {
        "name": business_owner,
        "authority_ref": owner_authority_ref,
        "selection_reason": owner_selection_reason,
        "handoff_condition": "supporting assets cannot replace the primary owner",
    }
    domain_assets = _domain_bundle_assets(domain_projection)
    # Domain references are promoted ahead of generic support so the compact
    # route remains actionable.  The existing selected skill stays visible,
    # then spare capacity retains generic owner/tool support.
    assets = (
        _bundle_assets("rule", "constraint", rules)[:3]
        + domain_assets[:4]
        + _bundle_assets("skill", "execution", skills)[:1]
        + _bundle_assets("owner", "support", owners)
        + _bundle_assets("tool", "support", tools)
    )[:8]
    result_contract = {
        "consumer": "business caller through existing workflow lifecycle",
        "acceptance_predicate": "owner result is accepted and consumed by the business caller",
        "consume_ref": "codex_workflow_entry.run/status/wait/consume/attach-result",
    }
    automatic_owner = bool(explicit_owner or structured_resource_owner or business_owner.startswith("skill:"))
    frame_mode = str(decision_frame.get("post_gate_mode") or decision_frame.get("decision_mode") or "")
    status = "ready" if business_owner else "insufficient_discovery"
    if decision_frame.get("decision_mode") == "insufficient_discovery":
        status = "insufficient_discovery"
    elif frame_mode == "judgment_required" and not automatic_owner:
        status = "judgment_required"
    elif frame_mode == "fast_recommendation" and not automatic_owner:
        status = "fast_recommendation"
    primary_owner["decision_role"] = (
        "selected_owner" if automatic_owner else "recommendation_only"
    )
    primary_owner["supporting_evidence_refs"] = [
        str(item.get("authority_ref") or "")
        for item in decision_frame.get("candidates", [])
        if isinstance(item, dict) and item.get("authority_ref")
    ][:2]
    decision_receipt: dict[str, Any] = {}
    if capability_view and not simple_fast_path:
        from capability_decision_session import decide_from_environment_view

        view_candidates = [item for item in capability_view.get("candidates", []) if isinstance(item, dict)]
        semantic_exposed = any(
            str(item.get("capability_class") or item.get("kind") or "") == "semantic_model"
            or str(item.get("candidate_id") or item.get("capability_id") or "") == "model:bge-m3"
            for item in view_candidates
        )
        selected_semantic_selector = semantic_selector
        if selected_semantic_selector is None and semantic_exposed:
            selected_semantic_selector = _default_semantic_selector

        decision_receipt = decide_from_environment_view(
            task_contract_ref=str(route_decision.get("task_contract_ref") or "business_environment.capability_bundle"),
            primary_owner=primary_owner,
            environment_view=capability_view,
            acceptance=result_contract,
            required_gates=[row for row in route_decision.get("required_gates", []) if isinstance(row, dict)],
            permission_ref=str(route_decision.get("permission_ref") or ""),
            semantic_query=message,
            semantic_probe_result=semantic_probe_result,
            semantic_selector=selected_semantic_selector,
        )
        selected_assets = [
            {
                "kind": str(item.get("kind") or "support"),
                "name": str(item.get("candidate_id") or ""),
                "role": "selected_support",
                "authority_ref": str(item.get("authority_ref") or ""),
                "selection_reason": str(decision_receipt.get("ranking", {}).get("selection_reason") or "capability decision receipt"),
                "expand_ref": str(item.get("entry_ref") or item.get("source_ref") or ""),
                "fallback_ref": str(item.get("fallback_ref") or ""),
                "decision_receipt_ref": str(decision_receipt.get("receipt_signature") or ""),
            }
            for item in decision_receipt.get("selected_supporting_assets", [])
            if isinstance(item, dict) and item.get("candidate_id") and item.get("authority_ref")
        ]
        if selected_assets:
            selected_ids = {item["name"] for item in selected_assets}
            assets = (selected_assets + [item for item in assets if item.get("name") not in selected_ids])[:8]
    payload = {
        "schema": "business_environment.capability_bundle.v1",
        "simple_fast_path": False,
        "intent": {
            "task_mode": str(route_decision.get("task_mode") or "general"),
            "primary_domain": str(route_decision.get("primary_domain") or category["id"]),
            "supporting_domains": [domain for domain in domains if domain not in {"general", str(route_decision.get("primary_domain") or "")}][:2],
        },
        "primary_owner": primary_owner,
        "assets": assets,
        "gates": [row for row in route_decision.get("required_gates", []) if isinstance(row, dict)][:4],
        "permission_boundaries": [str(row.get("name") or "") for row in rules if str(row.get("name") or "")][:3],
        "fallback_chain": ["return insufficient_discovery when the primary owner is unavailable", "use only configured forward fallback for a selected supporting asset"],
        "result_contract": result_contract,
        "domain_projection": {
            "schema": str((domain_projection or {}).get("schema") or ""),
            "scenario": str((domain_projection or {}).get("scenario") or ""),
            "acceptance": dict((domain_projection or {}).get("acceptance") or {}),
            "excluded_support": list((domain_projection or {}).get("excluded_support") or [])[:2],
        },
        "environment_decision_frame": {
            "schema": str(decision_frame.get("schema") or ""),
            "decision_mode": str(decision_frame.get("decision_mode") or ""),
            "post_gate_mode": str(decision_frame.get("post_gate_mode") or ""),
            "candidate_refs": [str(item.get("authority_ref") or "") for item in decision_frame.get("candidates", []) if isinstance(item, dict)][:3],
            "judgment_rule": str(decision_frame.get("judgment_rule") or ""),
        },
        "capability_view": {
            "schema": str((capability_view or {}).get("schema") or ""),
            "source_signature": str((capability_view or {}).get("source_signature") or ""),
            "decision_mode": str((capability_view or {}).get("decision_mode") or ""),
            "primary_owner": str((capability_view or {}).get("primary_owner") or ""),
            "candidates": list((capability_view or {}).get("candidates") or [])[:6],
            "issues": list((capability_view or {}).get("issues") or [])[:4],
            "next_action": str((capability_view or {}).get("next_action") or ""),
        },
        "capability_decision_receipt": decision_receipt,
        "status": status,
        "required_decision": (
            "Codex selects a candidate or follows its minimal probe before execution"
            if status == "judgment_required"
            else "Codex may accept or override this signed recommendation before execution"
            if status == "fast_recommendation"
            else ""
        ),
        "expand": [asset["expand_ref"] for asset in assets if asset.get("expand_ref")][:8],
    }
    payload["evidence_signature"] = _stable_hash({"message": message, "intent": payload["intent"], "primary_owner": primary_owner, "assets": assets, "gates": payload["gates"], "domain_projection": payload["domain_projection"], "environment_decision_frame": payload["environment_decision_frame"], "capability_view": payload["capability_view"], "capability_decision_receipt": decision_receipt})
    return payload


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


def _activation_assets(
    workflow_plan: dict[str, Any],
    skill_plan: dict[str, Any],
    quality: dict[str, Any],
    acceptance_results: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected = {str(item.get("name") or "") for item in skill_plan.get("selected_skills", []) if isinstance(item, dict)}
    assets = []
    for case in tool_utilization_audit.declared_activation_cases():
        skill = str(case.get("usage_skill") or case.get("expect_skill") or "")
        usage = quality.get("skills", {}).get(skill, {}) if skill else {}
        applied = int(usage.get("applied") or 0)
        completed = int(usage.get("completed") or 0)
        minimum = int(case.get("minimum_applied") or 0)
        acceptance = (acceptance_results or {}).get(str(case.get("id") or ""))
        if acceptance is None:
            acceptance = tool_utilization_audit.evaluate_activation_case(
                workflow_plan,
                skill_plan,
                {"skills": quality.get("skills", {})},
                case,
            )
        accepted = bool(acceptance.get("ok"))
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
                "authority_ref": "tool_utilization_audit.evaluate_activation_case + skill_lifecycle_state.quality_summary",
                "blocker": "" if accepted else "activation acceptance probe, route, or consumed-use evidence is incomplete",
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
    activation_acceptance = tool_utilization_audit.activation_acceptance_with_build_plan(
        workflow_orchestrator.build_plan,
        usage_summary={"skills": quality_result.get("skills", {})},
    )
    acceptance_results = {
        str(item.get("id") or ""): item
        for item in activation_acceptance.get("results", [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    assets = _member_assets(membership, maintenance) + _activation_assets(
        workflow_plan,
        skill_plan,
        quality_result,
        acceptance_results,
    )
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
