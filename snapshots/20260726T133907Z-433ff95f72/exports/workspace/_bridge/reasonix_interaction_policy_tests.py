#!/usr/bin/env python3
"""Requirement-driven validator for the Reasonix shadow interaction policy."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Callable

from reasonix_interaction_policy import evaluate_payload
from reasonix_interaction_requirements import REQUIREMENTS, Requirement


CheckFn = Callable[[dict[str, Any]], tuple[bool, str]]
MAINTENANCE_REQUIREMENT_IDS = frozenset({"REQ-011", "REQ-012", "REQ-013", "REQ-014", "REQ-015"})


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    requirements: tuple[str, ...]
    payload: Any
    checks: tuple[CheckFn, ...]
    description: str = ""
    evaluator: Callable[[Any], dict[str, Any]] = evaluate_payload


def _path_value(result: dict[str, Any], path: str) -> Any:
    cur: Any = result
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def check(path: str, expected: Any) -> CheckFn:
    def _check(result: dict[str, Any]) -> tuple[bool, str]:
        cur = _path_value(result, path)
        ok = cur == expected
        return ok, f"{path} expected {expected!r}, got {cur!r}"

    return _check


def contains(path: str, expected: str) -> CheckFn:
    def _check(result: dict[str, Any]) -> tuple[bool, str]:
        cur = _path_value(result, path)
        ok = expected in cur
        return ok, f"{path} expected to contain {expected!r}, got {cur!r}"

    return _check


def excludes(path: str, unexpected: str) -> CheckFn:
    def _check(result: dict[str, Any]) -> tuple[bool, str]:
        cur = _path_value(result, path)
        ok = unexpected not in cur
        return ok, f"{path} expected to exclude {unexpected!r}, got {cur!r}"

    return _check


def every_decision_is_shadow_only() -> CheckFn:
    def _check(result: dict[str, Any]) -> tuple[bool, str]:
        notes = result["decision"]["notes"]
        actions = result["decision"]["shadow_actions"]
        has_note = any("shadow_only" in note for note in notes)
        would_only = all(action.startswith("would_") or action in {"record_shadow_decision", "codex_handles_directly"} for action in actions)
        ok = has_note and would_only
        return ok, f"decision must stay shadow-only, notes={notes!r}, actions={actions!r}"

    return _check


def maintenance_command(command: str, payload: Any | None = None, profile: str = "quick") -> Callable[[Any], dict[str, Any]]:
    def _evaluate(_: Any) -> dict[str, Any]:
        from reasonix_interaction_maintenance import evaluate_maintenance

        return evaluate_maintenance(command, payload=payload, profile=profile)

    return _evaluate


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="explicit_reasonix_online_waits_for_done_nonempty",
        kind="positive",
        requirements=("REQ-001", "REQ-005", "REQ-008"),
        description="Explicit Reasonix request while AI is online must wait for real review.",
        payload={
            "task": {"text": "让 Reasonix 审阅这个架构策略", "risk": "L1"},
            "state": {"responder_alive": True, "ai_online": True, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.review_need", "required"),
            check("decision.wait_policy", "normal_wait"),
            contains("decision.acceptable_result_kinds", "reasonix_ai_review"),
            contains("decision.shadow_actions", "would_wait_for_done_nonempty_result"),
            check("decision.should_block_codex", True),
            every_decision_is_shadow_only(),
        ),
    ),
    Scenario(
        name="claimed_without_result_is_never_success",
        kind="negative",
        requirements=("REQ-001", "REQ-007"),
        description="Claimed backlog must be risk, not a consumable result.",
        payload={
            "task": {"text": "Reasonix 审阅这个计划"},
            "state": {"responder_alive": True, "ai_online": True, "claimed_without_result_count": 3},
        },
        checks=(
            contains("decision.risks", "claimed_without_result_backlog"),
            contains("decision.notes", "claimed tasks are not successful reviews; only done plus non-empty result can be consumed"),
            excludes("decision.acceptable_result_kinds", "claimed"),
        ),
    ),
    Scenario(
        name="pending_backlog_is_visible_risk",
        kind="negative",
        requirements=("REQ-007",),
        description="Pending backlog must not disappear from the decision surface.",
        payload={
            "task": {"text": "Reasonix 审阅这个配置"},
            "state": {"responder_alive": True, "ai_online": True, "pending_review_count": 2},
        },
        checks=(contains("decision.risks", "pending_review_backlog"),),
    ),
    Scenario(
        name="no_backlog_has_no_backlog_risk",
        kind="positive",
        requirements=("REQ-007",),
        description="Clean queue state should not invent backlog risk.",
        payload={
            "task": {"text": "Reasonix 审阅这个计划"},
            "state": {"responder_alive": True, "ai_online": True, "claimed_without_result_count": 0, "pending_review_count": 0},
        },
        checks=(
            excludes("decision.risks", "claimed_without_result_backlog"),
            excludes("decision.risks", "pending_review_backlog"),
        ),
    ),
    Scenario(
        name="required_offline_excludes_kb_assist",
        kind="negative",
        requirements=("REQ-002", "REQ-006"),
        description="Required reviews cannot accept offline KB as final review.",
        payload={
            "task": {"text": "配置审查这个方案"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.review_need", "required"),
            check("decision.wait_policy", "background_wait"),
            excludes("decision.acceptable_result_kinds", "reasonix_offline_kb"),
            contains("decision.acceptable_result_kinds", "timeout_pending"),
            contains("decision.acceptable_result_kinds", "late_result"),
            contains("decision.shadow_actions", "would_not_accept_offline_kb_as_review"),
        ),
    ),
    Scenario(
        name="optional_offline_labels_kb_assist_only",
        kind="positive",
        requirements=("REQ-002",),
        description="Optional offline support may expose KB help only with assist-only labeling.",
        payload={
            "task": {"text": "分析一下这个普通问题", "risk": "L1"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.review_need", "optional"),
            contains("decision.acceptable_result_kinds", "reasonix_offline_kb"),
            contains("decision.shadow_actions", "would_label_offline_kb_as_assist_only"),
        ),
    ),
    Scenario(
        name="responder_down_wakes_responder_before_ai",
        kind="positive",
        requirements=("REQ-003",),
        description="Responder down is a different state from AI offline.",
        payload={"task": {"text": "根因分析这个跨系统问题"}, "state": {"responder_alive": False, "ai_online": False}},
        checks=(
            check("decision.review_need", "required"),
            check("decision.wake_policy", "responder_then_true_ai"),
            contains("decision.shadow_actions", "would_start_responder"),
            contains("decision.shadow_actions", "would_recheck_ai_online"),
        ),
    ),
    Scenario(
        name="offline_path_does_not_silently_skip_wake",
        kind="negative",
        requirements=("REQ-003",),
        description="AI offline path must still describe a wake/probe attempt instead of direct fallback.",
        payload={
            "task": {"text": "根因分析这个风险"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.wake_policy", "true_ai"),
            excludes("decision.shadow_actions", "codex_handles_directly"),
            contains("decision.shadow_actions", "would_probe_true_ai"),
        ),
    ),
    Scenario(
        name="ai_offline_checks_credentials_and_probes",
        kind="positive",
        requirements=("REQ-003",),
        description="Responder alive but true AI offline should attempt true AI path in shadow.",
        payload={
            "task": {"text": "架构审阅这个变更"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.wake_policy", "true_ai"),
            contains("decision.shadow_actions", "would_check_credentials"),
            contains("decision.shadow_actions", "would_probe_true_ai"),
        ),
    ),
    Scenario(
        name="missing_credentials_blocks_true_ai_wake",
        kind="negative",
        requirements=("REQ-004",),
        description="Credentials missing must be explicit blocker, not silent fallback.",
        payload={
            "task": {"text": "架构审阅这个变更"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": []},
        },
        checks=(
            contains("decision.risks", "missing_reasonix_credentials"),
            contains("decision.blockers", "true_ai_credentials_missing"),
        ),
    ),
    Scenario(
        name="mcp_transport_closed_blocks_use",
        kind="negative",
        requirements=("REQ-004",),
        description="Closed MCP transport is a blocker and cannot count as successful review.",
        payload={
            "task": {"text": "Reasonix 审阅这个配置"},
            "state": {"responder_alive": True, "ai_online": True, "mcp_transport_ok": False},
        },
        checks=(
            contains("decision.risks", "mcp_transport_unavailable"),
            contains("decision.blockers", "reasonix_mcp_transport_closed"),
            check("decision.wait_policy", "blocked"),
            excludes("decision.shadow_actions", "would_submit_reasonix_request_with_request_id"),
            contains("decision.shadow_actions", "would_not_submit_until_blocker_cleared"),
            check("decision.should_block_codex", False),
        ),
    ),
    Scenario(
        name="healthy_online_has_no_blocker",
        kind="positive",
        requirements=("REQ-004",),
        description="Healthy online path should not invent blockers.",
        payload={
            "task": {"text": "Reasonix 审阅这个配置"},
            "state": {"responder_alive": True, "ai_online": True, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.blockers", []),
            check("decision.wait_policy", "normal_wait"),
            contains("decision.shadow_actions", "would_submit_reasonix_request_with_request_id"),
        ),
    ),
    Scenario(
        name="late_result_path_preserved_for_required_offline",
        kind="positive",
        requirements=("REQ-006",),
        description="Required offline review keeps late-result path available.",
        payload={
            "task": {"text": "跨系统影响评估"},
            "state": {"responder_alive": True, "ai_online": False, "credential_sources": ["credentials"]},
        },
        checks=(
            check("decision.wait_policy", "background_wait"),
            contains("decision.acceptable_result_kinds", "late_result"),
            contains("decision.shadow_actions", "would_preserve_late_result_path"),
        ),
    ),
    Scenario(
        name="l2_task_requires_review",
        kind="positive",
        requirements=("REQ-008",),
        description="Risk level alone can require review.",
        payload={"task": {"text": "你再分析一下这个修改影响", "risk": "L2"}, "state": {"responder_alive": True, "ai_online": True}},
        checks=(check("decision.review_need", "required"), check("decision.should_call_reasonix", True)),
    ),
    Scenario(
        name="domain_keywords_require_review",
        kind="positive",
        requirements=("REQ-008",),
        description="Critical domain keywords should trigger required review.",
        payload={"task": {"text": "做一次配置文件审查和根因分析"}, "state": {"responder_alive": True, "ai_online": True}},
        checks=(check("decision.review_need", "required"),),
    ),
    Scenario(
        name="explicit_reasonix_overrides_trivial_text",
        kind="negative",
        requirements=("REQ-008", "REQ-010"),
        description="A trivial-looking message that explicitly asks Reasonix still requires review.",
        payload={"task": {"text": "Reasonix 你好", "risk": "L1"}, "state": {"responder_alive": True, "ai_online": True}},
        checks=(
            check("decision.review_need", "required"),
            check("decision.should_call_reasonix", True),
        ),
    ),
    Scenario(
        name="trivial_task_skips_reasonix",
        kind="positive",
        requirements=("REQ-010",),
        description="Trivial unrelated requests should not consume Reasonix capacity.",
        payload={"task": {"text": "你好", "risk": "L1"}, "state": {"responder_alive": True, "ai_online": True}},
        checks=(
            check("decision.review_need", "none"),
            check("decision.should_call_reasonix", False),
            check("decision.wait_policy", "no_wait"),
        ),
    ),
    Scenario(
        name="malformed_empty_payload_normalizes",
        kind="negative",
        requirements=("REQ-009",),
        description="Empty payload should normalize and avoid crashes.",
        payload={},
        checks=(
            check("decision.review_need", "none"),
            check("decision.should_call_reasonix", False),
        ),
    ),
    Scenario(
        name="well_formed_payload_remains_evaluable",
        kind="positive",
        requirements=("REQ-009",),
        description="A normal payload still evaluates after malformed-input hardening.",
        payload={"task": {"text": "Reasonix 审阅这个方案"}, "state": {"responder_alive": True, "ai_online": True}},
        checks=(
            check("decision.review_need", "required"),
            check("decision.should_call_reasonix", True),
        ),
    ),
    Scenario(
        name="malformed_non_dict_payload_normalizes",
        kind="negative",
        requirements=("REQ-009",),
        description="Non-dict payload should normalize and avoid crashes.",
        payload=["bad", "payload"],
        checks=(
            check("decision.review_need", "none"),
            check("decision.should_call_reasonix", False),
        ),
    ),
    Scenario(
        name="malformed_counts_do_not_crash",
        kind="boundary",
        requirements=("REQ-009", "REQ-007"),
        description="Non-integer backlog counters normalize rather than crashing.",
        payload={
            "task": {"text": "Reasonix 审阅这个方案", "risk": "unknown"},
            "state": {"responder_alive": True, "ai_online": True, "claimed_without_result_count": "many"},
        },
        checks=(
            check("decision.review_need", "required"),
            excludes("decision.risks", "claimed_without_result_backlog"),
        ),
    ),
    Scenario(
        name="maintenance_snapshot_exposes_contract",
        kind="positive",
        requirements=("REQ-011", "REQ-014", "REQ-015"),
        description="Snapshot must expose the stable maintenance contract and metrics.",
        payload={},
        evaluator=maintenance_command("snapshot"),
        checks=(
            check("mode", "shadow_only"),
            check("capabilities.snapshot", True),
            check("capabilities.doctor", True),
            check("capabilities.repair_plan", True),
            check("capabilities.validate", True),
            check("capabilities.metrics", True),
            check("safety.writes_bridge_state", False),
            check("safety.starts_processes", False),
            check("safety.calls_reasonix", False),
            check("metrics.requirements_ok", True),
            check("metrics.repair_dry_run_only", True),
        ),
    ),
    Scenario(
        name="maintenance_contract_does_not_call_external_systems",
        kind="negative",
        requirements=("REQ-011",),
        description="Maintenance contract safety flags must stay false for external side effects.",
        payload={},
        evaluator=maintenance_command("snapshot"),
        checks=(
            check("safety.writes_bridge_state", False),
            check("safety.starts_processes", False),
            check("safety.calls_external_api", False),
            check("safety.calls_reasonix", False),
        ),
    ),
    Scenario(
        name="maintenance_repair_plan_clean_state_is_empty_plan",
        kind="positive",
        requirements=("REQ-012",),
        description="Clean repair-plan should remain dry-run and have no work to execute.",
        payload={},
        evaluator=maintenance_command("repair-plan"),
        checks=(
            check("dry_run", True),
            check("would_write", False),
            check("would_start_process", False),
            check("plan_items", []),
            check("doctor_ok", True),
        ),
    ),
    Scenario(
        name="maintenance_repair_plan_is_dry_run_only",
        kind="negative",
        requirements=("REQ-012",),
        description="Repair planning must not execute writes, processes, or Reasonix calls.",
        payload={},
        evaluator=maintenance_command(
            "repair-plan",
            payload={
                "task": {"text": "Reasonix 审阅这个配置"},
                "state": {"responder_alive": True, "ai_online": True, "mcp_transport_ok": False},
            },
        ),
        checks=(
            check("dry_run", True),
            check("would_write", False),
            check("would_start_process", False),
            check("would_call_reasonix", False),
            contains("plan_items.0.action", "repair_transport_before_submit"),
            check("plan_items.0.dry_run_only", True),
            check("plan_items.0.would_execute", False),
            check("plan_items.0.requires_user_confirmation", True),
        ),
    ),
    Scenario(
        name="maintenance_doctor_classifies_transport_and_backlog",
        kind="negative",
        requirements=("REQ-013",),
        description="Doctor must keep external transport blockers separate from backlog drift risks.",
        payload={},
        evaluator=maintenance_command(
            "doctor",
            payload={
                "task": {"text": "Reasonix 审阅这个配置"},
                "state": {
                    "responder_alive": True,
                    "ai_online": True,
                    "mcp_transport_ok": False,
                    "claimed_without_result_count": 2,
                },
            },
        ),
        checks=(
            check("ok", False),
            check("blockers.0.category", "external_transport_failure"),
            check("risks.0.category", "live_state_drift"),
            contains("advisories.0.code", "shadow_only_not_integrated"),
        ),
    ),
    Scenario(
        name="maintenance_doctor_clean_state_has_advisories_only",
        kind="positive",
        requirements=("REQ-013",),
        description="Clean doctor report should have no blockers or risks but still provide rollout advisories.",
        payload={},
        evaluator=maintenance_command("doctor"),
        checks=(
            check("ok", True),
            check("blockers", []),
            check("risks", []),
            contains("advisories.1.code", "keep_contract_in_sync"),
        ),
    ),
    Scenario(
        name="maintenance_validate_returns_structured_profile",
        kind="positive",
        requirements=("REQ-014",),
        description="Validate command must expose structured validation report and profile metadata.",
        payload={},
        evaluator=maintenance_command("validate", profile="full"),
        checks=(
            check("profile", "full"),
            check("validation.ok", True),
            check("metrics.requirements_ok", True),
            check("metrics.shadow_only_ok", True),
        ),
    ),
    Scenario(
        name="maintenance_metrics_are_machine_readable",
        kind="positive",
        requirements=("REQ-014",),
        description="Metrics command must return structured counters and booleans.",
        payload={},
        evaluator=maintenance_command("metrics"),
        checks=(
            check("metrics.requirements_ok", True),
            check("metrics.scenario_failure_count", 0),
            check("metrics.blocking_requirement_count", 0),
            check("metrics.shadow_only_ok", True),
        ),
    ),
    Scenario(
        name="maintenance_validate_unknown_profile_normalizes",
        kind="negative",
        requirements=("REQ-014",),
        description="Unknown validation profile should normalize to quick while preserving request metadata.",
        payload={},
        evaluator=maintenance_command("validate", profile="surprise"),
        checks=(
            check("profile", "quick"),
            check("requested_profile", "surprise"),
            check("validation.ok", True),
        ),
    ),
    Scenario(
        name="maintenance_requirements_are_in_coverage_gate",
        kind="positive",
        requirements=("REQ-015",),
        description="Maintenance requirements must appear in the same requirement catalog as policy requirements.",
        payload={},
        evaluator=maintenance_command("snapshot"),
        checks=(
            contains("requirements.10.id", "REQ-011"),
            contains("requirements.14.id", "REQ-015"),
            check("validation_summary.blocking_requirements", []),
        ),
    ),
    Scenario(
        name="maintenance_requirement_catalog_has_no_unknown_ids",
        kind="negative",
        requirements=("REQ-015",),
        description="Coverage report must reject unknown requirement references; current catalog should have none.",
        payload={},
        evaluator=maintenance_command("validate"),
        checks=(
            check("validation.coverage.unknown_requirements", []),
            check("validation.coverage_ok", True),
        ),
    ),
)


def _requirement_map() -> dict[str, Requirement]:
    return {requirement.id: requirement for requirement in REQUIREMENTS}


def _run_scenario(scenario: Scenario) -> dict[str, Any]:
    failed_checks = []
    try:
        result = scenario.evaluator(scenario.payload)
    except Exception as exc:  # noqa: BLE001 - validator must report crashes as scenario failures.
        return {
            "name": scenario.name,
            "kind": scenario.kind,
            "requirements": list(scenario.requirements),
            "ok": False,
            "failed_checks": [f"scenario raised {type(exc).__name__}: {exc}"],
            "decision": None,
            "output": None,
        }

    for fn in scenario.checks:
        ok, message = fn(result)
        if not ok:
            failed_checks.append(message)
    return {
        "name": scenario.name,
        "kind": scenario.kind,
        "requirements": list(scenario.requirements),
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "decision": result.get("decision"),
        "output": result,
    }


def _coverage_report(results: list[dict[str, Any]], requirements: tuple[Requirement, ...]) -> dict[str, Any]:
    requirement_map = _requirement_map()
    coverage: dict[str, dict[str, Any]] = {}
    for requirement in requirements:
        linked = [result for result in results if requirement.id in result["requirements"]]
        positive = [result for result in linked if result["kind"] == "positive"]
        negative = [result for result in linked if result["kind"] == "negative"]
        boundary = [result for result in linked if result["kind"] == "boundary"]
        failures = [result for result in linked if not result["ok"]]
        missing: list[str] = []
        if len(positive) < requirement.min_positive_scenarios:
            missing.append(f"positive<{requirement.min_positive_scenarios}")
        if len(negative) < requirement.min_negative_scenarios:
            missing.append(f"negative<{requirement.min_negative_scenarios}")
        coverage[requirement.id] = {
            "priority": requirement.priority,
            "statement": requirement.statement,
            "scenario_count": len(linked),
            "positive": len(positive),
            "negative": len(negative),
            "boundary": len(boundary),
            "failures": [result["name"] for result in failures],
            "missing": missing,
            "blocks_release": requirement.blocks_release,
            "ok": not failures and not missing,
        }

    unknown_requirements = sorted(
        {
            requirement_id
            for result in results
            for requirement_id in result["requirements"]
            if requirement_id not in requirement_map
        }
    )
    blocking = [
        requirement_id
        for requirement_id, item in coverage.items()
        if item["blocks_release"] and not item["ok"]
    ]
    return {
        "ok": not blocking and not unknown_requirements,
        "blocking_requirements": blocking,
        "unknown_requirements": unknown_requirements,
        "requirements": coverage,
    }


def run_scenarios(include_maintenance: bool = True) -> dict[str, Any]:
    requirements = REQUIREMENTS if include_maintenance else tuple(
        requirement for requirement in REQUIREMENTS if requirement.id not in MAINTENANCE_REQUIREMENT_IDS
    )
    selected_scenarios = SCENARIOS if include_maintenance else tuple(
        scenario
        for scenario in SCENARIOS
        if not any(requirement_id in MAINTENANCE_REQUIREMENT_IDS for requirement_id in scenario.requirements)
    )
    results = [_run_scenario(scenario) for scenario in selected_scenarios]
    failures = [result for result in results if not result["ok"]]
    coverage = _coverage_report(results, requirements)
    return {
        "ok": not failures and coverage["ok"],
        "scenario_count": len(selected_scenarios),
        "failure_count": len(failures),
        "requirement_count": len(requirements),
        "include_maintenance": include_maintenance,
        "coverage_ok": coverage["ok"],
        "coverage": coverage,
        "results": results,
    }


def _print_text_report(report: dict[str, Any]) -> None:
    print(
        "ok={ok} scenarios={scenario_count} failures={failure_count} requirements={requirement_count} coverage_ok={coverage_ok}".format(
            **report
        )
    )
    for item in report["results"]:
        status = "PASS" if item["ok"] else "FAIL"
        reqs = ",".join(item["requirements"])
        print(f"{status} {item['name']} kind={item['kind']} reqs={reqs}")
        for failure in item["failed_checks"]:
            print(f"  - {failure}")

    blocking = report["coverage"]["blocking_requirements"]
    if blocking:
        print("blocking_requirements: " + ", ".join(blocking))
    else:
        print("blocking_requirements: none")

    for requirement_id, item in report["coverage"]["requirements"].items():
        status = "PASS" if item["ok"] else "GAP"
        print(
            f"{status} {requirement_id} {item['priority']} scenarios={item['scenario_count']} "
            f"positive={item['positive']} negative={item['negative']} boundary={item['boundary']}"
        )
        if item["missing"]:
            print("  missing: " + ", ".join(item["missing"]))
        if item["failures"]:
            print("  failures: " + ", ".join(item["failures"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Reasonix shadow policy against requirements")
    parser.add_argument("--json", action="store_true", help="Emit full JSON")
    args = parser.parse_args()
    report = run_scenarios()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
