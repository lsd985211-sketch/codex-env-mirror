#!/usr/bin/env python3
"""Explicit profile/tool execution-priority registry for configured MCPs.

Ownership: classify whether an MCP action starts through Hub, native current
session, native tool transport, or an owner CLI.
Non-goals: execute tools, change permissions, probe transports, or define
fallback implementation details.
State behavior: pure and read-only.
Caller context: mcp_route_policy, capability routes, session doctor, workflow
route packs, and validators.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tomllib
from pathlib import Path
from typing import Any


PROFILE_PRIORITIES: dict[str, dict[str, Any]] = {
    "codegraph": {"affinity": "hub_first", "binding": "none", "reason": "stateless_index_query_with_fresh_stdio_hub_adapter"},
    "gitnexus": {"affinity": "hub_first", "binding": "none", "reason": "stateless_read_only_semantic_code_graph_has_hub_adapter"},
    "graphify": {"affinity": "hub_first", "binding": "none", "reason": "stateless_read_only_managed_knowledge_graph_has_hub_adapter"},
    "headroom": {"affinity": "hub_first", "binding": "none", "reason": "fresh_stdio_reversible_context_compression_has_direct_hub_tools"},
    "mobile-openclaw-bridge": {"affinity": "owner_cli_first", "binding": "none", "reason": "default_mobile_status_and_maintenance_use_owner_state; thread_supplements_override"},
    "local-pmb-memory": {"affinity": "hub_first", "binding": "none", "reason": "stateless_pmb_read_prepare_calls_have_direct_hub_tools"},
    "filesystem": {"affinity": "hub_first", "binding": "none", "reason": "stateless_read_tools_have_allowlisted_hub_fresh_stdio_adapter"},
    "filesystem-admin": {"affinity": "hub_first", "binding": "none", "reason": "read_tools_use_the_readonly_owner_adapter_and_mutations_use_the_same_boundary_fresh_stdio_gateway"},
    "custom-slash-commands": {"affinity": "hub_first", "binding": "none", "reason": "stateless_template_registry_has_direct_hub_tools"},
    "local-mcp-hub": {"affinity": "hub_first", "binding": "none", "reason": "hub_control_plane_is_directly_exposed"},
    "sqlite-scratch": {"affinity": "hub_first", "binding": "none", "reason": "read_queries_have_direct_hub_aliases; writes_override"},
    "sqlite-bridge-ro": {"affinity": "hub_first", "binding": "none", "reason": "read_only_sqlite_queries_have_direct_hub_aliases"},
    "node_repl": {"affinity": "session_native_first", "binding": "current_repl_kernel", "reason": "persistent_kernel_state_is_bound_to_current_session"},
    "context7": {"affinity": "hub_first", "binding": "none", "reason": "stateless_docs_owner_has_readonly_hub_adapter"},
    "github": {"affinity": "hub_first", "binding": "none", "reason": "github_api_and_gh_hub_routes_cover_read_and_write_operations"},
    "myskills": {"affinity": "hub_first", "binding": "none", "reason": "read_inventory_and_discovery_have_readonly_hub_adapter; writes_override"},
    "gui-automation": {"affinity": "session_native_first", "binding": "current_gui_session", "reason": "window_and_selector state_is_current_session_bound"},
    "desktop-weixin": {"affinity": "session_native_first", "binding": "current_desktop_weixin", "reason": "desktop_chat_and_draft_state_is_current_session_bound"},
    "chrome-devtools": {"affinity": "session_native_first", "binding": "current_chrome", "reason": "tabs_cookies_and_devtools_state_are_current_session_bound"},
    "playwright": {"affinity": "session_native_first", "binding": "current_browser_session", "reason": "browser_context_and_page_state_are_current_session_bound"},
    "next-ai-drawio": {"affinity": "session_native_first", "binding": "current_diagram_session", "reason": "editable_diagram_session_and_preview_state_are_process_bound"},
    "markitdown": {"affinity": "hub_first", "binding": "none", "reason": "stateless_conversion_has_readonly_hub_adapter"},
    "microsoftdocs": {"affinity": "hub_first", "binding": "none", "reason": "stateless_docs_owner_has_readonly_hub_adapter"},
    "openai-docs": {"affinity": "hub_first", "binding": "none", "reason": "official_openai_docs_owner_has_readonly_hub_adapter"},
    "agent-bridge": {"affinity": "native_first", "binding": "none", "reason": "full_claim_send_complete_surface_has_no_equivalent_hub_adapter"},
}


HUB_MANAGED_MCP_NAMES = {
    "codegraph",
    "gitnexus",
    "graphify",
    "headroom",
    "context7",
    "custom-slash-commands",
    "filesystem",
    "filesystem-admin",
    "github",
    "local-pmb-memory",
    "markitdown",
    "microsoftdocs",
    "openai-docs",
    "myskills",
    "sqlite-bridge-ro",
    "sqlite-scratch",
}
DESKTOP_NATIVE_MCP_NAMES = set(PROFILE_PRIORITIES) - HUB_MANAGED_MCP_NAMES
LAZY_NATIVE_MCP_NAMES = {"chrome-devtools", "gui-automation", "next-ai-drawio", "playwright"}

for _profile_name, _profile_record in PROFILE_PRIORITIES.items():
    _hub_managed = _profile_name in HUB_MANAGED_MCP_NAMES
    _lazy_native = _profile_name in LAZY_NATIVE_MCP_NAMES
    _profile_record["registration_mode"] = "hub_managed" if _hub_managed else "desktop_native"
    _profile_record["desktop_instance_budget"] = 0 if _hub_managed else 1
    _profile_record["startup_mode"] = "hub_managed" if _hub_managed else ("lazy_stdio_proxy" if _lazy_native else "eager_native")
    _profile_record["startup_child_budget"] = 0 if (_hub_managed or _lazy_native) else 1
    _profile_record["lifecycle"] = (
        "fresh_stdio_per_call_exit"
        if _hub_managed
        else ("lazy_session_child_after_first_non_catalog_call" if _lazy_native else "session_or_control_plane_owned")
    )


HUB_FIRST_TOOLS: dict[str, set[str]] = {
    "codegraph": {"codegraph_explore"},
    "gitnexus": {"list_tools", "call"},
    "graphify": {"list_tools", "call"},
    "headroom": {"compress", "retrieve", "stats"},
    "context7": {"resolve_library_id", "query_docs"},
    "custom-slash-commands": {"slash_get_command", "slash_list_commands", "slash_render_command", "slash_validate_registry", "get_command", "list_commands", "render_command", "validate_registry"},
    "github": {"get_me", "get_file_contents", "search_code", "search_commits", "search_issues", "search_pull_requests", "search_repositories", "search_users"},
    "local-pmb-memory": {"workspace_info", "prepare", "recall", "project_overview", "stats", "list_goals"},
    "markitdown": {"convert_to_markdown"},
    "microsoftdocs": {"microsoft_code_sample_search", "microsoft_docs_fetch", "microsoft_docs_search"},
    "openai-docs": {"search_openai_docs", "fetch_openai_doc", "list_openai_docs", "get_openapi_spec"},
    "sqlite-bridge-ro": {"sqlite_health", "sqlite_query", "sqlite_schema", "sqlite_tables"},
    "filesystem": {"directory_tree", "get_file_info", "list_allowed_directories", "list_directory", "list_directory_with_sizes", "read_file", "read_media_file", "read_multiple_files", "read_text_file", "search_files"},
    "filesystem-admin": {"directory_tree", "get_file_info", "list_allowed_directories", "list_directory", "list_directory_with_sizes", "read_file", "read_media_file", "read_multiple_files", "read_text_file", "search_files"},
    "myskills": {"skills_inventory", "skills_read", "skills_history", "scenarios_list", "discover_search"},
    "sqlite-scratch": {"sqlite_health", "sqlite_query", "sqlite_schema", "sqlite_tables"},
}


NATIVE_FIRST_TOOLS: dict[str, set[str]] = {
    "filesystem": {"create_directory", "edit_file", "move_file", "write_file"},
    "local-pmb-memory": {
        "forget", "index_pdf", "index_project", "mark_lesson_followed", "pin",
        "record_activity", "record_batch", "record_exploration", "record_fact",
        "record_fact_tree", "record_goal", "record_keyed_fact", "record_milestone",
        "update_goal",
    },
    "myskills": {
        "align_apply", "authoring_draft", "authoring_revise", "discover_install",
        "scenarios_create", "skills_delete", "skills_rescan", "skills_rollback",
        "skills_set_enabled", "skills_set_scenarios",
    },
    "sqlite-scratch": {"sqlite_execute", "sqlite_insert_record", "sqlite_upsert_record"},
}


MOBILE_SESSION_TOOLS = {"bridge.get_pending_batch", "bridge.ack_message", "get_pending_batch", "ack_message"}


def runtime_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    release = platform.release().lower()
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in release:
        return "wsl"
    return sys.platform


def codex_config_path() -> Path:
    configured = os.environ.get("CODEX_CONFIG")
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    if runtime_platform() == "wsl":
        for candidate in (home / ".codex-app" / "config.toml", home / ".codex" / "config.toml"):
            if candidate.is_file():
                return candidate
        return home / ".codex-app" / "config.toml"
    return home / ".codex" / "config.toml"


CAPABILITY_PRIORITIES = {
    "cli_harness_pipeline": ("owner_cli_first", "none", "cli_harness_is_owned_by_local_governance_cli"),
    "developer_toolchain": ("owner_cli_first", "none", "developer_tools_are_local_cli_executables"),
    "network_routing": ("hub_first", "none", "network_gateway_has_direct_hub_control_plane"),
    "code_structure": ("hub_first", "none", "stateless_codegraph_capability"),
    "gitnexus_semantic_graph": ("hub_first", "none", "stateless_gitnexus_semantic_graph_capability"),
    "graphify_knowledge_graph": ("hub_first", "none", "stateless_graphify_managed_graph_capability"),
    "context_compression": ("hub_first", "none", "stateless_reversible_context_compression_capability"),
    "external_docs_research": ("hub_first", "none", "stateless_docs_capability"),
    "github_remote": ("hub_first", "none", "github_hub_capability"),
    "memory_router": ("hub_first", "none", "pmb_read_prepare_capability"),
    "resource_acquisition": ("hub_first", "none", "resource_hub_capability"),
    "sqlite_state": ("hub_first", "none", "sqlite_read_capability"),
    "browser_session": ("session_native_first", "current_browser_session", "current_browser_state_capability"),
}


PRIORITY_REGRESSION_MATRIX: tuple[dict[str, str], ...] = (
    {
        "id": "codegraph_read",
        "profile": "codegraph",
        "tool": "codegraph_explore",
        "capability": "code_structure",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "gitnexus_read",
        "profile": "gitnexus",
        "tool": "call",
        "capability": "gitnexus_semantic_graph",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "graphify_read",
        "profile": "graphify",
        "tool": "call",
        "capability": "graphify_knowledge_graph",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "headroom_compress",
        "profile": "headroom",
        "tool": "compress",
        "capability": "context_compression",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "pmb_read",
        "profile": "local-pmb-memory",
        "tool": "prepare",
        "capability": "memory_router",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "pmb_write",
        "profile": "local-pmb-memory",
        "tool": "record_fact",
        "capability": "memory_router",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_gateway",
    },
    {
        "id": "filesystem_read",
        "profile": "filesystem",
        "tool": "read_text_file",
        "capability": "resource_acquisition",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "filesystem_write",
        "profile": "filesystem",
        "tool": "write_file",
        "capability": "resource_acquisition",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_gateway",
    },
    {
        "id": "filesystem_admin_read",
        "profile": "filesystem-admin",
        "tool": "read_text_file",
        "capability": "resource_acquisition",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "filesystem_admin_write",
        "profile": "filesystem-admin",
        "tool": "write_file",
        "capability": "resource_acquisition",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_gateway",
    },
    {
        "id": "myskills_read",
        "profile": "myskills",
        "tool": "skills_inventory",
        "capability": "skill_inventory_governance",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_direct",
    },
    {
        "id": "myskills_write",
        "profile": "myskills",
        "tool": "skills_rescan",
        "capability": "skill_inventory_governance",
        "expected_affinity": "hub_first",
        "expected_first_step": "hub_mcp_gateway",
    },
)


def _record(profile: str, tool: str, capability: str, affinity: str, binding: str, source: str, reason: str) -> dict[str, Any]:
    profile_record = PROFILE_PRIORITIES.get(profile, {})
    return {
        "profile": profile,
        "tool": tool,
        "capability": capability,
        "execution_affinity": affinity,
        "session_binding": binding,
        "registration_mode": profile_record.get("registration_mode", "unclassified"),
        "desktop_instance_budget": profile_record.get("desktop_instance_budget"),
        "startup_mode": profile_record.get("startup_mode", "unclassified"),
        "startup_child_budget": profile_record.get("startup_child_budget"),
        "lifecycle": profile_record.get("lifecycle", "unclassified"),
        "priority_source": source,
        "priority_reason": reason,
        "priority_explicit": affinity != "unclassified",
        "native_fallback_allowed": affinity in {"native_first", "session_native_first"},
    }


def resolve_execution_priority(profile: str, tool: str = "", capability: str = "") -> dict[str, Any]:
    profile_key = str(profile or "").strip().lower()
    tool_key = str(tool or "").strip().lower()
    capability_key = str(capability or "").strip().lower()
    if profile_key == "mobile-openclaw-bridge" and tool_key in MOBILE_SESSION_TOOLS:
        return _record(profile_key, tool_key, capability_key, "session_native_first", "current_mobile_thread", "tool_override", "mobile_supplement_is_bound_to_current_thread")
    profile_record = PROFILE_PRIORITIES.get(profile_key)
    if profile_key in HUB_MANAGED_MCP_NAMES:
        if tool_key and tool_key in HUB_FIRST_TOOLS.get(profile_key, set()):
            reason = "tool_has_direct_hub_mapping"
        else:
            reason = "hub_managed_profile_uses_fresh_stdio_gateway_for_full_tool_surface"
        return _record(profile_key, tool_key, capability_key, "hub_first", "none", "registration", reason)
    if tool_key and tool_key in NATIVE_FIRST_TOOLS.get(profile_key, set()):
        return _record(profile_key, tool_key, capability_key, "native_first", "none", "tool_override", "mutating_or_unrepresented_tool_requires_desktop_native_profile")
    if tool_key and profile_key in HUB_FIRST_TOOLS:
        if tool_key in HUB_FIRST_TOOLS[profile_key]:
            return _record(profile_key, tool_key, capability_key, "hub_first", "none", "tool_override", "tool_has_direct_hub_mapping")
        return _record(profile_key, tool_key, capability_key, "native_first", "none", "tool_override", "tool_is_not_exposed_by_profile_hub_adapter")
    if profile_record:
        return _record(profile_key, tool_key, capability_key, str(profile_record["affinity"]), str(profile_record["binding"]), "profile", str(profile_record["reason"]))
    if capability_key in CAPABILITY_PRIORITIES:
        affinity, binding, reason = CAPABILITY_PRIORITIES[capability_key]
        return _record(profile_key, tool_key, capability_key, affinity, binding, "capability", reason)
    return _record(profile_key, tool_key, capability_key, "unclassified", "none", "missing", "profile_and_capability_priority_not_registered")


def validate() -> dict[str, Any]:
    allowed = {"hub_first", "native_first", "session_native_first", "owner_cli_first"}
    issues: list[str] = []
    platform_scope = runtime_platform()
    for profile, record in PROFILE_PRIORITIES.items():
        if record.get("affinity") not in allowed:
            issues.append(f"invalid_affinity:{profile}")
        if not record.get("reason"):
            issues.append(f"missing_reason:{profile}")
        expected_mode = "hub_managed" if profile in HUB_MANAGED_MCP_NAMES else "desktop_native"
        if record.get("registration_mode") != expected_mode:
            issues.append(f"registration_mode_mismatch:{profile}")
        expected_budget = 0 if profile in HUB_MANAGED_MCP_NAMES else 1
        if record.get("desktop_instance_budget") != expected_budget:
            issues.append(f"desktop_instance_budget_mismatch:{profile}")
        expected_startup_mode = "hub_managed" if profile in HUB_MANAGED_MCP_NAMES else ("lazy_stdio_proxy" if profile in LAZY_NATIVE_MCP_NAMES else "eager_native")
        if record.get("startup_mode") != expected_startup_mode:
            issues.append(f"startup_mode_mismatch:{profile}")
        expected_child_budget = 0 if profile in HUB_MANAGED_MCP_NAMES or profile in LAZY_NATIVE_MCP_NAMES else 1
        if record.get("startup_child_budget") != expected_child_budget:
            issues.append(f"startup_child_budget_mismatch:{profile}")
    configured_profiles: list[str] = []
    config_path = codex_config_path()
    if config_path.is_file():
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            configured_profiles = sorted(str(item).strip().lower() for item in (config.get("mcp_servers") or {}))
            configured_set = set(configured_profiles)
            for profile in sorted(configured_set - set(PROFILE_PRIORITIES)):
                issues.append(f"configured_profile_priority_missing:{profile}")
            for profile in sorted(configured_set & HUB_MANAGED_MCP_NAMES):
                issues.append(f"hub_managed_profile_registered_in_config:{profile}")
            if platform_scope == "windows":
                for profile in sorted(DESKTOP_NATIVE_MCP_NAMES - configured_set):
                    issues.append(f"desktop_native_profile_missing_from_config:{profile}")
            elif "node_repl" not in configured_set:
                issues.append("wsl_required_profile_missing_from_config:node_repl")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            issues.append(f"configured_profile_check_failed:{type(exc).__name__}")
    probes = {
        "gitnexus_read": resolve_execution_priority("gitnexus", "call", "gitnexus_semantic_graph"),
        "graphify_read": resolve_execution_priority("graphify", "call", "graphify_knowledge_graph"),
        "pmb_prepare": resolve_execution_priority("local-pmb-memory", "prepare"),
        "pmb_write": resolve_execution_priority("local-pmb-memory", "record_fact"),
        "filesystem_read": resolve_execution_priority("filesystem", "read_text_file"),
        "filesystem_write": resolve_execution_priority("filesystem", "write_file"),
        "filesystem_admin": resolve_execution_priority("filesystem-admin", "write_file"),
        "myskills_read": resolve_execution_priority("myskills", "skills_inventory"),
        "myskills_write": resolve_execution_priority("myskills", "skills_set_enabled"),
        "sqlite_read": resolve_execution_priority("sqlite-scratch", "sqlite_query"),
        "sqlite_write": resolve_execution_priority("sqlite-scratch", "sqlite_execute"),
        "node_repl": resolve_execution_priority("node_repl", "js"),
        "unknown": resolve_execution_priority("unknown-profile", "read"),
    }
    expected = {
        "gitnexus_read": "hub_first", "graphify_read": "hub_first",
        "pmb_prepare": "hub_first", "pmb_write": "hub_first",
        "filesystem_read": "hub_first", "filesystem_write": "hub_first",
        "filesystem_admin": "hub_first", "sqlite_read": "hub_first",
        "myskills_read": "hub_first", "myskills_write": "hub_first",
        "sqlite_write": "hub_first", "node_repl": "session_native_first",
        "unknown": "unclassified",
    }
    for key, affinity in expected.items():
        if probes[key]["execution_affinity"] != affinity:
            issues.append(f"probe_mismatch:{key}")
    regression_matrix: list[dict[str, Any]] = []
    for case in PRIORITY_REGRESSION_MATRIX:
        actual = resolve_execution_priority(case["profile"], case["tool"], case["capability"])
        case_ok = actual.get("execution_affinity") == case["expected_affinity"]
        if not case_ok:
            issues.append(f"priority_regression_affinity_mismatch:{case['id']}")
        regression_matrix.append({**case, "actual_affinity": actual.get("execution_affinity"), "ok": case_ok})
    return {
        "schema": "mcp_execution_priority.validate.v1",
        "ok": not issues,
        "issues": issues,
        "profile_count": len(PROFILE_PRIORITIES),
        "configured_profile_count": len(configured_profiles),
        "platform_scope": platform_scope,
        "config_path": str(config_path),
        "registration_validation": "windows_desktop_native_contract" if platform_scope == "windows" else "wsl_projected_config_contract",
        "hub_managed_profile_count": len(HUB_MANAGED_MCP_NAMES),
        "desktop_native_profile_count": len(DESKTOP_NATIVE_MCP_NAMES),
        "hub_managed_profiles": sorted(HUB_MANAGED_MCP_NAMES),
        "desktop_native_profiles": sorted(DESKTOP_NATIVE_MCP_NAMES),
        "lazy_native_profiles": sorted(LAZY_NATIVE_MCP_NAMES),
        "profiles": PROFILE_PRIORITIES,
        "probes": probes,
        "regression_matrix": regression_matrix,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
