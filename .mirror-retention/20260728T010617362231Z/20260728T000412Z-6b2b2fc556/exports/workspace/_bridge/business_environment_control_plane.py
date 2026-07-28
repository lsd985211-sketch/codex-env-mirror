#!/usr/bin/env python3
"""Read-only shadow control plane for cross-domain business environment work.

Ownership: deterministic aggregation of existing workflow, skill, placement,
membership, and maintenance authority results into one business-facing view.
Non-goals: owner execution, a second asset catalog, business-state writes,
permission changes, scheduling, lifecycle mutation, or authority transfer.
State behavior: read-only and shadow-only; emits no persistent state.
Caller context: Codex workflow facade and focused architecture validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import business_environment_capability_resolver
import code_maintainability
import maintenance_capability_registry
import skill_orchestrator
import system_membership
import workflow_orchestrator
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


SCHEMA = "business_environment_control_plane.v1"
SCHEMA_CATALOG = {
    "business_intent": "business_environment.business_intent.v1",
    "capability": "business_environment.capability_ref.v1",
    "asset": "business_environment.asset_ref.v1",
    "authority_binding": "business_environment.authority_binding_ref.v1",
    "execution_graph": "business_environment.execution_graph_ref.v1",
    "durable_operation": "business_environment.durable_operation_ref.v1",
    "lifecycle_record": "business_environment.lifecycle_projection.v1",
    "business_outcome": "business_environment.business_outcome_ref.v1",
}


def _stable_projection(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _signature(message: str, authorities: dict[str, dict[str, Any]]) -> str:
    stable = {
        "message": str(message or "").strip(),
        "authorities": {
            name: _stable_projection(payload, ("schema", "ok", "domains", "selected_skills", "owner_module", "recommended_placement", "systems", "coverage_percent", "active_member_count", "unmapped_members"))
            for name, payload in sorted(authorities.items())
        },
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _placement(message: str) -> dict[str, Any]:
    del message
    args = argparse.Namespace(
        root=None, all_bridge=False, include_excluded=False,
        term=["business", "environment", "control", "governance"],
        task_mode="code",
        message="建立面向整个 Codex 工作环境的全局 business environment control plane",
        target="", limit=8, issue_limit=20,
        large_file_lines=1200, large_function_lines=160,
        large_function_decisions=30, json=False,
    )
    return code_maintainability.placement_plan(args)


def _classify_members(membership: dict[str, Any]) -> list[dict[str, Any]]:
    projection = membership.get("mirror_source_projection") if isinstance(membership.get("mirror_source_projection"), dict) else {}
    members = projection.get("members") if isinstance(projection.get("members"), list) else []
    rows: list[dict[str, Any]] = []
    for raw in members:
        if not isinstance(raw, dict) or str(raw.get("lifecycle") or "active") != "active":
            continue
        member_id = str(raw.get("member_id") or "")
        system = str(raw.get("system") or "")
        owner = str(raw.get("owner") or "")
        classification = "active-effective" if member_id and system and owner else "blocked-unclassified"
        rows.append({
            "schema": SCHEMA_CATALOG["lifecycle_record"],
            "member_id": member_id,
            "system": system,
            "owner": owner,
            "classification": classification,
            "authority_ref": "system_membership.snapshot.mirror_source_projection",
        })
    return sorted(rows, key=lambda item: (item["system"], item["member_id"]))


def build_plan(
    message: str,
    *,
    workflow_plan: dict[str, Any] | None = None,
    skill_plan: dict[str, Any] | None = None,
    placement: dict[str, Any] | None = None,
    membership: dict[str, Any] | None = None,
    maintenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic shadow decision from existing authorities."""

    routing_context = skill_orchestrator.prepare_readonly_routing_context()
    workflow = workflow_plan if workflow_plan is not None else workflow_orchestrator.build_plan(
        message,
        detail="micro",
        skill_routing_context=routing_context,
    )
    skills = skill_plan if skill_plan is not None else skill_orchestrator.build_plan(
        message,
        routing_context=routing_context,
    )
    placement_result = placement if placement is not None else _placement(message)
    membership_result = membership if membership is not None else system_membership.snapshot()
    maintenance_result = maintenance if maintenance is not None else maintenance_capability_registry.global_coverage()
    authorities = {
        "workflow": workflow,
        "skills": skills,
        "placement": placement_result,
        "membership": membership_result,
        "maintenance": maintenance_result,
    }
    information_package = business_environment_capability_resolver.build_information_package(
        message,
        workflow_plan=workflow,
        skill_plan=skills,
        membership=membership_result,
        maintenance=maintenance_result,
    )
    workflow_domains = [str(item.get("key") or "") for item in workflow.get("domains", []) if isinstance(item, dict)]
    skill_domains = [str(item.get("key") or "") for item in skills.get("domains", []) if isinstance(item, dict)]
    selected_skills = [str(item.get("name") or "") for item in skills.get("selected_skills", []) if isinstance(item, dict)]
    owner_module = str(placement_result.get("owner_module") or "")
    conflicts: list[dict[str, Any]] = []
    if workflow_domains and skill_domains and not set(workflow_domains) & set(skill_domains):
        conflicts.append({"code": "workflow_skill_domain_conflict", "workflow": workflow_domains, "skills": skill_domains})
    blockers: list[dict[str, Any]] = []
    if not workflow.get("ok"):
        blockers.append({"code": "workflow_authority_unavailable", "authority_ref": "workflow_orchestrator.plan"})
    if not skills.get("ok"):
        blockers.append({"code": "skill_authority_unavailable", "authority_ref": "skill_orchestrator.plan"})
    elif not selected_skills and information_package.get("business_category") in {
        "software_engineering",
        "external_research",
        "content_production",
    }:
        blockers.append({"code": "required_skill_selection_empty", "authority_ref": "skill_orchestrator.plan"})
    if not placement_result.get("ok") or not owner_module:
        blockers.append({"code": "placement_unresolved", "authority_ref": "code_maintainability.placement_plan"})
    if not maintenance_result.get("ok"):
        blockers.append({"code": "maintenance_coverage_incomplete", "authority_ref": "maintenance_capability_registry.coverage"})
    asset_lifecycle = _classify_members(membership_result)
    blocked_assets = [item for item in asset_lifecycle if item["classification"] == "blocked-unclassified"]
    blockers.extend({"code": "active_member_unclassified", "member_id": item["member_id"]} for item in blocked_assets)
    information_package["route_blockers"] = list(blockers)
    information_package["authority_conflicts"] = list(conflicts)
    information_package["ok"] = bool(information_package.get("ok")) and not blockers and not conflicts
    terminal_projection = json.dumps(
        {"blockers": blockers, "authority_conflicts": conflicts},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hashlib.sha256(
        f"{_signature(message, authorities)}:{information_package['evidence_signature']}:{terminal_projection}".encode("utf-8")
    ).hexdigest()
    decision = {
        "classification": workflow_domains or ["blocked-unclassified"],
        "selected_capabilities": [
            {"schema": SCHEMA_CATALOG["capability"], "kind": "skill", "ref": name}
            for name in selected_skills
        ] + ([{"schema": SCHEMA_CATALOG["capability"], "kind": "owner_module", "ref": owner_module}] if owner_module else []),
        "authority_conflict_count": len(conflicts),
        "blocker_count": len(blockers),
        "next_safe_action": "consume_shadow_plan_without_mutation" if not blockers else "resolve_reported_authority_blockers",
    }
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers and not conflicts,
        "generated_at": now_iso(),
        "read_only": True,
        "shadow_only": True,
        "writes_authoritative_state": False,
        "input_signature": signature,
        "schemas": SCHEMA_CATALOG,
        "business_intent": {
            "schema": SCHEMA_CATALOG["business_intent"],
            "message": str(message or "").strip(),
            "completion_predicate": "caller_consumes_owner_results_and_business_outcome",
            "write_scope": "none_in_shadow",
        },
        "authority_bindings": [
            {
                "schema": SCHEMA_CATALOG["authority_binding"],
                "fact": name,
                "owner_ref": str(payload.get("schema") or name),
                "write_authority_transferred": False,
            }
            for name, payload in authorities.items()
        ],
        "decision": decision,
        "authority_conflicts": conflicts,
        "blockers": blockers,
        "reusable_receipts": [],
        "business_information_package": {
            key: value for key, value in information_package.items() if key != "_all_assets"
        },
        "asset_lifecycle": asset_lifecycle,
        "authority_refs": [
            {"schema": SCHEMA_CATALOG["asset"], "name": name, "source_schema": str(payload.get("schema") or "")}
            for name, payload in authorities.items()
        ],
        "execution_graph": {
            "schema": SCHEMA_CATALOG["execution_graph"],
            "nodes": ["workflow.plan", "skills.plan", "placement.plan", "membership.snapshot", "maintenance.coverage"],
            "execution_allowed": False,
        },
        "durable_operation": {
            "schema": SCHEMA_CATALOG["durable_operation"],
            "operation_id": f"shadow:{signature[:24]}",
            "input_signature": signature,
            "side_effect_level": "read_only",
            "terminal_receipt_ref": "",
            "resume_supported": False,
        },
        "business_outcome": {
            "schema": SCHEMA_CATALOG["business_outcome"],
            "state": "shadow_decision_ready" if not blockers and not conflicts else "blocked",
            "consumed": False,
            "quality_evidence": ["authority_refs", "blockers", "authority_conflicts"],
        },
    }


def snapshot() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "shadow_only": True,
        "writes_authoritative_state": False,
        "schemas": SCHEMA_CATALOG,
        "authority_refs": [
            "workflow_orchestrator.plan",
            "skill_orchestrator.plan",
            "code_maintainability.placement_plan",
            "system_membership.snapshot",
            "maintenance_capability_registry.coverage",
        ],
    }


def explain(message: str) -> dict[str, Any]:
    plan = build_plan(message)
    return {
        "schema": f"{SCHEMA}.explain",
        "ok": plan["ok"],
        "generated_at": now_iso(),
        "input_signature": plan["input_signature"],
        "decision": plan["decision"],
        "authority_conflicts": plan["authority_conflicts"],
        "blockers": plan["blockers"],
        "authority_refs": plan["authority_refs"],
    }


def context(message: str) -> dict[str, Any]:
    return build_plan(message)["business_information_package"]


def assets(message: str) -> dict[str, Any]:
    package = business_environment_capability_resolver.collect_information_package(message)
    return {
        "schema": "business_environment_capability_resolver.v1.assets",
        "ok": package["ok"],
        "generated_at": now_iso(),
        "read_only": True,
        "asset_summary": package["asset_summary"],
        "assets": package["_all_assets"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only business environment shadow control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    for name in ("plan", "explain", "context", "assets"):
        child = sub.add_parser(name)
        child.add_argument("--message", required=True)
    args = parser.parse_args(argv)
    actions = {"plan": build_plan, "explain": explain, "context": context, "assets": assets}
    payload = snapshot() if args.command == "snapshot" else actions[args.command](args.message)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main())
