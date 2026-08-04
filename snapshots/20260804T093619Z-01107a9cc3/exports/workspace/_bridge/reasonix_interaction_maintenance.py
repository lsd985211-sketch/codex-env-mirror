#!/usr/bin/env python3
"""Read-only maintenance contract for Reasonix interaction shadow policy.

The maintenance layer is intentionally inert: it reports health, coverage,
metrics, and dry-run repair plans, but it does not mutate bridge state, start
processes, call Reasonix, or touch external services.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from reasonix_interaction_policy import evaluate_payload
from reasonix_interaction_policy_tests import run_scenarios
from reasonix_interaction_requirements import REQUIREMENTS


CONTRACT_VERSION = "2026-06-26.1"
PRODUCER_VERSION = "reasonix-interaction-shadow-maintenance/1"
SCHEMA_VERSION = "maintenance-contract/v1"


@dataclass(frozen=True)
class MaintenanceIssue:
    category: str
    severity: str
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "evidence": self.evidence,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_contract() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "producer_version": PRODUCER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "mode": "shadow_only",
        "capabilities": {
            "snapshot": True,
            "doctor": True,
            "repair_plan": True,
            "validate": True,
            "metrics": True,
        },
        "safety": {
            "writes_bridge_state": False,
            "starts_processes": False,
            "calls_reasonix": False,
            "calls_external_api": False,
            "repair_defaults_to_dry_run": True,
        },
    }


def maintenance_metrics(validation_report: dict[str, Any] | None = None) -> dict[str, Any]:
    report = validation_report or run_scenarios(include_maintenance=False)
    coverage = report["coverage"]
    requirement_items = coverage["requirements"]
    blocking_count = len(coverage["blocking_requirements"])
    failing_requirement_count = sum(1 for item in requirement_items.values() if not item["ok"])
    return {
        "requirements_ok": coverage["ok"],
        "requirement_count": report["requirement_count"],
        "scenario_count": report["scenario_count"],
        "scenario_failure_count": report["failure_count"],
        "blocking_requirement_count": blocking_count,
        "failing_requirement_count": failing_requirement_count,
        "shadow_only_ok": True,
        "repair_dry_run_only": True,
    }


def maintenance_snapshot() -> dict[str, Any]:
    report = run_scenarios(include_maintenance=False)
    contract = _base_contract()
    contract.update(
        {
            "validation_summary": {
                "ok": report["ok"],
                "coverage_ok": report["coverage_ok"],
                "scenario_count": report["scenario_count"],
                "failure_count": report["failure_count"],
                "requirement_count": report["requirement_count"],
                "blocking_requirements": report["coverage"]["blocking_requirements"],
            },
            "requirements": [
                {
                    "id": requirement.id,
                    "priority": requirement.priority,
                    "blocks_release": requirement.blocks_release,
                    "statement": requirement.statement,
                }
                for requirement in REQUIREMENTS
            ],
            "metrics": maintenance_metrics(report),
        }
    )
    return contract


def _issues_from_report(report: dict[str, Any]) -> list[MaintenanceIssue]:
    issues: list[MaintenanceIssue] = []
    coverage = report["coverage"]
    if report["failure_count"]:
        issues.append(
            MaintenanceIssue(
                category="policy_failure",
                severity="blocker",
                code="scenario_failures",
                message="One or more Reasonix interaction policy scenarios failed.",
                evidence={"failure_count": report["failure_count"]},
            )
        )
    if coverage["blocking_requirements"]:
        issues.append(
            MaintenanceIssue(
                category="coverage_gap",
                severity="blocker",
                code="blocking_requirements",
                message="Release-blocking requirements are not covered or are failing.",
                evidence={"blocking_requirements": coverage["blocking_requirements"]},
            )
        )
    if coverage["unknown_requirements"]:
        issues.append(
            MaintenanceIssue(
                category="contract_drift",
                severity="blocker",
                code="unknown_requirements",
                message="Scenarios reference requirements absent from the catalog.",
                evidence={"unknown_requirements": coverage["unknown_requirements"]},
            )
        )
    return issues


def _issues_from_payload_probe(payload: Any | None) -> list[MaintenanceIssue]:
    if payload is None:
        return []
    result = evaluate_payload(payload)
    decision = result["decision"]
    issues: list[MaintenanceIssue] = []
    for blocker in decision["blockers"]:
        issues.append(
            MaintenanceIssue(
                category="external_transport_failure" if "transport" in blocker else "policy_blocker",
                severity="blocker",
                code=blocker,
                message=f"Payload probe produced blocker: {blocker}",
                evidence={"decision": decision},
            )
        )
    for risk in decision["risks"]:
        category = "live_state_drift" if "backlog" in risk else "policy_risk"
        issues.append(
            MaintenanceIssue(
                category=category,
                severity="risk",
                code=risk,
                message=f"Payload probe produced risk: {risk}",
                evidence={"decision": decision},
            )
        )
    return issues


def maintenance_doctor(payload: Any | None = None) -> dict[str, Any]:
    report = run_scenarios(include_maintenance=False)
    issues = _issues_from_report(report) + _issues_from_payload_probe(payload)
    blockers = [issue.to_dict() for issue in issues if issue.severity == "blocker"]
    risks = [issue.to_dict() for issue in issues if issue.severity == "risk"]
    advisories = [
        {
            "category": "rollout",
            "severity": "advisory",
            "code": "shadow_only_not_integrated",
            "message": "Reasonix interaction policy is validated as shadow-only and is not wired into live execution.",
        },
        {
            "category": "maintenance_contract",
            "severity": "advisory",
            "code": "keep_contract_in_sync",
            "message": "Update maintenance outputs when Reasonix interaction states, requirements, or maintenance semantics change.",
        },
    ]
    return {
        **_base_contract(),
        "ok": not blockers,
        "blockers": blockers,
        "risks": risks,
        "advisories": advisories,
        "metrics": maintenance_metrics(report),
    }


def maintenance_repair_plan(payload: Any | None = None, dry_run: bool = True) -> dict[str, Any]:
    doctor = maintenance_doctor(payload)
    plan_items: list[dict[str, Any]] = []
    for issue in doctor["blockers"] + doctor["risks"]:
        action = "review_issue"
        if issue["category"] == "coverage_gap":
            action = "add_or_fix_requirement_scenarios"
        elif issue["category"] == "policy_failure":
            action = "fix_policy_or_scenario_contract"
        elif issue["category"] == "external_transport_failure":
            action = "repair_transport_before_submit"
        elif issue["category"] == "live_state_drift":
            action = "inspect_backlog_and_result_contract"
        plan_items.append(
            {
                "action": action,
                "source_code": issue["code"],
                "dry_run_only": True,
                "would_execute": False,
                "requires_user_confirmation": True,
            }
        )
    return {
        **_base_contract(),
        "dry_run": True if dry_run else True,
        "requested_dry_run": dry_run,
        "would_write": False,
        "would_start_process": False,
        "would_call_reasonix": False,
        "plan_items": plan_items,
        "doctor_ok": doctor["ok"],
    }


def maintenance_validate(profile: str = "quick") -> dict[str, Any]:
    report = run_scenarios(include_maintenance=False)
    supported_profiles = ("quick", "full")
    profile_used = profile if profile in supported_profiles else "quick"
    return {
        **_base_contract(),
        "profile": profile_used,
        "requested_profile": profile,
        "validation": report,
        "metrics": maintenance_metrics(report),
        "ok": report["ok"],
        "notes": [
            "quick and full are currently equivalent because live-state adapter is not enabled",
            "future full profile should include read-only live-state adapter and historical replay checks",
        ],
    }


def evaluate_maintenance(command: str, payload: Any | None = None, profile: str = "quick") -> dict[str, Any]:
    if command == "snapshot":
        return maintenance_snapshot()
    if command == "doctor":
        return maintenance_doctor(payload)
    if command == "repair-plan":
        return maintenance_repair_plan(payload, dry_run=True)
    if command == "validate":
        return maintenance_validate(profile)
    if command == "metrics":
        return {**_base_contract(), "metrics": maintenance_metrics()}
    raise ValueError(f"unknown maintenance command: {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reasonix interaction maintenance contract")
    parser.add_argument("command", choices=("snapshot", "doctor", "repair-plan", "validate", "metrics"))
    parser.add_argument("--payload", help="Optional inline JSON payload for doctor/repair-plan probes")
    parser.add_argument("--profile", default="quick", help="Validation profile: quick or full")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    payload = json.loads(args.payload) if args.payload else None
    result = evaluate_maintenance(args.command, payload=payload, profile=args.profile)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
