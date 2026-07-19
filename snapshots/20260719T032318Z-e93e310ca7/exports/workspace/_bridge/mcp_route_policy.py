#!/usr/bin/env python3
"""Classified MCP execution-affinity policy shared by workflow and doctors.

Ownership: capability-specific Hub/native/owner execution affinity and fallback.
Non-goals: executing MCP tools, probing transports, or mutating observations.
State behavior: stateless, read-only policy projection.
Caller context: workflow route packs, MCP session doctor contract checks, and
closeout validation that must distinguish Hub MCP from local Hub/CLI fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_execution_priority import (
    MOBILE_SESSION_TOOLS,
    PRIORITY_REGRESSION_MATRIX,
    PROFILE_PRIORITIES,
    resolve_execution_priority,
    validate as validate_execution_priority,
)


ROUTE_SEQUENCE: tuple[dict[str, Any], ...] = (
    {
        "id": "precise_tool_discovery",
        "label": "Precise Tool Discovery",
        "role": "entry stage for native-first profiles and evidence before declaring a native tool unbound",
        "transport": "codex_tool_search",
    },
    {
        "id": "native_mcp",
        "label": "Native MCP",
        "role": "native current-turn call stage",
        "transport": "codex_current_turn_mcp",
    },
    {
        "id": "hub_mcp_direct",
        "label": "Hub MCP direct tool",
        "role": "entry stage for Hub-first profiles and fallback after native failure",
        "transport": "codex_current_turn_local_http_mcp_hub",
    },
    {
        "id": "hub_mcp_gateway",
        "label": "Hub MCP gateway",
        "role": "diagnostic or dynamic route resolution when direct Hub mapping is unknown or ambiguous",
        "transport": "codex_current_turn_local_http_mcp_hub",
    },
    {
        "id": "local_hub_cli",
        "label": "Local Hub CLI/Python",
        "role": "local same-boundary continuity only after Hub MCP is unavailable or insufficient",
        "transport": "local_process",
    },
    {
        "id": "owner_cli",
        "label": "Owner CLI fallback",
        "role": "profile-specific local fallback after Hub/local Hub cannot complete the route",
        "transport": "local_process",
    },
    {
        "id": "terminal_local_read",
        "label": "Terminal local read",
        "role": "bottom fallback such as targeted rg/read only after same-boundary owner routes are unavailable or insufficient",
        "transport": "local_process",
    },
)


def execution_affinity(profile: str, tool: str = "", capability: str = "") -> dict[str, Any]:
    """Classify execution affinity without expanding the target permission boundary."""

    return resolve_execution_priority(profile, tool, capability)


def route_policy() -> dict[str, Any]:
    return {
        "schema": "mcp_route_policy.v1",
        "ok": True,
        "sequence": list(ROUTE_SEQUENCE),
        "sequence_role": "fixed_forward_fallback_sequence; execution_affinity selects the entry stage and never reorders later stages",
        "canonical_order": [item["id"] for item in ROUTE_SEQUENCE],
        "profile_priorities": PROFILE_PRIORITIES,
        "profile_priority_count": len(PROFILE_PRIORITIES),
        "rules": {
            "precise_tool_discovery_before_unbound": True,
            "classified_affinity": True,
            "specific_affinity_overrides_generic_guidance": True,
            "affinity_lookup_before_first_call": True,
            "fixed_forward_fallback_sequence": True,
            "failure_continues_without_backward_jump": True,
            "unclassified_never_defaults_native": True,
            "hub_first_must_not_probe_native_first": True,
            "stateless_owner_services_hub_first": True,
            "session_bound_tools_native_first": True,
            "native_first": False,
            "native_unbound_requires_precise_discovery_evidence": True,
            "record_native_negative_before_fallback": True,
            "hub_mcp_before_local_hub": True,
            "direct_known_hub_tool_before_complete_route": True,
            "complete_route_is_diagnostic_or_dynamic_not_default_transit": True,
            "local_hub_cli_only_after_hub_mcp_unavailable_or_insufficient": True,
            "terminal_local_read_only_after_same_boundary_routes": True,
            "terminal_local_read_requires_route_exhaustion_evidence": True,
            "permission_boundary": "same_as_native_tool",
            "positive_current_turn_requires_real_native_call": True,
        },
    }


KNOWN_TOOL_DISCOVERY_QUERIES: dict[str, str] = {
    "github": "github get_me search_repositories search_issues search_pull_requests repo issue pull request",
    "context7": "context7 resolve_library_id query_docs documentation library framework SDK API",
    "microsoftdocs": "microsoftdocs microsoft_docs_search microsoft_docs_fetch Microsoft Learn docs",
    "openai-docs": "openai-docs openaiDeveloperDocs search_openai_docs fetch_openai_doc official OpenAI Codex API docs",
    "chrome-devtools": "chrome-devtools list_pages navigate_page take_snapshot evaluate_script screenshot",
    "playwright": "playwright browser tabs page screenshot navigate read-only",
    "local-pmb-memory": "local-pmb-memory prepare recall project_structure memory MCP",
    "myskills": "myskills skills_inventory skills_read discover_search authoring_draft MCP",
    "mobile-openclaw-bridge": "mobile-openclaw-bridge get_pending_batch ack_message bridge_health MCP",
    "filesystem": "filesystem read_file list_directory search_files MCP",
    "filesystem-admin": "filesystem-admin read_file list_directory write_file edit_file MCP",
    "codegraph": "codegraph_explore codegraph source symbol call graph MCP",
}


KNOWN_DIRECT_HUB_TOOLS: dict[str, list[str]] = {
    "github": ["github.api", "github.gh"],
    "github_remote": ["github.api", "github.gh"],
    "context7": ["owner_mcp.call_readonly"],
    "microsoftdocs": ["owner_mcp.call_readonly"],
    "openai-docs": ["owner_mcp.call_readonly"],
    "microsoft-docs": ["owner_mcp.call_readonly"],
    "openaiDeveloperDocs": ["owner_mcp.call_readonly"],
    "filesystem": ["owner_mcp.call_readonly"],
    "filesystem-admin": ["owner_mcp.call_readonly"],
    "markitdown": ["owner_mcp.call_readonly"],
    "network": ["network_gateway.plan", "network_gateway.env", "network.plan", "network.probe"],
    "network_routing": ["network_gateway.plan", "network_gateway.env", "network.plan", "network.probe", "network.probe_suite", "network.validate"],
    "resource": ["resource.request", "resource.status", "resource.progress", "resource.attach_result"],
    "resource_acquisition": ["resource.request", "resource.status", "resource.progress", "resource.attach_result"],
    "workflow": ["workflow.route_pack"],
    "local-mcp-hub": ["hub.capabilities", "hub.validate", "mcp_session.validate", "mcp_session.recover_plan"],
    "mcp_stability": ["hub.capabilities", "hub.validate", "mcp_session.validate", "mcp_session.recover_plan"],
    "pmb": ["pmb.workspace_info", "pmb.prepare", "pmb.recall", "pmb.project_overview", "pmb.stats", "pmb.list_goals"],
    "local-pmb-memory": ["pmb.workspace_info", "pmb.prepare", "pmb.recall", "pmb.project_overview", "pmb.stats", "pmb.list_goals"],
    "memory_router": ["pmb.prepare", "pmb.recall", "pmb.project_overview", "pmb.list_goals"],
    "codegraph": ["codegraph.explore"],
    "custom-slash-commands": ["slash.list_commands", "slash.get_command", "slash.render_command", "slash.validate_registry"],
    "myskills": ["owner_mcp.call_readonly"],
    "sqlite-scratch": ["sqlite_scratch_query", "sqlite_scratch_schema", "sqlite_scratch_tables", "sqlite_scratch_health"],
    "sqlite-bridge-ro": ["sqlite_bridge_query", "sqlite_bridge_schema", "sqlite_bridge_tables", "sqlite_bridge_health"],
    "local-mcp-hub-record-store": ["record_store_query", "record_store_schema", "record_store_tables", "record_store_health"],
    "local-mcp-hub-email-state": ["email_state_query", "email_state_schema", "email_state_tables", "email_state_health"],
    "sqlite_state": ["sqlite_bridge_query", "sqlite_scratch_query", "record_store_query", "email_state_query"],
    "chrome-devtools": [
        "chrome_devtools.list_pages",
        "chrome_devtools.navigate_page",
        "chrome_devtools.take_snapshot",
        "chrome_devtools.evaluate_script",
    ],
    "desktop-weixin": ["desktop_weixin.capabilities", "desktop_weixin.status"],
    "mobile-openclaw-bridge": ["mobile_bridge.get_pending_batch", "mobile_bridge.ack_message"],
    "mobile_bridge": ["mobile_bridge.get_pending_batch", "mobile_bridge.ack_message"],
}

DIRECT_HUB_TOOL_BY_NATIVE: dict[tuple[str, str], str] = {
    ("local-pmb-memory", "workspace_info"): "pmb.workspace_info",
    ("local-pmb-memory", "prepare"): "pmb.prepare",
    ("local-pmb-memory", "recall"): "pmb.recall",
    ("local-pmb-memory", "project_overview"): "pmb.project_overview",
    ("local-pmb-memory", "stats"): "pmb.stats",
    ("local-pmb-memory", "list_goals"): "pmb.list_goals",
    ("codegraph", "codegraph_explore"): "codegraph.explore",
    ("custom-slash-commands", "slash_list_commands"): "slash.list_commands",
    ("custom-slash-commands", "slash_get_command"): "slash.get_command",
    ("custom-slash-commands", "slash_render_command"): "slash.render_command",
    ("custom-slash-commands", "slash_validate_registry"): "slash.validate_registry",
    ("sqlite-scratch", "sqlite_query"): "sqlite_scratch_query",
    ("sqlite-scratch", "sqlite_schema"): "sqlite_scratch_schema",
    ("sqlite-scratch", "sqlite_tables"): "sqlite_scratch_tables",
    ("sqlite-scratch", "sqlite_health"): "sqlite_scratch_health",
    ("sqlite-bridge-ro", "sqlite_query"): "sqlite_bridge_query",
    ("sqlite-bridge-ro", "sqlite_schema"): "sqlite_bridge_schema",
    ("sqlite-bridge-ro", "sqlite_tables"): "sqlite_bridge_tables",
    ("sqlite-bridge-ro", "sqlite_health"): "sqlite_bridge_health",
}

DIRECT_HUB_CALL_HINTS: dict[str, dict[str, Any]] = {
    "context7": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "context7",
        "typical_tools": ["resolve-library-id", "get-library-docs", "resolve_library_id", "query_docs"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "profile and target tool are known; complete_route is only for unknown, ambiguous, or diagnostic routing",
    },
    "microsoftdocs": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "microsoftdocs",
        "typical_tools": ["microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "profile and target tool are known; complete_route is only for unknown, ambiguous, or diagnostic routing",
    },
    "openai-docs": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "openai-docs",
        "typical_tools": ["search_openai_docs", "fetch_openai_doc", "list_openai_docs", "get_openapi_spec"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "official OpenAI documentation profile and read-only tool are explicit",
    },
    "filesystem": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "filesystem",
        "typical_tools": ["read_text_file", "read_multiple_files", "list_directory", "directory_tree", "search_files", "get_file_info"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "read-only filesystem profile and tool are explicit",
    },
    "filesystem-admin": {
        "hub_tool": "owner_mcp.call_readonly|mcp_gateway.call",
        "profile": "filesystem-admin",
        "typical_tools": ["read_text_file", "read_multiple_files", "list_directory", "directory_tree", "search_files", "write_file", "edit_file", "move_file", "create_directory"],
        "requires": ["target_tool_name", "arguments", "Hub acknowledgement matching read-only or gateway mode"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions for reads; native-mcp-unavailable-and-original-permissions-apply for gateway mutations",
        "why_not_complete_route": "known reads use the direct owner adapter; authorized mutations use the explicit same-boundary gateway call",
    },
    "markitdown": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "markitdown",
        "typical_tools": ["convert_to_markdown"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "read-only conversion profile and tool are explicit",
    },
    "myskills": {
        "hub_tool": "owner_mcp.call_readonly",
        "profile": "myskills",
        "typical_tools": ["skills_inventory", "skills_read", "skills_history", "scenarios_list", "discover_search"],
        "requires": ["target_tool_name", "arguments", "hub_ack"],
        "hub_ack": "hub-readonly-owner-call-preserves-original-permissions",
        "why_not_complete_route": "inventory and read tools are explicitly allowlisted; writes retain native approval contracts",
    },
    "github": {
        "hub_tool": "github.api|github.gh",
        "profile": "github",
        "typical_tools": ["github.api", "github.gh"],
        "requires": ["REST path/method or gh args", "same credential boundary"],
        "why_not_complete_route": "Hub has profile-specific GitHub continuity tools",
    },
    "mobile-openclaw-bridge": {
        "hub_tool": "mobile_bridge.get_pending_batch|mobile_bridge.ack_message",
        "profile": "mobile-openclaw-bridge",
        "typical_tools": ["mobile_bridge.get_pending_batch", "mobile_bridge.ack_message"],
        "requires": ["thread_id", "fallback_ack", "message_id for ack"],
        "fallback_ack": "native-mcp-unavailable-and-original-permissions-apply",
        "why_not_complete_route": "Hub has direct supplement tools for the known mobile bridge calls",
    },
    "resource_acquisition": {
        "hub_tool": "resource.request|resource.status|resource.progress|resource.attach_result",
        "profile": "resource",
        "typical_tools": ["resource.request", "resource.status", "resource.progress", "resource.attach_result"],
        "requires": ["resource request or request_id"],
        "why_not_complete_route": "resource layer is a Hub-native capability, not a dynamic MCP fallback",
    },
    "codegraph": {
        "hub_tool": "codegraph.explore",
        "profile": "codegraph",
        "typical_tools": ["codegraph.explore"],
        "requires": ["query", "freshness_targets when local files are explicit"],
        "why_not_complete_route": "the codegraph Hub alias is a known same-boundary continuity tool before bottom local reads",
    },
}


def precise_tool_discovery_query(profile: str, tool: str = "") -> str:
    """Return a compact discovery query for exposing exact native MCP tools."""

    profile_key = str(profile or "").strip().lower()
    base = KNOWN_TOOL_DISCOVERY_QUERIES.get(profile_key, profile_key)
    target = str(tool or "").strip()
    return " ".join(part for part in (base, target) if part).strip()


def direct_hub_tools_for(profile: str, capability: str = "") -> list[str]:
    """Return same-boundary Hub direct tools known before complete_route."""

    keys = [str(profile or "").strip().lower(), str(capability or "").strip().lower()]
    output: list[str] = []
    for key in keys:
        for tool in KNOWN_DIRECT_HUB_TOOLS.get(key, []):
            if tool not in output:
                output.append(tool)
    return output


def preferred_direct_hub_tool(profile: str, tool: str = "", capability: str = "") -> str:
    if str(profile or "").strip().lower() == "mobile-openclaw-bridge" and str(tool or "").strip().lower() not in MOBILE_SESSION_TOOLS:
        return ""
    profile_key = str(profile or "").strip().lower()
    target = str(tool or "").strip().lower().replace("-", "_")
    explicit = DIRECT_HUB_TOOL_BY_NATIVE.get((profile_key, target))
    if explicit:
        return explicit
    tools = direct_hub_tools_for(profile, capability)
    if target:
        for candidate in tools:
            leaf = candidate.rsplit(".", 1)[-1].lower().replace("-", "_")
            if leaf == target:
                return candidate
    return tools[0] if tools else ""


def direct_hub_hints_for(profile: str, capability: str = "") -> list[dict[str, Any]]:
    keys = [str(profile or "").strip().lower(), str(capability or "").strip().lower()]
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        hint = DIRECT_HUB_CALL_HINTS.get(key)
        if not hint:
            continue
        marker = str(hint.get("hub_tool") or key)
        if marker in seen:
            continue
        seen.add(marker)
        hints.append(dict(hint))
    return hints


def common_direct_hub_options() -> dict[str, list[str]]:
    return {
        "github": list(KNOWN_DIRECT_HUB_TOOLS["github"]),
        "context7": list(KNOWN_DIRECT_HUB_TOOLS["context7"]),
        "microsoftdocs": list(KNOWN_DIRECT_HUB_TOOLS["microsoftdocs"]),
        "mobile-openclaw-bridge": list(KNOWN_DIRECT_HUB_TOOLS["mobile-openclaw-bridge"]),
        "resource_acquisition": list(KNOWN_DIRECT_HUB_TOOLS["resource_acquisition"]),
        "codegraph": list(KNOWN_DIRECT_HUB_TOOLS["codegraph"]),
        "filesystem": list(KNOWN_DIRECT_HUB_TOOLS["filesystem"]),
        "filesystem-admin": list(KNOWN_DIRECT_HUB_TOOLS["filesystem-admin"]),
        "markitdown": list(KNOWN_DIRECT_HUB_TOOLS["markitdown"]),
    }


def call_priority_pack(profile: str, tool: str = "", capability: str = "") -> dict[str, Any]:
    """Machine-readable MCP call priority chain for workflow route packs."""

    affinity = execution_affinity(profile, tool, capability)
    direct_hub_tools = direct_hub_tools_for(profile, capability)
    direct_hub_hints = direct_hub_hints_for(profile, capability)
    preferred_hub_tool = preferred_direct_hub_tool(profile, tool, capability)
    if affinity.get("execution_affinity") == "native_first" and affinity.get("priority_source") == "tool_override":
        direct_hub_tools = []
        direct_hub_hints = []
        preferred_hub_tool = ""
    if preferred_hub_tool and preferred_hub_tool in direct_hub_tools:
        direct_hub_tools = [preferred_hub_tool, *[item for item in direct_hub_tools if item != preferred_hub_tool]]
    if str(profile or "").strip().lower() == "mobile-openclaw-bridge" and str(tool or "").strip().lower() not in MOBILE_SESSION_TOOLS and str(tool or "").strip():
        direct_hub_tools = []
        direct_hub_hints = []
    hub_step = {
        "id": "hub_mcp_direct",
        "action": "call_known_same_boundary_hub_tool",
        "tools": direct_hub_tools,
        "preferred_tool": preferred_hub_tool,
        "skip_allowed_only_if": [
            "no_known_same_boundary_hub_tool",
            "hub_mcp_not_exposed_in_current_turn",
            "hub_mcp_transport_closed",
            "known_direct_hub_tool_insufficient_for_target_schema",
        ],
    }
    native_discovery = {
        "id": "precise_tool_discovery",
        "action": "tool_search",
        "query": precise_tool_discovery_query(profile, tool),
        "success": "target native tool namespace is exposed in current turn",
        "failure": "record tool_unbound only after this exact discovery query is insufficient",
    }
    native_step = {
        "id": "native_mcp",
        "action": "call_native_tool",
        "success": "current_turn_callable and call_completed evidence",
        "failure": "record current-turn negative observation before fallback",
    }
    gateway_step = {
        "id": "hub_mcp_gateway",
        "action": "mcp_gateway.complete_route",
        "use_only_when": (["execution_priority_unclassified"] if affinity["execution_affinity"] == "unclassified" else [
            "direct_hub_mapping_unknown",
            "permission_mapping_unclear",
            "schema_mapping_unclear",
            "diagnostic_route_evidence_required",
        ]),
    }
    canonical_steps = [
        native_discovery,
        native_step,
        hub_step,
        gateway_step,
        {
            "id": "local_hub_cli",
            "action": "local_hub_or_doctor_cli",
            "use_only_after": "Hub MCP direct and gateway are unavailable or insufficient",
        },
        {
            "id": "owner_cli",
            "action": "same-boundary profile-specific owner CLI",
            "use_only_after": "native, Hub direct, Hub gateway, and local Hub routes cannot complete",
        },
        {
            "id": "terminal_local_read",
            "action": "targeted rg/read or other bottom local structure read",
            "use_only_after": "precise discovery, native current-turn call, Hub direct/gateway, local Hub, and owner CLI are unavailable, insufficient, or explicitly inapplicable",
            "requires_evidence": [
                "native_negative_observation_or_tool_not_exposed",
                "hub_direct_or_gateway_attempt_or_skip_reason",
                "local_hub_or_owner_cli_attempt_or_skip_reason",
            ],
        },
    ]
    entry_step_by_affinity = {
        "native_first": "precise_tool_discovery",
        "session_native_first": "precise_tool_discovery",
        "hub_first": "hub_mcp_direct",
        "owner_cli_first": "owner_cli",
        "unclassified": "hub_mcp_gateway",
    }
    required_first_step = entry_step_by_affinity[affinity["execution_affinity"]]
    if (
        affinity.get("execution_affinity") == "hub_first"
        and affinity.get("priority_reason") == "hub_managed_profile_uses_fresh_stdio_gateway_for_full_tool_surface"
    ):
        required_first_step = "hub_mcp_gateway"
    entry_index = next(index for index, step in enumerate(canonical_steps) if step["id"] == required_first_step)
    steps = canonical_steps[entry_index:]
    steps = [
        {
            **step,
            "on_failure_next_step": steps[index + 1]["id"] if index + 1 < len(steps) else "terminal_failure",
            "continue_on": ["transport_closed", "tool_unbound", "unsupported_call", "insufficient_result", "dispatch_failure"],
            "stop_on": ["call_completed", "explicit_policy_block", "permission_boundary_violation"],
        }
        for index, step in enumerate(steps)
    ]
    return {
        "schema": "mcp_call_priority.v1",
        "profile": profile,
        "tool": tool,
        "capability": capability,
        **affinity,
        "required_first_step": required_first_step,
        "affinity_decision_required_before_call": True,
        "direct_hub_tools": direct_hub_tools,
        "preferred_direct_hub_tool": preferred_hub_tool,
        "direct_hub_hints": direct_hub_hints,
        "complete_route_boundary": {
            "use_direct_hub_first_when_tools_present": bool(direct_hub_tools),
            "complete_route_use_only_when": [
                "direct_hub_mapping_unknown",
                "permission_mapping_unclear",
                "schema_mapping_unclear",
                "diagnostic_route_evidence_required",
            ],
        },
        "steps": steps,
        "continuation_policy": {
            "follow_chain_until": ["call_completed", "explicit_policy_block", "permission_boundary_violation", "terminal_failure"],
            "hub_failure_does_not_release_chain": True,
            "do_not_stop_after_first_transport_failure": True,
            "direction": "forward_only_from_selected_entry_stage",
            "backward_jump_allowed": False,
        },
        "rules": route_policy()["rules"],
    }


def hub_attempt_placeholder(reason: str, *, expected_before_local: bool = True) -> dict[str, Any]:
    return {
        "schema": "mcp_session.hub_mcp_attempt.v1",
        "ok": False,
        "attempted": False,
        "used": False,
        "transport": "codex_current_turn_local_http_mcp_hub",
        "reason": reason,
        "expected_before_local": expected_before_local,
        "route_policy": {
            "must_try_hub_mcp_before_local_hub": True,
            "acceptable_skip_reasons": [
                "already_inside_hub_complete_route",
                "hub_mcp_not_exposed_in_current_turn",
                "hub_mcp_transport_closed",
                "known_direct_hub_tool_unavailable",
                "hub_schema_or_permission_mapping_unclear",
            ],
        },
    }


def route_contract_check(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = policy if isinstance(policy, dict) else route_policy()
    order = list(payload.get("canonical_order") or [])
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
    issues: list[str] = []
    priority_validation = validate_execution_priority()
    if not priority_validation.get("ok"):
        issues.extend(f"execution_priority:{item}" for item in priority_validation.get("issues", []))
    configured_profile_count = int(priority_validation.get("configured_profile_count") or 0)
    if configured_profile_count and len(PROFILE_PRIORITIES) < configured_profile_count:
        issues.append("configured_profile_priorities_incomplete")
    required = ["hub_mcp_direct", "native_mcp", "precise_tool_discovery", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"]
    for item in required:
        if item not in order:
            issues.append(f"missing_route:{item}")
    expected_order = ["precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"]
    if [item for item in order if item in expected_order] != expected_order:
        issues.append("canonical_fallback_sequence_invalid")
    if rules.get("hub_mcp_before_local_hub") is not True:
        issues.append("hub_mcp_not_required_before_local_hub")
    if rules.get("specific_affinity_overrides_generic_guidance") is not True:
        issues.append("specific_affinity_precedence_missing")
    if rules.get("affinity_lookup_before_first_call") is not True:
        issues.append("affinity_lookup_not_required_before_first_call")
    if rules.get("fixed_forward_fallback_sequence") is not True:
        issues.append("fixed_forward_fallback_sequence_missing")
    if rules.get("failure_continues_without_backward_jump") is not True:
        issues.append("backward_fallback_jump_allowed")
    if rules.get("hub_first_must_not_probe_native_first") is not True:
        issues.append("hub_first_native_probe_allowed")
    if rules.get("unclassified_never_defaults_native") is not True:
        issues.append("unclassified_native_default_allowed")
    if rules.get("precise_tool_discovery_before_unbound") is not True:
        issues.append("precise_tool_discovery_not_required")
    if rules.get("native_unbound_requires_precise_discovery_evidence") is not True:
        issues.append("native_unbound_without_precise_discovery_allowed")
    if rules.get("direct_known_hub_tool_before_complete_route") is not True:
        issues.append("direct_hub_tool_not_before_complete_route")
    if rules.get("complete_route_is_diagnostic_or_dynamic_not_default_transit") is not True:
        issues.append("complete_route_role_too_broad")
    if rules.get("local_hub_cli_only_after_hub_mcp_unavailable_or_insufficient") is not True:
        issues.append("local_hub_cli_not_limited_after_hub")
    if rules.get("terminal_local_read_only_after_same_boundary_routes") is not True:
        issues.append("terminal_local_read_can_skip_same_boundary_routes")
    if rules.get("terminal_local_read_requires_route_exhaustion_evidence") is not True:
        issues.append("terminal_local_read_missing_exhaustion_evidence_rule")
    if rules.get("permission_boundary") != "same_as_native_tool":
        issues.append("permission_boundary_not_preserved")
    github_pack = call_priority_pack("github", "search_repositories", "github_remote")
    hub_step = next((step for step in github_pack["steps"] if step.get("id") == "hub_mcp_direct"), {})
    if github_pack.get("execution_affinity") != "hub_first":
        issues.append("github_not_hub_first")
    if "search_repositories" not in precise_tool_discovery_query("github", "search_repositories"):
        issues.append("github_precise_discovery_query_missing_search_repositories")
    if "github.gh" not in hub_step.get("tools", []):
        issues.append("github_direct_hub_tool_missing")
    for profile, expected in {
        "context7": "owner_mcp.call_readonly",
        "microsoftdocs": "owner_mcp.call_readonly",
        "filesystem-admin": "owner_mcp.call_readonly",
        "mobile-openclaw-bridge": "mobile_bridge.get_pending_batch",
        "resource_acquisition": "resource.request",
    }.items():
        tools = direct_hub_tools_for(profile, profile)
        if expected not in tools:
            issues.append(f"{profile}_direct_hub_tool_missing")
    affinity_cases = {
        "codegraph": (execution_affinity("codegraph", "codegraph_explore", "code_structure"), "hub_first", "none"),
        "chrome": (execution_affinity("chrome-devtools", "take_snapshot", "browser_session"), "session_native_first", "current_chrome"),
        "mobile_supplement": (execution_affinity("mobile-openclaw-bridge", "bridge.get_pending_batch", "mobile_bridge"), "session_native_first", "current_mobile_thread"),
        "mobile_status": (execution_affinity("mobile-openclaw-bridge", "status", "mobile_bridge"), "owner_cli_first", "none"),
    }
    for name, (actual, expected_affinity, expected_binding) in affinity_cases.items():
        if actual.get("execution_affinity") != expected_affinity or actual.get("session_binding") != expected_binding:
            issues.append(f"execution_affinity_invalid:{name}")
    first_step_cases = {
        "codegraph": (call_priority_pack("codegraph", "codegraph_explore", "code_structure"), "hub_mcp_direct"),
        "pmb": (call_priority_pack("local-pmb-memory", "prepare", "memory_router"), "hub_mcp_direct"),
        "github": (call_priority_pack("github", "search_repositories", "github_remote"), "hub_mcp_direct"),
        "context7": (call_priority_pack("context7", "query_docs", "external_docs_research"), "hub_mcp_direct"),
        "filesystem": (call_priority_pack("filesystem", "read_text_file", "resource_acquisition"), "hub_mcp_direct"),
        "filesystem_write": (call_priority_pack("filesystem", "write_file", "resource_acquisition"), "hub_mcp_gateway"),
        "filesystem_admin": (call_priority_pack("filesystem-admin", "read_text_file", "resource_acquisition"), "hub_mcp_direct"),
        "filesystem_admin_write": (call_priority_pack("filesystem-admin", "write_file", "resource_acquisition"), "hub_mcp_gateway"),
        "sqlite": (call_priority_pack("sqlite-bridge-ro", "sqlite_query", "sqlite_state"), "hub_mcp_direct"),
        "sqlite_write": (call_priority_pack("sqlite-scratch", "sqlite_execute", "sqlite_state"), "hub_mcp_gateway"),
        "node_repl": (call_priority_pack("node_repl", "js", ""), "precise_tool_discovery"),
        "unknown": (call_priority_pack("unknown-profile", "read", ""), "hub_mcp_gateway"),
        "chrome": (call_priority_pack("chrome-devtools", "take_snapshot", "browser_session"), "precise_tool_discovery"),
        "mobile_status": (call_priority_pack("mobile-openclaw-bridge", "status", "mobile_bridge"), "owner_cli"),
    }
    for name, (pack, expected_first) in first_step_cases.items():
        steps = pack.get("steps") if isinstance(pack.get("steps"), list) else []
        step_ids = [str(step.get("id") or "") for step in steps]
        actual_first = str(pack.get("required_first_step") or (steps[0].get("id") if steps else ""))
        if actual_first != expected_first:
            issues.append(f"affinity_first_step_invalid:{name}:{actual_first}")
        if pack.get("continuation_policy", {}).get("hub_failure_does_not_release_chain") is not True:
            issues.append(f"affinity_continuation_missing:{name}")
        if any(not step.get("on_failure_next_step") for step in steps):
            issues.append(f"affinity_step_continuation_missing:{name}")
        if pack.get("continuation_policy", {}).get("backward_jump_allowed") is not False:
            issues.append(f"affinity_backward_jump_not_disabled:{name}")
        if step_ids != [item for item in expected_order if item in step_ids]:
            issues.append(f"affinity_sequence_not_forward_only:{name}")
        if pack.get("execution_affinity") == "hub_first" and any(item in step_ids for item in ("precise_tool_discovery", "native_mcp")):
            issues.append(f"hub_first_jumps_backward_to_native:{name}")
    priority_regression_matrix: list[dict[str, Any]] = []
    for case in PRIORITY_REGRESSION_MATRIX:
        pack = call_priority_pack(case["profile"], case["tool"], case["capability"])
        actual_affinity = str(pack.get("execution_affinity") or "")
        actual_first_step = str(pack.get("required_first_step") or "")
        case_ok = (
            actual_affinity == case["expected_affinity"]
            and actual_first_step == case["expected_first_step"]
        )
        if not case_ok:
            issues.append(f"priority_regression_route_mismatch:{case['id']}")
        priority_regression_matrix.append(
            {
                **case,
                "actual_affinity": actual_affinity,
                "actual_first_step": actual_first_step,
                "ok": case_ok,
            }
        )
    surface_check = affinity_surface_check()
    issues.extend(str(item) for item in surface_check.get("issues", []))
    return {
        "schema": "mcp_route_policy.contract_check.v1",
        "ok": not issues,
        "issues": issues,
        "policy": payload,
        "priority_regression_matrix": priority_regression_matrix,
        "surface_check": surface_check,
    }


def affinity_surface_check() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    home = Path.home()
    checks = [
        ("global_agents", home / ".codex" / "AGENTS.md", ["resolve the capability matrix before mcp work", "move only forward after failure"], ["prefer native mcp when"]),
        ("workspace_agents", root / "AGENTS.md", ["mcp affinity, session binding, and fallback", "generated capability routes"], []),
        ("codegraph_skill", home / ".codex" / "skills" / "codegraph-ops" / "SKILL.md", ["mcp__local_mcp_hub.codegraph_explore", "do not jump backward to native"], ["native `mcp__codegraph.codegraph_explore` tool is the second route", "use the mcp tool first when available"]),
        ("github_skill", home / ".codex" / "skills" / "github-ops" / "SKILL.md", ["github is `hub_first`"], ["prefer native github mcp"]),
        ("memory_router", root / "_bridge" / "memory_router.py", ["hub.pmb_prepare|hub.pmb_recall first"], ["native local-pmb-memory prepare|recall; fallback hub"]),
        ("workflow_orchestrator", root / "_bridge" / "workflow_orchestrator.py", ["resolve classified execution affinity before the first mcp call"], ["native mcp first when current-turn callable"]),
        ("capability_matrix", root / "_bridge" / "docs" / "mcp_capability_matrix.md", ["one fixed forward sequence with affinity-specific entry points", "does not jump backward to native"], ["github mcp first for remote repo state", "after the native mcp path is unavailable", "native mcp is fallback"]),
    ]
    issues: list[str] = []
    evidence: list[dict[str, Any]] = []
    for name, path, required, forbidden in checks:
        if not path.is_file():
            issues.append(f"affinity_surface_missing:{name}")
            evidence.append({"surface": name, "path": str(path), "ok": False, "reason": "missing"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        missing = [token for token in required if token not in text]
        stale = [token for token in forbidden if token in text]
        if missing:
            issues.append(f"affinity_surface_required_text_missing:{name}")
        if stale:
            issues.append(f"affinity_surface_stale_precedence:{name}")
        evidence.append({"surface": name, "path": str(path), "ok": not missing and not stale, "missing": missing, "stale": stale})
    return {"schema": "mcp_route_policy.affinity_surface_check.v1", "ok": not issues, "issues": issues, "evidence": evidence}


if __name__ == "__main__":
    import json

    print(json.dumps(route_contract_check(), ensure_ascii=False, indent=2))
