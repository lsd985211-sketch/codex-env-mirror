#!/usr/bin/env python3
"""Compact per-turn infrastructure route pack.

Ownership: workflow routing support for Codex preflight.
Non-goals: classify tasks, execute tools, mutate memory, or replace owner MCPs.
State behavior: read-only projection from an already-built workflow plan.
Caller context: workflow_orchestrator and codex_workflow_entry use this to keep
mandatory infrastructure routing short, stable, and machine-friendly.
"""

from __future__ import annotations

import json
from typing import Any

from intent_routing import matched_terms, term_matches
from mcp_route_policy import call_priority_pack, common_direct_hub_options, direct_hub_hints_for, direct_hub_tools_for, execution_affinity, preferred_direct_hub_tool, route_policy
from workflow_automation_delegation import automation_delegation_decision, compact_automation_delegation_policy
from task_route_contract import resolve_task_route_contract
from structured_task_envelope import build_legacy_resource_envelope


ALWAYS_STOP_IF = (
    "write_or_external_action_without_explicit_approval",
    "native_tool_failed_without_same_boundary_hub_or_fallback_evidence",
    "resource_or_network_owner_boundary_unclear",
)

FACT_POLICY_RULES = {
    "local_write": ("workflow.task_contract",),
    "config_change": ("workflow.task_contract", "workflow.closeout"),
    "system_member_change": ("system.membership", "workflow.closeout"),
    "external_network_read": ("external.online_access", "resource.structured_contract"),
    "external_write": ("workflow.task_contract",),
    "resource_materialization": ("resource.structured_contract", "resource.source_and_satisfaction"),
    "package_install": ("resource.structured_contract", "network.route_policy"),
    "database_write": ("workflow.task_contract",),
    "gui_or_browser_state": ("workflow.task_contract",),
    "secret_or_permission_use": ("platform.precedence",),
    "destructive_or_high_risk": ("platform.precedence",),
    "reload_or_restart_required": ("workflow.closeout",),
    "durable_closeout_required": ("workflow.closeout",),
    "explicit_mobile_envelope": ("mobile.permission_contract",),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _policy_decisions(task_facts: dict[str, Any], matched_signals: dict[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = [
        {
            "rule_id": "workflow.task_contract",
            "decision": "consumed",
            "enforcement_point": "execution_route_pack.route_decision",
            "provenance": {"source": "task_route_contract"},
        },
        {
            "rule_id": "workflow.execution_decision",
            "decision": "consumed",
            "enforcement_point": "execution_route_pack.required_gates",
            "provenance": {"source": "execution_route_pack"},
        },
    ]
    seen = {item["rule_id"] for item in decisions}
    for fact, value in task_facts.items():
        if not value:
            continue
        for rule_id in FACT_POLICY_RULES.get(fact, ("workflow.task_contract",)):
            if rule_id in seen:
                continue
            seen.add(rule_id)
            decisions.append(
                {
                    "rule_id": rule_id,
                    "decision": "required",
                    "enforcement_point": "execution_route_pack.required_gates",
                    "trigger_fact": fact,
                    "provenance": matched_signals.get(fact, {}),
                }
            )
    return decisions


def _domain_keys(plan: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for item in _as_list(plan.get("domains")):
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            if key:
                keys.append(key)
    return keys


def _domain_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _as_list(plan.get("domains")) if isinstance(item, dict)]


def _primary_domain(plan: dict[str, Any]) -> dict[str, Any]:
    domains = _domain_items(plan)
    drivers = [item for item in domains if item.get("drives_execution")]
    if drivers:
        return drivers[0]
    return domains[0] if domains else {"key": "general", "route_confidence": 0.0, "match_quality": "fallback"}


def _ambiguity(plan: dict[str, Any]) -> dict[str, Any]:
    domains = _domain_items(plan)
    drivers = [item for item in domains if item.get("drives_execution")]
    primary = drivers[0] if drivers else (domains[0] if domains else {})
    primary_quality = str(primary.get("match_quality") or "")
    primary_confidence = float(primary.get("route_confidence") or primary.get("confidence") or 0.0)
    primary_is_weak = (
        primary_quality in {"low_confidence", "ambiguous_fallback", "confidence_or_ambiguity_fallback"}
        or primary_confidence < 0.5
    )
    secondary_low_quality = [
        str(item.get("key") or "")
        for item in domains
        if item is not primary and str(item.get("match_quality") or "") in {"low_confidence", "ambiguous_fallback"}
    ]
    return {
        "is_ambiguous": len(drivers) != 1 or primary_is_weak,
        "driving_domains": [str(item.get("key") or "") for item in drivers],
        "low_quality_domains": [key for key in secondary_low_quality if key],
        "primary_is_weak": primary_is_weak,
        "resolution": (
            "use_primary_domain_with_bounded_owner_route_and_record_evidence"
            if len(drivers) == 1 and not primary_is_weak
            else "do_not_execute_stateful_work_until_owner_route_is_clear"
        ),
    }


def _first_enabled_phase(plan: dict[str, Any]) -> str:
    for phase in _as_list(plan.get("machine_phases")):
        if isinstance(phase, dict) and phase.get("enabled"):
            return str(phase.get("id") or "")
    return ""


def _memory_summary(plan: dict[str, Any]) -> dict[str, Any]:
    route = _as_dict(_as_dict(plan.get("memory")).get("route"))
    layers = []
    for layer in _as_list(route.get("layers"))[:4]:
        if not isinstance(layer, dict):
            continue
        layers.append(
            {
                "key": layer.get("key"),
                "action": layer.get("action"),
                "command": layer.get("command"),
                "verify": layer.get("verify"),
            }
        )
    return {
        "primary": route.get("primary"),
        "layers": layers,
        "rule": "use routed memory only; PMB seeds hypotheses and live state still needs owner verification",
    }


def _owner_routes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    tools = _as_dict(plan.get("tools"))
    intent_route = _as_dict(tools.get("intent_resource_route"))
    routes: list[dict[str, Any]] = []
    for item in _as_list(intent_route.get("owner_routes")):
        if not isinstance(item, dict):
            continue
        routes.append(
            {
                "resource": item.get("resource"),
                "owner_mcp": item.get("owner_mcp"),
                "read_tools_first": item.get("read_tools_first", [])[:5],
                "fallback_allowed_only_with": item.get("fallback_allowed_only_with", []),
                "write_tools_blocked_by_default": item.get("write_tools_blocked_by_default", [])[:5],
            }
        )
    return routes


def _intent_resource_route(plan: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(plan.get("tools")).get("intent_resource_route"))


def _enabled_tool_policies(plan: dict[str, Any]) -> list[dict[str, Any]]:
    tools = _as_dict(plan.get("tools"))
    policies: list[dict[str, Any]] = []
    for key in (
        "structured_state_policy",
        "codegraph_policy",
        "maintenance_upgrade_policy",
        "system_incident_policy",
        "self_update_policy",
        "network_policy",
        "external_docs_policy",
        "tool_utilization_policy",
    ):
        policy = _as_dict(tools.get(key))
        if not policy.get("enabled"):
            continue
        policies.append(
            {
                "key": key,
                "route_terms": policy.get("route_terms", [])[:6],
                "query_rule": policy.get("query_rule"),
                "principles": policy.get("principles", [])[:5],
                "evidence_required": policy.get("evidence_required", [])[:6],
                "validation": policy.get("validation"),
            }
        )
    return policies


def _source_owner_profile_from_message(plan: dict[str, Any]) -> tuple[str, str, str]:
    message = str(plan.get("message") or "").lower()
    if matched_terms(message, ("github", "repo", "repository", "issue", "pull request", "pr")):
        return "github", "search_repositories", "github_remote"
    if matched_terms(message, ("microsoft", "windows", "azure", "learn.microsoft", "microsoft docs", "微软")):
        return "microsoftdocs", "microsoft_docs_search", "external_docs_research"
    if matched_terms(message, ("context7", "sdk", "framework", "library", "package docs", "库", "框架")):
        return "context7", "resolve_library_id", "external_docs_research"
    return "source-owning-mcp", "", "external_docs_research"


def _primary_mcp_profile(plan: dict[str, Any], domain_keys: list[str]) -> tuple[str, str, str]:
    owner_routes = _owner_routes(plan)
    if owner_routes:
        route = owner_routes[0]
        owner = str(route.get("owner_mcp") or "").strip()
        if owner:
            read_tools = route.get("read_tools_first") if isinstance(route.get("read_tools_first"), list) else []
            return owner, str(read_tools[0] if read_tools else ""), str(route.get("resource") or "")
    message = str(plan.get("message") or "").lower()
    if term_matches(message, "codegraph") or matched_terms(message, ("代码结构", "call path", "blast radius", "symbol flow")):
        return "codegraph", "codegraph_explore", "code_structure"
    if "github" in domain_keys:
        return "github", "search_repositories", "github_remote"
    if "external_docs_research" in domain_keys:
        return _source_owner_profile_from_message(plan)
    if "gui_browser" in domain_keys:
        return "chrome-devtools", "snapshot", "browser_devtools"
    if "mcp_tools" in domain_keys:
        return "local-mcp-hub", "", "mcp_stability"
    return "", "", ""


def _route_decision(
    plan: dict[str, Any],
    domain_keys: list[str],
    resource_gate: dict[str, Any],
    tool_policies: list[dict[str, Any]],
    mcp_profile: str,
    mcp_tool: str,
    mcp_capability: str,
) -> dict[str, Any]:
    primary = _primary_domain(plan)
    ambiguity = _ambiguity(plan)
    structured_route = _as_dict(plan.get("structured_route"))
    task_contract = _as_dict(structured_route.get("task_contract"))
    task_contract_source = "structured_route_contract"
    if not task_contract:
        task_contract = resolve_task_route_contract(str(plan.get("message") or ""), domain_keys).to_dict()
        task_contract_source = "legacy_reclassification_fallback"
    system_change_gate = _as_dict(task_contract.get("system_change_gate"))
    task_facts = _as_dict(task_contract.get("task_facts"))
    matched_signals = _as_dict(task_contract.get("matched_signals"))
    fact_gates = [item for item in _as_list(task_contract.get("required_gates")) if isinstance(item, dict)]
    task_validation = [str(item) for item in _as_list(task_contract.get("validation")) if str(item).strip()]
    task_closeout = _as_dict(task_contract.get("closeout"))
    primary_domain_override = str(task_contract.get("primary_domain_override") or "")
    if primary_domain_override:
        primary = {
            "key": primary_domain_override,
            "route_confidence": 1.0,
            "confidence": 1.0,
            "match_quality": "explicit_contract",
        }
        ambiguity = {
            "is_ambiguous": False,
            "driving_domains": [primary_domain_override],
            "low_quality_domains": [],
            "primary_is_weak": False,
            "resolution": "explicit_task_contract",
        }
    external_gate = _as_dict(_as_dict(plan.get("tools")).get("execution_gate"))
    policy_keys = [str(item.get("key") or "") for item in tool_policies if isinstance(item, dict)]
    if task_contract.get("required_next_action"):
        next_action = str(task_contract.get("required_next_action"))
    elif resource_gate.get("enabled"):
        next_action = str(resource_gate.get("next_action") or "submit_resource_request_and_wait_for_receipt")
    elif "structured_state_policy" in policy_keys:
        next_action = "run_structured_state_read_only_query"
    elif "network_policy" in policy_keys:
        next_action = "request_network_route_plan"
    elif str(primary.get("key") or "") == "memory":
        next_action = "run_memory_governance_route_and_recall"
    elif mcp_profile:
        next_action = "follow_mcp_call_priority_chain"
    else:
        next_action = "execute_primary_workflow_phase"
    if ambiguity.get("is_ambiguous") and not resource_gate.get("enabled") and not mcp_profile:
        next_action = "clarify_or_run_read_only_route_probe"
    affinity = execution_affinity(mcp_profile, mcp_tool, mcp_capability)
    evidence_required = [
        "selected_primary_domain",
        "resource_request_id_and_receipt_when_resource_gate_enabled",
        "resource_task_progress_polled_until_terminal_receipt",
        "resource_receipt_status_completed_or_handoff_or_failed",
        "resource_completed_receipt_consumed_or_evaluated_when_consume_required",
        "selected_execution_affinity_and_session_binding",
        "same_boundary_hub_direct_before_complete_route_when_known",
        "validation_readback_or_receipt",
    ]
    if resource_gate.get("resource_layer_source_discovery_required"):
        evidence_required.insert(2, "resource_layer_source_discovery_receipt_for_external_lookup")
    stop_if = list(ALWAYS_STOP_IF)
    required_gates: list[dict[str, Any]] = [
        item
        for item in fact_gates
        if not (system_change_gate.get("triggered") and str(item.get("fact") or "") == "system_member_change")
    ]
    for gate in required_gates:
        fact = str(gate.get("fact") or "")
        if fact:
            evidence_required.append(f"task_fact_gate_completed:{fact}")
        stop_if.extend(str(item) for item in _as_list(gate.get("stop_if")) if str(item).strip())
    if system_change_gate.get("triggered"):
        required_gates.append(system_change_gate)
        evidence_required.extend(
            [
                "system_membership_pre_change_plan_consumed",
                "system_membership_post_change_impact_resolved",
                "system_membership_owner_validation_and_reload_boundary_recorded",
                "system_membership_closeout_reconciliation_receipt",
            ]
        )
        stop_if.extend(str(item) for item in system_change_gate.get("stop_if", []) if str(item).strip())

    policy_decisions = _policy_decisions(task_facts, matched_signals)

    return {
        "schema": "workflow_route_decision.v1",
        "decision_owner": "execution_route_pack",
        "task_contract_source": task_contract_source,
        "task_contract": task_contract,
        "task_mode": str(task_contract.get("task_mode") or "general"),
        "primary_owner": str(task_contract.get("business_owner") or ""),
        "evidence_owner": str(task_contract.get("evidence_owner") or ""),
        "task_facts": task_facts,
        "matched_signals": matched_signals,
        "primary_domain": str(primary.get("key") or "general"),
        "confidence": float(primary.get("route_confidence") or primary.get("confidence") or 0.0),
        "match_quality": str(primary.get("match_quality") or ""),
        "ambiguity": ambiguity,
        "required_next_action": next_action,
        "resource_delegation_required": bool(
            (resource_gate.get("enabled") and resource_gate.get("auto_fill_required"))
            or task_facts.get("external_network_read")
            or task_facts.get("resource_materialization")
            or task_facts.get("package_install")
        ),
        "resource_delegate_command": resource_gate.get("delegate_command_text") if resource_gate.get("enabled") else "",
        "resource_submit_command": resource_gate.get("primary_command_text") if resource_gate.get("enabled") else "",
        "resource_completion_contract": resource_gate.get("completion_contract", {}),
        "resource_task_lifecycle": resource_gate.get("task_lifecycle", {}),
        "owner_route": {
            "mcp_profile": mcp_profile,
            "tool": mcp_tool,
            "capability": mcp_capability,
            **affinity,
            "owner_profile": mcp_profile,
            "hub_tool": preferred_direct_hub_tool(mcp_profile, mcp_tool, mcp_capability),
            "native_tool": mcp_tool,
            "policy_keys": policy_keys,
            "direct_hub_tools": direct_hub_tools_for(mcp_profile, mcp_capability),
            "direct_hub_hints": direct_hub_hints_for(mcp_profile, mcp_capability),
            "complete_route_use_only_when": [
                "direct_hub_mapping_unknown",
                "permission_mapping_unclear",
                "schema_mapping_unclear",
                "diagnostic_route_evidence_required",
            ],
        },
        "mcp_priority_required": bool(mcp_profile),
        "generic_web": {
            "allowed": not bool(
                external_gate.get("generic_web_search_requires_owner_route")
                or task_facts.get("external_network_read")
            ),
            "requires_owner_route_first": bool(
                external_gate.get("generic_web_search_requires_owner_route")
                or task_facts.get("external_network_read")
            ),
            "fallback_reasons": resource_gate.get("fallback_reasons_for_generic_web", []),
        },
        "required_gates": required_gates,
        "policy_decisions": policy_decisions,
        "validation": task_validation,
        "closeout": task_closeout,
        "evidence_required": evidence_required,
        "stop_if": list(dict.fromkeys(stop_if)),
    }


def _resource_gate(plan: dict[str, Any], domain_keys: list[str]) -> dict[str, Any]:
    owner_routes = _owner_routes(plan)
    intent_route = _intent_resource_route(plan)
    layer_contract = _as_dict(intent_route.get("resource_layer_contract"))
    structured_route = _as_dict(plan.get("structured_route"))
    structured_resource = _as_dict(structured_route.get("resource_delegation"))
    has_structured_resource_decision = (
        structured_route.get("schema") == "workflow_structured_route.v1"
        and "required" in structured_resource
    )
    generic_web_gate = _as_dict(intent_route.get("generic_web_gate"))
    resource_task_class = str(layer_contract.get("task_class") or "")
    message = str(plan.get("message") or "").lower()
    resource_terms = (
        "获取",
        "下载",
        "安装",
        "文档",
        "url",
        "http://",
        "https://",
        "package",
        "install",
        "package install",
        "dependency",
        "choco",
        "chocolatey",
        "winget",
        "依赖",
    )
    governance_terms = (
        "资源层",
        "资源委托",
        "资源任务",
        "委托任务",
        "生命周期",
        "作业化",
        "命令门面",
        "测试",
        "治理",
        "优化",
        "机制",
        "resource layer",
        "resource delegation",
        "resource task",
        "job facade",
        "lifecycle",
    )
    is_resource_governance = bool(matched_terms(message, governance_terms)) and not bool(owner_routes)
    resource_signal = bool(matched_terms(message, resource_terms))
    package_install_signal = bool(
        matched_terms(
            message,
            (
            "安装",
            "install",
            "package install",
            "dependency install",
            "choco",
            "chocolatey",
            "winget",
            ),
        )
    )
    resource_contract_required = bool(
        structured_resource.get("required")
        if has_structured_resource_decision
        else layer_contract.get("required")
    )
    needs_resource_layer = resource_contract_required
    if not has_structured_resource_decision:
        needs_resource_layer = resource_contract_required or any(
            key in domain_keys
            for key in (
                "external_docs_research",
                "github",
            )
        ) or ("resource_acquisition" in domain_keys and not is_resource_governance) or (resource_signal and not is_resource_governance)
    codex_url_discovery_allowed = bool(layer_contract.get("codex_url_discovery_allowed"))
    resource_layer_source_selection_required = bool(layer_contract.get("resource_layer_source_selection_required"))
    resource_layer_source_discovery_required = bool(layer_contract.get("resource_layer_source_discovery_required"))
    candidate_review_before_materialization = bool(layer_contract.get("candidate_review_before_materialization"))
    materialization_requires_resource_layer = bool(layer_contract.get("materialization_requires_resource_layer"))
    install_requires_resource_layer = bool(layer_contract.get("install_requires_resource_layer"))
    direct_resource_delegation_preferred = bool(layer_contract.get("direct_resource_delegation_preferred", needs_resource_layer))
    next_action = "submit_resource_request_and_wait_for_receipt"
    delegate_target = str(plan.get("message") or "") if needs_resource_layer else ""
    package_metadata: dict[str, str] = {}
    if package_install_signal:
        package_metadata["intent"] = "package_dependency"
        package_metadata["package_action"] = "install"
        package_metadata["package_ecosystem"] = "windows_tool" if matched_terms(message, ("windows", "choco", "chocolatey", "winget", "aria2", "aria2c")) else ""
        if term_matches(message, "winget"):
            package_metadata["windows_package_manager"] = "winget"
        elif matched_terms(message, ("choco", "chocolatey")):
            package_metadata["windows_package_manager"] = "choco"
    delegate_payload_seed = {
        "task": plan.get("message") or "",
        "target": delegate_target,
        "url": "",
        "path": "",
        "name": "",
        "intent": "unknown",
        "need_materialization": False,
        "allow_network": True,
        "allow_filesystem_write": False,
        "auto_owner": True,
        "owner_execution_mode": "read_only",
        "validation_profile": "quick" if needs_resource_layer else (_as_dict(plan.get("execution_plan")).get("validation_tier") or "quick"),
        "runtime": "generic",
    }
    for key, value in package_metadata.items():
        if value:
            delegate_payload_seed[key] = value
    structured_request_seed = build_legacy_resource_envelope(
        task=str(delegate_payload_seed["task"]),
        target=delegate_target,
        url="",
        path="",
        resource_kind="",
        package_action=str(delegate_payload_seed.get("package_action") or ""),
        need_materialization=bool(materialization_requires_resource_layer),
        allow_network=True,
        allow_filesystem_write=bool(materialization_requires_resource_layer),
        install_approved=False,
        candidate_review=bool(candidate_review_before_materialization),
        destination_policy="user_resource_library" if materialization_requires_resource_layer else "resource_cache",
    )
    primary_command = [
        "python",
        r"_bridge\codex_workflow_entry.py",
        "resource",
        "custom",
        "--request-json",
        json.dumps(structured_request_seed, ensure_ascii=False, sort_keys=True),
        "--receipt-detail",
        "compact",
        "--json",
    ]
    delegate_command = [
        "python",
        r"_bridge\codex_workflow_entry.py",
        "resource",
        "delegate",
        "--task",
        str(delegate_payload_seed["task"]),
    ]
    if delegate_target:
        delegate_command.extend(["--target", delegate_target])
    delegate_command.extend(["--validation-profile", str(delegate_payload_seed["validation_profile"]), "--json"])
    if delegate_payload_seed.get("intent") == "package_dependency":
        delegate_command.extend(["--intent", "package_dependency"])
    if delegate_payload_seed.get("package_ecosystem"):
        delegate_command.extend(["--package-ecosystem", str(delegate_payload_seed["package_ecosystem"])])
    if delegate_payload_seed.get("package_action"):
        delegate_command.extend(["--package-action", str(delegate_payload_seed["package_action"])])
    if delegate_payload_seed.get("windows_package_manager"):
        delegate_command.extend(["--windows-package-manager", str(delegate_payload_seed["windows_package_manager"])])
    delegate_submit_command = [*delegate_command, "--submit"]
    job_run_command = [
        "python",
        r"_bridge\codex_workflow_entry.py",
        "resource",
        "job",
        "run",
        "--task",
        str(delegate_payload_seed["task"]),
    ]
    if delegate_target:
        job_run_command.extend(["--target", delegate_target])
    job_run_command.extend(["--validation-profile", str(delegate_payload_seed["validation_profile"]), "--receipt-detail", "compact", "--json"])
    if delegate_payload_seed.get("intent") == "package_dependency":
        job_run_command.extend(["--intent", "package_dependency"])
    if delegate_payload_seed.get("package_ecosystem"):
        job_run_command.extend(["--package-ecosystem", str(delegate_payload_seed["package_ecosystem"])])
    if delegate_payload_seed.get("package_action"):
        job_run_command.extend(["--package-action", str(delegate_payload_seed["package_action"])])
    if delegate_payload_seed.get("windows_package_manager"):
        job_run_command.extend(["--windows-package-manager", str(delegate_payload_seed["windows_package_manager"])])
    job_submit_command = [
        "python",
        r"_bridge\codex_workflow_entry.py",
        "resource",
        "job",
        "submit",
        "--task",
        str(delegate_payload_seed["task"]),
    ]
    if delegate_target:
        job_submit_command.extend(["--target", delegate_target])
    job_submit_command.extend(["--validation-profile", str(delegate_payload_seed["validation_profile"]), "--receipt-detail", "compact", "--json"])
    if delegate_payload_seed.get("intent") == "package_dependency":
        job_submit_command.extend(["--intent", "package_dependency"])
    if delegate_payload_seed.get("package_ecosystem"):
        job_submit_command.extend(["--package-ecosystem", str(delegate_payload_seed["package_ecosystem"])])
    if delegate_payload_seed.get("package_action"):
        job_submit_command.extend(["--package-action", str(delegate_payload_seed["package_action"])])
    if delegate_payload_seed.get("windows_package_manager"):
        job_submit_command.extend(["--windows-package-manager", str(delegate_payload_seed["windows_package_manager"])])
    return {
        "enabled": needs_resource_layer,
        "decision_source": "structured_route_contract" if has_structured_resource_decision else "legacy_projection_fallback",
        "auto_fill_required": needs_resource_layer,
        "entrypoint": "_bridge/codex_workflow_entry.py resource custom --request-json <structured_task_envelope> --json",
        "submit_entrypoint": "_bridge/codex_workflow_entry.py resource custom --request-json <structured_task_envelope> --mode submit --json",
        "structured_request_seed": structured_request_seed,
        "primary_command": primary_command,
        "primary_command_text": " ".join(json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part for part in primary_command),
        "delegate_payload_seed": delegate_payload_seed,
        "delegate_command": delegate_command,
        "delegate_command_text": " ".join(
            json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part
            for part in delegate_command
        ),
        "delegate_submit_command": delegate_submit_command,
        "delegate_submit_command_text": " ".join(
            json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part
            for part in delegate_submit_command
        ),
        "job_submit_command": job_submit_command,
        "job_submit_command_text": " ".join(
            json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part
            for part in job_submit_command
        ),
        "job_run_command": job_run_command,
        "job_run_command_text": " ".join(
            json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part
            for part in job_run_command
        ),
        "legacy_job_run_command": job_run_command,
        "legacy_delegate_submit_command": delegate_submit_command,
        "delegation_default": (
            "required_for_external_or_resource_acquisition"
            if bool(layer_contract.get("required"))
            else "use_when_resource_or_materialization_needed"
        ),
        "task_class": resource_task_class,
        "next_action": next_action,
        "codex_url_discovery_allowed": codex_url_discovery_allowed,
        "resource_layer_source_selection_required": resource_layer_source_selection_required,
        "resource_layer_source_discovery_required": resource_layer_source_discovery_required,
        "source_discovery_owner": str(layer_contract.get("source_discovery_owner") or ""),
        "source_discovery_scope": layer_contract.get("source_discovery_scope", []),
        "candidate_review_before_materialization": candidate_review_before_materialization,
        "candidate_review_owner": str(layer_contract.get("candidate_review_owner") or ""),
        "candidate_review_policy": _as_dict(layer_contract.get("candidate_review_policy")),
        "direct_resource_delegation_preferred": direct_resource_delegation_preferred,
        "materialization_requires_resource_layer": materialization_requires_resource_layer,
        "install_requires_resource_layer": install_requires_resource_layer,
        "url_discovery_contract": {
            "allowed": codex_url_discovery_allowed,
            "only_for_task_class": "legacy_explicit_codex_url_discovery",
            "codex_scope": "identify_concrete_source_url_and_basic_source_evidence",
            "must_not_do": ["download", "install", "filesystem_materialize", "archive"],
            "after_url_found": "submit_resource_layer_materialization_job_with_url",
            "research_only_rule": "delegate_directly_to_resource_layer_without_codex_url_discovery",
            "current_default": "resource_layer_performs_source_selection; codex direct URL discovery is not the default path",
        },
        "owner_selection_required": bool(layer_contract.get("owner_selection_required")),
        "generic_web_allowed": bool(generic_web_gate.get("generic_web_allowed")),
        "fallback_reasons_for_generic_web": generic_web_gate.get("allowed_reasons", []),
        "rule": "resource requests default to resource-layer ownership; unsuitable or deferred results should be retried by refining delegation parameters; failed or blocked results use the configured owner/Hub online route chain before any direct generic web; destructive state changes still need separate approval",
        "completion_contract": {
            "codex_must_submit_to_resource_layer": needs_resource_layer,
            "codex_waits_for_receipt": needs_resource_layer,
            "not_complete_on_delegate_payload_only": needs_resource_layer,
            "task_starts_at": "job_run",
            "task_ends_at": "resource_consumed_or_resource_layer_released",
            "url_discovery_phase_allowed": codex_url_discovery_allowed,
            "source_selection_owner": "resource_layer" if resource_layer_source_selection_required else "",
            "source_discovery_owner": str(layer_contract.get("source_discovery_owner") or ""),
            "source_discovery_required": resource_layer_source_discovery_required,
            "candidate_review_before_materialization": candidate_review_before_materialization,
            "candidate_review_owner": str(layer_contract.get("candidate_review_owner") or ""),
            "candidate_review_policy": _as_dict(layer_contract.get("candidate_review_policy")),
            "resource_layer_required_after_url_discovery": materialization_requires_resource_layer,
            "research_only_direct_resource_layer": resource_task_class == "research_only",
            "unsuitable_result_policy": _as_dict(layer_contract.get("unsuitable_result_policy")),
            "result_iteration_policy": _as_dict(layer_contract.get("result_iteration_policy")),
            "blocking_command": "python _bridge/codex_workflow_entry.py resource job run --task <task> --target <target> --json",
            "completed_status": "consume_resource_result_before_finishing",
            "completed_receipt_is_not_final": True,
            "consume_required_field": "consume_required",
            "required_consume_paths_field": "required_consume_paths",
            "consume_contract_field": "consume_contract",
            "consume_done_condition": "codex_reads_or_evaluates_one_required_consume_path_or_records_no_read_needed_reason",
            "handoff_required_status": "call_owner_tool_and_attach_result_to_same_request_id",
            "deferred_status": "refine_resource_delegation_and_retry",
            "failed_or_blocked_status": "use_configured_owner_hub_online_route_chain_before_direct_generic_web",
            "required_receipt_fields": ["request_id", "status", "route", "attempts", "next_action"],
            "required_job_fields": [
                "request_id",
                "status",
                "acquisition_owner",
                "ownership",
                "duplicate_fetch_policy",
                "resource_need_satisfied",
                "same_need_fetch_allowed",
                "same_need_independent_direct_fetch_allowed",
                "direct_generic_web_allowed",
                "refine_resource_delegation_required",
                "configured_online_route_chain_allowed",
                "consume_required",
                "required_consume_paths",
            ],
            "progress_command": "python _bridge/codex_workflow_entry.py resource job progress --request-id <request_id> --json",
            "status_command": "python _bridge/codex_workflow_entry.py resource job status --request-id <request_id> --json",
        },
        "ownership_contract": {
            "acquisition_owner": "resource_layer",
            "owned_need_scope": "same_resource_or_external_lookup_need",
            "ownership_starts_at": "job_run_process_start_or_background_submit_success",
            "ownership_ends_at": "completed_receipt_or_explicit_resource_layer_unavailable; deferred_requires_refinement; failed_blocked_requires_owner_hub_route_chain",
            "handoff_keeps_ownership": True,
            "duplicate_fetch_policy": {
                "same_need": "do_not_start_direct_fetch_while_resource_layer_owns_request",
                "allowed_actions": [
                    "wait_for_resource_receipt",
                    "poll_resource_progress",
                    "perform_explicit_handoff_action_for_same_request_id",
                    "refine_resource_delegation_after_deferred_or_insufficient_result",
                    "use_configured_owner_hub_online_route_chain_after_failed_or_blocked",
                    "surface_resource_layer_blocker",
                ],
                "direct_generic_web_allowed_only_with": [
                    "resource_layer_unavailable",
                    "predefined_online_route_exhausted",
                    "explicit_user_direct_web",
                ],
            },
        },
        "task_lifecycle": {
            "kind": "resource_delegate_task",
            "start_action": "submit_job_to_resource_layer",
            "running_state": "resource_layer_owns_acquisition_until_receipt",
            "codex_during_running": "consume_job_run_process_output_or_poll_progress; do_not_start_independent_replacement_fetch_for_same_need",
            "resource_layer_receipt_statuses": ["completed", "handoff_required", "failed", "blocked", "deferred"],
            "end_to_end_terminal_statuses": ["completed", "failed", "blocked", "deferred"],
            "completion_evidence": [
                "request_id",
                "receipt.status",
                "receipt.next_action",
                "resource_need_satisfied",
                "consume_required",
                "required_consume_paths_or_no_read_needed_reason",
                "codex_consumed_or_evaluated_resource",
            ],
            "handoff_continuation": "if handoff_required, Codex performs requested owner-tool action and attaches result back to same request_id",
            "completed_continuation": "if completed, Codex reads or evaluates required_consume_paths before treating the resource need as done",
            "deferred_continuation": "if deferred or insufficient coverage, Codex refines the resource request and resubmits through the resource layer",
            "failed_or_blocked_continuation": "if failed or blocked, Codex tries the configured owner/Hub online route chain and records route-chain exhaustion evidence before any direct generic web",
            "unsuitable_result_continuation": "if completed but unsuitable, Codex evaluates the receipt and submits a refined resource request instead of starting an independent direct fetch",
        },
        "owner_routes": owner_routes,
    }


def build_execution_route_pack(
    plan: dict[str, Any],
    *,
    environment_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact route contract for the next Codex action."""

    domain_keys = _domain_keys(plan)
    tools = _as_dict(plan.get("tools"))
    execution_gate = _as_dict(tools.get("execution_gate"))
    tool_policies = _enabled_tool_policies(plan)
    mcp_profile, mcp_tool, mcp_capability = _primary_mcp_profile(plan, domain_keys)
    resource_gate = _resource_gate(plan, domain_keys)
    call_priority = call_priority_pack(mcp_profile, mcp_tool, mcp_capability) if mcp_profile else call_priority_pack("", "", "")
    route_decision = _route_decision(
        plan,
        domain_keys,
        resource_gate,
        tool_policies,
        mcp_profile,
        mcp_tool,
        mcp_capability,
    )
    automation_decision = automation_delegation_decision(
        task_facts=_as_dict(route_decision.get("task_facts")),
        owner_route=_as_dict(route_decision.get("owner_route")),
        required_gates=[item for item in _as_list(route_decision.get("required_gates")) if isinstance(item, dict)],
        machine_phases=[item for item in _as_list(plan.get("machine_phases")) if isinstance(item, dict)],
        declared_inputs={
            "domain_keys": domain_keys,
            "owner_route": route_decision.get("owner_route", {}),
            "task_mode": route_decision.get("task_mode", "general"),
            "resource_delegation_required": route_decision.get("resource_delegation_required", False),
        },
        risk=str(plan.get("risk") or "unknown"),
        ambiguous=bool(_as_dict(route_decision.get("ambiguity")).get("is_ambiguous")),
        resource_required=bool(route_decision.get("resource_delegation_required")),
    )
    route_pack = {
        "schema": "execution_route_pack.v1",
        "ok": bool(plan.get("ok")),
        "next_phase": _first_enabled_phase(plan),
        "domain_keys": domain_keys,
        "environment_context": _as_dict(environment_context),
        "memory": _memory_summary(plan),
        "automation_delegation": compact_automation_delegation_policy(),
        "automation_decision": automation_decision,
        "resource_gate": resource_gate,
        "tool_policies": tool_policies,
        "mcp_boundary": {
            "matrix": tools.get("matrix"),
            "lookup_terms": tools.get("lookup_terms", [])[:8],
            "rule": tools.get("rule"),
            "fallback_policy": route_policy(),
            "call_priority": call_priority,
            "direct_hub_tools": call_priority.get("direct_hub_tools", []),
            "direct_hub_hints": call_priority.get("direct_hub_hints", []),
            "direct_hub_options": common_direct_hub_options(),
            "complete_route_boundary": call_priority.get("complete_route_boundary", {}),
        },
        "route_decision": route_decision,
        "external_research_gate": {
            "generic_web_search_requires_owner_route": bool(
                execution_gate.get("generic_web_search_requires_owner_route")
            ),
            "generic_web_first_violation": bool(execution_gate.get("generic_web_first_violation")),
            "rule": execution_gate.get("rule"),
        },
        "validation": {
            "quick": _as_dict(plan.get("validation_tiers")).get("quick", [])[:4],
            "chosen_tier": _as_dict(plan.get("execution_plan")).get("validation_tier"),
        },
        "stop_if": list(ALWAYS_STOP_IF),
    }
    if any(policy.get("key") == "network_policy" for policy in tool_policies):
        route_pack["network_gate"] = {
            "entrypoint": "_bridge/codex_network_gateway.py plan",
            "rule": "networked resource/package/docs/GitHub work should consume route advice before execution",
        }
    else:
        route_pack["network_gate"] = {"entrypoint": "", "rule": "not routed for this task"}
    return route_pack
