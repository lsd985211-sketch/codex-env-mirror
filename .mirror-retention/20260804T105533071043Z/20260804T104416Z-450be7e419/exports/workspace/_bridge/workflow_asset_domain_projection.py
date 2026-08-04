#!/usr/bin/env python3
"""Project domain-specific references into workflow asset guidance.

Ownership: pure admission-time projection over the already selected business
owner, skills, maintenance owners, tools, and task facts.
Non-goals: an asset catalog, capability discovery, owner execution, permission
decisions, lifecycle state, or result-consumption recording.
State behavior: read-only and deterministic; every item is a stable reference
to an existing authority and carries no copied owner contract.
Caller context: workflow_asset_guidance uses this after generic route selection
to make the smallest domain handoff visible to Codex.
"""

from __future__ import annotations

from typing import Any


SCHEMA = "workflow_asset_domain_projection.v1"


def _names(values: list[dict[str, Any]]) -> set[str]:
    return {str(value.get("name") or "").strip() for value in values if isinstance(value, dict)}


def _asset(
    name: str,
    role: str,
    authority_ref: str,
    action_ref: str,
    acceptance_ref: str,
) -> dict[str, str]:
    return {
        "name": name,
        "role": role,
        "authority_ref": authority_ref,
        "action_ref": action_ref,
        "acceptance_ref": acceptance_ref,
    }


def _scenario(
    message: str,
    primary_owner: str,
    skills: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    task_facts: dict[str, Any],
) -> str:
    text = str(message or "").casefold()
    skill_names = _names(skills)
    tool_names = _names(tools)
    if primary_owner == "windows_execution_agent":
        return "windows_desktop"
    if primary_owner.startswith("skill:") and (
        "baoyu-slide-deck" in skill_names
        or any(term in text for term in ("演示文稿", "课件", "presentation", "slide deck"))
    ):
        return "content_production"
    if primary_owner == "codex_code_review_judgment":
        return "code_maintenance"
    if primary_owner in {"maintenance_upgrade_governance", "workflow_governance"}:
        return "system_maintenance"
    if primary_owner == "resource_broker" or "resource-layer" in tool_names:
        return "external_research"
    return ""


def _domain_assets(scenario: str, skills: list[dict[str, Any]]) -> list[dict[str, str]]:
    skill_names = sorted(_names(skills))
    selected_skill = skill_names[0] if skill_names else "selected-domain-skill"
    if scenario == "code_maintenance":
        return [
            _asset("module-asset-catalog", "reuse_discovery", "code_maintainability.module-assets", "python _bridge/code_maintainability.py module-assets --task-mode code --term <task>", "selected module boundary and reuse target are consumed before placement"),
            _asset("code-structure-route", "structure_discovery", "mcp_capability_routes.lookup:code_structure", "Hub codegraph.explore through the configured forward route", "bounded symbol or impact evidence answers the source question"),
            _asset("maintenance-capability-registry", "owner_discovery", "maintenance_capability_registry.query", "python _bridge/maintenance_capability_registry.py query --term <task>", "selected maintenance owner remains the lifecycle authority"),
            _asset("target-placement-validator", "target_validation", "code_maintainability.placement-plan", "python _bridge/code_maintainability.py placement-plan --message <task> --target <path>", "target owner validator and focused regression pass"),
        ]
    if scenario == "system_maintenance":
        return [
            _asset("maintenance-capability-registry", "owner_discovery", "maintenance_capability_registry.query", "python _bridge/maintenance_capability_registry.py query --term <task>", "owner health or repair result satisfies the caller predicate"),
            _asset("system-membership-contract", "system_boundary", "system_membership.snapshot", "read the selected system contract and health command", "system owner accepts the maintenance result"),
        ]
    if scenario == "external_research":
        return [
            _asset("research-source-classification", "source_strategy", "skill:agent-reach", "classify the source before choosing the acquisition route", "claims retain source and freshness evidence"),
            _asset("structured-resource-request", "resource_owner", "execution_route_pack.resource_gate", "submit or reuse one resource request", "resource result is attached to the same request_id"),
            _asset("network-and-owner-mcp-route", "transport_support", "codex_network_gateway + mcp_capability_routes", "use the gateway-selected route and configured Owner MCP", "transport success is followed by content relevance acceptance"),
            _asset("resource-result-consumption", "result_acceptance", "resource_cli.job.consume", "attach-result then consume on the same request_id", "material claims are cited and the caller records consumption"),
        ]
    if scenario == "windows_desktop":
        return [
            _asset("windows-execution-plane", "platform_owner", "system_membership:wsl_workspace.windows_execution_agent", "hand the fixed operation to the Windows execution owner", "Windows business owner accepts the typed result"),
            _asset("gui-application-skill", "application_guidance", f"skill:{selected_skill}", "read the selected application skill before live interaction", "the application-specific readback predicate is satisfied"),
            _asset("current-session-gui-owner", "runtime_evidence", "mcp_capability_routes:gui_automation", "use the classified current-session GUI owner", "visible or machine-readable runtime evidence is captured"),
            _asset("platform-readback", "result_acceptance", "windows_execution_agent.result_contract", "return the owner receipt and live-state readback", "caller consumes both operation and readback evidence"),
        ]
    if scenario == "content_production":
        return [
            _asset("domain-execution-skill", "business_owner", f"skill:{selected_skill}", "run the selected skill workflow", "the skill output contract is satisfied"),
            _asset("skill-templates-and-materials", "production_inputs", f"skill:{selected_skill}#references", "load only the selected style, template, or material reference", "inputs are traceable to the generated artifact"),
            _asset("presentation-artifact-owner", "artifact_owner", f"skill:{selected_skill}#Output Contract", "preserve editable source and generated PPTX/PDF", "output path, format, and slide count are reported"),
            _asset("target-application-validation", "domain_validation", f"skill:{selected_skill}#windows-powerpoint-validation", "open and render in the target presentation application", "complete contact sheet and representative text-heavy slides pass"),
        ]
    return []


def build_domain_projection(
    message: str,
    *,
    primary_owner: str,
    skills: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    task_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return federated domain references and remove incompatible support."""

    facts = task_facts if isinstance(task_facts, dict) else {}
    scenario = _scenario(message, primary_owner, skills, tools, facts)
    filtered_tools = list(tools)
    excluded: list[dict[str, str]] = []
    if scenario == "windows_desktop":
        kept: list[dict[str, Any]] = []
        for tool in filtered_tools:
            name = str(tool.get("name") or "")
            if name in {"microsoftdocs", "context7", "openai-docs"}:
                excluded.append({"name": name, "reason": "documentation support cannot prove live Windows desktop state"})
                continue
            kept.append(tool)
        filtered_tools = kept
    assets = _domain_assets(scenario, skills)
    return {
        "schema": SCHEMA,
        "active": bool(assets),
        "scenario": scenario,
        "authority_mode": "federated_references_only",
        "assets": assets[:4],
        "filtered_tools": filtered_tools,
        "excluded_support": excluded,
        "acceptance": {
            "predicate": assets[-1]["acceptance_ref"] if assets else "",
            "owner_result_required": bool(assets),
            "selection_is_not_completion": True,
        },
        "expand": [asset["authority_ref"] for asset in assets[:4]],
    }
