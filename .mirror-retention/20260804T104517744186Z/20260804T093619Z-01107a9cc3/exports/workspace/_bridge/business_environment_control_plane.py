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
from pathlib import Path
from typing import Any

import business_environment_capability_resolver
import code_maintainability
import maintenance_capability_registry
import skill_orchestrator
import system_membership
import workflow_orchestrator
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


SCHEMA = "business_environment_control_plane.v1"
BUSINESS_OUTCOME_OBSERVATION_SCHEMA = "business_environment.outcome_observation.v1"
SCHEMA_CATALOG = {
    "business_intent": "business_environment.business_intent.v1",
    "capability": "business_environment.capability_ref.v1",
    "asset": "business_environment.asset_ref.v1",
    "authority_binding": "business_environment.authority_binding_ref.v1",
    "execution_graph": "business_environment.execution_graph_ref.v1",
    "durable_operation": "business_environment.durable_operation_ref.v1",
    "lifecycle_record": "business_environment.lifecycle_projection.v1",
    "business_outcome": "business_environment.business_outcome_ref.v1",
    "business_outcome_observation": BUSINESS_OUTCOME_OBSERVATION_SCHEMA,
}
DOMAIN_BUSINESS_CATEGORIES = {
    "code_review_refactor": "software_engineering",
    "external_docs_research": "external_research",
    "web_research": "external_research",
    "slide_deck": "content_production",
    "workflow_governance": "system_maintenance",
}
BUSINESS_ACCEPTANCE_SCENARIOS = (
    "software_engineering",
    "system_maintenance",
    "external_research",
    "content_production",
    "cross_system_automation",
)
TASK_SEGMENT_CLASSES = {
    "simple_explanation",
    "readonly_diagnosis",
    "authorized_repository_change",
    "external_resource_acquisition",
    "closeout_release_coordination",
}
MEASUREMENT_CONFIDENCE_LEVELS = {"low", "medium", "high"}
TASK_SEGMENT_TIME_FIELDS = (
    "active_execution_ms",
    "tool_wait_ms",
    "user_wait_ms",
    "idle_gap_ms",
    "rework_ms",
    "governance_tax_ms",
)
TASK_SEGMENT_COUNT_FIELDS = (
    "approval_round_trip_count",
    "clarification_round_trip_count",
)
MAX_TASK_SEGMENT_MS = 604_800_000
MAX_TASK_SEGMENT_ROUND_TRIPS = 10_000


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


def _domain_layers_compatible(
    workflow_domains: list[str], skill_domains: list[str], business_category: str
) -> bool:
    """Compare semantic business ownership, not layer-local route labels."""

    if not workflow_domains or not skill_domains or set(workflow_domains) & set(skill_domains):
        return True
    if "general" in workflow_domains:
        return True
    workflow_categories = {DOMAIN_BUSINESS_CATEGORIES.get(item, "") for item in workflow_domains}
    skill_categories = {DOMAIN_BUSINESS_CATEGORIES.get(item, "") for item in skill_domains}
    return business_category in workflow_categories and business_category in skill_categories


def _valid_evidence_refs(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _outcome_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_integer(value: Any, *, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _project_task_segment(value: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate one owner-supplied timeline and return its redacted projection."""

    if not isinstance(value, dict):
        return {}, ["task_segment_must_be_object"]
    issues: list[str] = []
    task_segment_class = str(value.get("task_segment_class") or "").strip()
    confidence = str(value.get("measurement_confidence") or "").strip()
    timeline_ref = str(value.get("timeline_evidence_ref") or "").strip()
    if task_segment_class not in TASK_SEGMENT_CLASSES:
        issues.append("task_segment_class_unknown")
    if confidence not in MEASUREMENT_CONFIDENCE_LEVELS:
        issues.append("measurement_confidence_unknown")
    if not timeline_ref:
        issues.append("timeline_evidence_missing")
    for field in TASK_SEGMENT_TIME_FIELDS:
        if not _bounded_integer(value.get(field), maximum=MAX_TASK_SEGMENT_MS):
            issues.append(f"{field}_invalid")
    for field in TASK_SEGMENT_COUNT_FIELDS:
        if not _bounded_integer(value.get(field), maximum=MAX_TASK_SEGMENT_ROUND_TRIPS):
            issues.append(f"{field}_invalid")
    if not isinstance(value.get("first_pass"), bool):
        issues.append("first_pass_boolean_required")
    if issues:
        return {}, issues

    active_execution_ms = int(value["active_execution_ms"])
    rework_ms = int(value["rework_ms"])
    observed_total_ms = sum(int(value[field]) for field in (
        "active_execution_ms", "tool_wait_ms", "user_wait_ms", "idle_gap_ms"
    ))
    governance_tax_ms = int(value["governance_tax_ms"])
    if rework_ms > active_execution_ms:
        issues.append("rework_exceeds_active_execution")
    if governance_tax_ms > observed_total_ms:
        issues.append("governance_tax_exceeds_observed_total")
    if issues:
        return {}, issues

    avoidable_costs = {
        "tool_wait": int(value["tool_wait_ms"]),
        "user_wait": int(value["user_wait_ms"]),
        "idle_gap": int(value["idle_gap_ms"]),
        "rework": rework_ms,
    }
    dominant_cost_class = max(avoidable_costs, key=lambda key: (avoidable_costs[key], key))
    return {
        "task_segment_class": task_segment_class,
        **{field: int(value[field]) for field in TASK_SEGMENT_TIME_FIELDS},
        **{field: int(value[field]) for field in TASK_SEGMENT_COUNT_FIELDS},
        "first_pass": bool(value["first_pass"]),
        "measurement_confidence": confidence,
        "active_lead_time_ms": active_execution_ms + int(value["tool_wait_ms"]),
        "observed_total_ms": observed_total_ms,
        "governance_tax_ratio": round(governance_tax_ms / observed_total_ms, 6) if observed_total_ms else 0.0,
        "dominant_cost_class": dominant_cost_class,
        "timeline_evidence_key": _outcome_digest(timeline_ref)[:24],
        "stores_timeline_evidence_ref": False,
    }, []


def build_business_outcome_observations(
    outcomes: list[dict[str, Any]], *, task_ref: str
) -> dict[str, Any]:
    """Project business acceptance into redacted, deduplicable observer inputs.

    This control plane remains read-only. The observation owner decides whether
    and where to persist these projections.
    """

    task_text = str(task_ref or "").strip()
    if not task_text:
        return {"schema": BUSINESS_OUTCOME_OBSERVATION_SCHEMA, "ok": False, "reason": "task_ref_required"}
    if not isinstance(outcomes, list) or not outcomes:
        return {"schema": BUSINESS_OUTCOME_OBSERVATION_SCHEMA, "ok": False, "reason": "outcomes_required"}
    task_key = f"business-task:{_outcome_digest(task_text)[:24]}"
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(outcomes):
        if not isinstance(raw, dict):
            errors.append({"index": index, "reason": "outcome_must_be_object"})
            continue
        category = str(raw.get("category") or "").strip()
        accepted = raw.get("accepted")
        consumed = raw.get("consumed")
        result_ref = str(raw.get("result_ref") or "").strip()
        evidence_refs = raw.get("evidence_refs")
        task_segment = raw.get("task_segment")
        issues: list[str] = []
        if category not in BUSINESS_ACCEPTANCE_SCENARIOS:
            issues.append("business_category_unknown")
        if not isinstance(accepted, bool) or not isinstance(consumed, bool):
            issues.append("business_outcome_flags_required")
        if not result_ref:
            issues.append("owner_result_reference_missing")
        if not _valid_evidence_refs(evidence_refs):
            issues.append("acceptance_evidence_missing")
        task_segment_projection: dict[str, Any] = {}
        if task_segment is not None:
            task_segment_projection, segment_issues = _project_task_segment(task_segment)
            issues.extend(segment_issues)
        if issues:
            errors.append({"index": index, "category": category, "reason": issues})
            continue
        outcome_state = "accepted_and_consumed" if accepted and consumed else (
            "accepted_not_consumed" if accepted else "not_accepted"
        )
        result_key = _outcome_digest(result_ref)[:24]
        evidence_key = _outcome_digest(sorted(str(item).strip() for item in evidence_refs))[:24]
        event_identity = {
            "task_key": task_key, "category": category, "outcome_state": outcome_state,
            "result_key": result_key, "evidence_key": evidence_key,
        }
        if task_segment_projection:
            event_identity["task_segment"] = task_segment_projection
        event_id = _outcome_digest(event_identity)[:24]
        observations.append({
            "schema": BUSINESS_OUTCOME_OBSERVATION_SCHEMA,
            "event": "BusinessOutcome",
            "event_id": f"business-outcome-{event_id}",
            "task_key": task_key,
            "category": category,
            "accepted": accepted,
            "consumed": consumed,
            "outcome_state": outcome_state,
            "delivery_value_state": outcome_state,
            "result_key": result_key,
            "evidence_key": evidence_key,
            "stores_task_ref": False,
            "stores_result_ref": False,
            "stores_evidence_refs": False,
            **task_segment_projection,
        })
    return {
        "schema": BUSINESS_OUTCOME_OBSERVATION_SCHEMA,
        "ok": not errors,
        "read_only": True,
        "writes_authoritative_state": False,
        "task_key": task_key,
        "observation_count": len(observations),
        "observations": observations,
        "errors": errors,
    }


def _durable_executor_contract_ok(payload: dict[str, Any]) -> bool:
    checks = payload.get("checks")
    if isinstance(checks, dict):
        return checks.get("no_business_resubmit_contract") is True
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("name") == "no_business_resubmit_contract"
        and item.get("ok") is True
        for item in checks
    )


def _scheduler_contract_ok(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    if "drift_count" in payload:
        return int(payload.get("drift_count") or 0) == 0
    configuration = payload.get("configuration")
    if isinstance(configuration, dict):
        return _scheduler_contract_ok(configuration)
    return all(payload.get(key) == [] for key in ("missing_task_ids", "runtime_only_task_ids", "changed_tasks"))


def build_final_acceptance(
    outcomes: list[dict[str, Any]], *, owner_results: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Validate consumed business results and existing owner gates without writes."""

    rows_by_category: dict[str, list[dict[str, Any]]] = {
        category: [] for category in BUSINESS_ACCEPTANCE_SCENARIOS
    }
    unexpected_categories: list[str] = []
    for raw in outcomes:
        category = str(raw.get("category") or "") if isinstance(raw, dict) else ""
        if category in rows_by_category:
            rows_by_category[category].append(raw)
        else:
            unexpected_categories.append(category or "<missing>")

    business_outcomes: list[dict[str, Any]] = []
    missing_categories: list[str] = []
    duplicate_categories: list[str] = []
    for category in BUSINESS_ACCEPTANCE_SCENARIOS:
        candidates = rows_by_category[category]
        if not candidates:
            missing_categories.append(category)
            business_outcomes.append({
                "category": category,
                "ok": False,
                "issues": ["business_outcome_missing"],
                "result_ref": "",
                "evidence_refs": [],
            })
            continue
        if len(candidates) > 1:
            duplicate_categories.append(category)
        raw = candidates[0]
        issues: list[str] = []
        if len(candidates) > 1:
            issues.append("duplicate_business_outcome")
        if raw.get("accepted") is not True:
            issues.append("owner_result_not_accepted")
        if raw.get("consumed") is not True:
            issues.append("owner_result_not_consumed")
        result_ref = str(raw.get("result_ref") or "").strip()
        if not result_ref:
            issues.append("owner_result_reference_missing")
        evidence_refs = raw.get("evidence_refs")
        if not _valid_evidence_refs(evidence_refs):
            issues.append("acceptance_evidence_missing")
        business_outcomes.append({
            "category": category,
            "ok": not issues,
            "issues": issues,
            "result_ref": result_ref,
            "evidence_refs": evidence_refs if isinstance(evidence_refs, list) else [],
        })

    recovery = owner_results.get("recovery") if isinstance(owner_results.get("recovery"), dict) else {}
    membership = owner_results.get("membership") if isinstance(owner_results.get("membership"), dict) else {}
    review_queue = owner_results.get("review_queue") if isinstance(owner_results.get("review_queue"), dict) else {}
    scheduler = owner_results.get("scheduler") if isinstance(owner_results.get("scheduler"), dict) else {}
    durable_executor = owner_results.get("durable_executor") if isinstance(owner_results.get("durable_executor"), dict) else {}
    owner_gates = {
        "recovery": recovery.get("ok") is True and recovery.get("capability_restore_ready") is True,
        "membership": membership.get("ok") is True,
        "review_queue": review_queue.get("ok") is True and review_queue.get("pending") == [],
        "scheduler": _scheduler_contract_ok(scheduler),
        "durable_executor": durable_executor.get("ok") is True and _durable_executor_contract_ok(durable_executor),
    }
    tombstone_count = int(membership.get("retirement_tombstone_count") or 0)
    tombstones = membership.get("retirement_tombstones")
    if tombstone_count == 0 and (tombstones is None or tombstones == []):
        retirement = {"ok": True, "disposition": "no-removal", "tombstone_count": 0}
    else:
        retirement_result = membership.get("retirement_acceptance")
        retirement_ok = (
            isinstance(retirement_result, dict)
            and retirement_result.get("ok") is True
            and int(retirement_result.get("accepted_tombstone_count") or 0) == tombstone_count
        )
        retirement = {
            "ok": retirement_ok,
            "disposition": "owner-validated-retirement" if retirement_ok else "retirement-owner-acceptance-required",
            "tombstone_count": tombstone_count,
        }

    consumed_count = sum(1 for row in business_outcomes if row["ok"])
    ok = (
        consumed_count == len(BUSINESS_ACCEPTANCE_SCENARIOS)
        and not missing_categories
        and not duplicate_categories
        and not unexpected_categories
        and all(owner_gates.values())
        and retirement["ok"]
    )
    return {
        "schema": f"{SCHEMA}.final_acceptance",
        "ok": ok,
        "generated_at": now_iso(),
        "read_only": True,
        "writes_authoritative_state": False,
        "business_outcomes": business_outcomes,
        "business_outcome_summary": {
            "required_count": len(BUSINESS_ACCEPTANCE_SCENARIOS),
            "consumed_count": consumed_count,
            "missing_categories": missing_categories,
            "duplicate_categories": duplicate_categories,
            "unexpected_categories": sorted(unexpected_categories),
        },
        "owner_gates": owner_gates,
        "failed_owner_gates": sorted(name for name, passed in owner_gates.items() if not passed),
        "retirement": retirement,
    }


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
    if not _domain_layers_compatible(
        workflow_domains,
        skill_domains,
        str(information_package.get("business_category") or ""),
    ):
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
    acceptance = sub.add_parser("acceptance")
    acceptance.add_argument("--outcomes-json", type=Path, required=True)
    acceptance.add_argument("--owner-results-json", type=Path, required=True)
    args = parser.parse_args(argv)
    actions = {"plan": build_plan, "explain": explain, "context": context, "assets": assets}
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "acceptance":
        outcomes = json.loads(args.outcomes_json.read_text(encoding="utf-8"))
        owner_results = json.loads(args.owner_results_json.read_text(encoding="utf-8"))
        if not isinstance(outcomes, list) or not isinstance(owner_results, dict):
            raise ValueError("acceptance inputs must be an outcomes list and owner-results object")
        payload = build_final_acceptance(outcomes, owner_results=owner_results)
    else:
        payload = actions[args.command](args.message)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main())
