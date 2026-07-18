#!/usr/bin/env python3
"""Machine-first derived route index for MCP capability selection.

The Markdown matrix remains the source of truth. This script emits a compact
runtime index for Codex/tool orchestration, optimized for lookup over prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from bounded_output import aggregate_validator_cli_payload, bounded_payload
from mcp_route_policy import call_priority_pack, direct_hub_hints_for, direct_hub_tools_for, execution_affinity, route_contract_check, route_policy
from shared.json_cli import now_iso


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
MATRIX = BRIDGE / "docs" / "mcp_capability_matrix.md"
OUT = BRIDGE / "runtime" / "mcp_capability_routes.json"
SCHEMA = "mcp_capability_routes.v1"
ROUTE_DEFINITION_REVISION = "2026-07-15.filesystem-hub-first.v2"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


MANUAL_ROUTES: list[dict[str, Any]] = [
    {
        "capability": "slash_templates",
        "terms": ["custom-slash-commands", "slash", "template", "workflow"],
        "native_mcp": "custom-slash-commands",
        "hub_route": "hub.slash",
        "local_fallback": "python _bridge\\custom_slash_commands_mcp.py --registry _bridge\\slash_commands\\commands.json",
        "permission_boundary": "read_render_validate_only",
        "validation_command": "python _bridge\\slash_command_governance.py validate",
        "current_turn_evidence_rule": "slash.validate_registry or render must complete in active turn",
    },
    {
        "capability": "mcp_stability",
        "terms": ["mcp", "transport closed", "tool_unbound", "hub"],
        "native_mcp": "local-mcp-hub",
        "hub_route": "known same-boundary Hub tool directly when mapped; mcp_gateway.complete_route only for unknown/ambiguous routes, schema/permission uncertainty, or diagnostic evidence",
        "local_fallback": "python _bridge\\mcp_session_doctor.py complete-route --profile <profile> --tool <tool> --status transport_closed --arguments-json <json> only when Hub is unavailable or not exposed; python _bridge\\mcp_session_doctor.py validate",
        "permission_boundary": "same_tool_permission_boundary",
        "validation_command": "python _bridge\\mcp_session_doctor.py validate",
        "current_turn_evidence_rule": "config/protocol success is not current_turn_callable; after native failure route is complete with direct known Hub call evidence, or with complete_route/CLI fallback route_complete or same_boundary_blocker",
    },
    {
        "capability": "local_filesystem_read",
        "terms": [
            "filesystem",
            "filesystem-admin",
            "read_file",
            "read_text_file",
            "read_multiple_files",
            "list_directory",
            "directory_tree",
            "search_files",
            "local files",
            "known files",
        ],
        "native_mcp": "filesystem",
        "hub_route": "owner_mcp.call_readonly profile=filesystem tool=<allowlisted read-only filesystem tool>",
        "local_fallback": "no separate owner CLI; continue to terminal_local_read only after Hub routes are unavailable, insufficient, or explicitly inapplicable",
        "permission_boundary": "same_filesystem_read_only_boundary_no_write_permission_expansion",
        "validation_command": "python _bridge\\mcp_capability_routes.py lookup --terms filesystem filesystem-admin read_multiple_files; python _bridge\\mcp_capability_routes.py validate --full",
        "current_turn_evidence_rule": "owner_mcp.call_readonly is the required first stage for allowlisted reads and needs no prior native failure; mutations remain on the filesystem-admin same-boundary Hub gateway and keep local-write approval requirements",
        "usage_policy": "read_only_route; never project owner_mcp.call_readonly as authorization for write, edit, move, create, or delete operations",
    },
    {
        "capability": "skill_inventory_governance",
        "terms": ["myskills", "skill inventory", "skills_rescan", "authoring_draft", "authoring_revise", "技能治理", "技能库存"],
        "native_mcp": "myskills",
        "hub_route": "mcp_gateway.call profile=myskills tool=<tool> when the target tool is known; mcp_gateway.complete_route only for unknown/ambiguous route or diagnostic evidence",
        "local_fallback": "python _bridge\\mcp_session_doctor.py complete-route --profile myskills --tool <tool> --status transport_closed --arguments-json <json> only when Hub is unavailable or not exposed; direct SKILL.md read or skill_orchestrator only after direct Hub call or route-completion evidence",
        "permission_boundary": "same_myskills_permission_boundary_no_direct_db_or_file_mutation_without_approval",
        "validation_command": "python _bridge\\skill_orchestrator.py validate; python _bridge\\mcp_session_doctor.py complete-route --profile myskills --tool skills_inventory --status tool_unbound --arguments-json {} --dry-run",
        "current_turn_evidence_rule": "Hub read-only owner call is the entry stage for stateless inventory/read operations; failures continue forward through gateway/local owner routes, while writes use their native-first override and keep the original approval boundary",
    },
    {
        "capability": "code_structure",
        "terms": ["codegraph", "source", "call path", "blast radius"],
        "native_mcp": "codegraph",
        "hub_route": "hub.codegraph.explore",
        "local_fallback": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py codegraph-fallback explore --max-files <n> <query>; rg with generated-tree exclusions only if same-capability fallback is unavailable",
        "permission_boundary": "read_only_source_inspection",
        "validation_command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session smoke --profile codegraph; python _bridge\\codegraph_health.py validate",
        "current_turn_evidence_rule": "Hub codegraph.explore is the entry stage for stateless source analysis; failures continue forward through gateway/local owner routes, while native protocol smoke remains separate backend-readiness evidence",
        "usage_policy": "use_for_source_structure_symbol_flow_call_paths_and_blast_radius_before_rg_or_manual_reads; include freshness_targets for explicit local files",
        "fallback_order": "hub_codegraph_explore_then_hub_gateway_then_local_codegraph_cli_then_targeted_rg",
    },
    {
        "capability": "memory_router",
        "terms": ["memory", "pmb", "note", "recall", "work-note", "user profile", "external knowledge", "record-store"],
        "native_mcp": "local-pmb-memory when routed",
        "hub_route": "hub.pmb_prepare|hub.pmb_recall when PMB route is selected",
        "local_fallback": "python _bridge\\memory_router.py route --message <task>; python _bridge\\local_pmb_memory.py pmb-recall --query <query>",
        "permission_boundary": "read_prepare_only_without_explicit_write_approval",
        "validation_command": "python _bridge\\memory_governance.py validate",
        "current_turn_evidence_rule": "memory_router decides layer fit first; Hub PMB read calls are preferred, while long-term writes and live-state verification remain separate",
    },
    {
        "capability": "agent_memory_absorption",
        "terms": [
            "agent-memory-engine",
            "ArcRift",
            "localmem",
            "memory absorption",
            "knowledge absorption",
            "external knowledge",
            "PMB",
            "note absorption",
        ],
        "native_mcp": "local-pmb-memory|memory_governance",
        "hub_route": "hub.pmb_prepare|hub.pmb_recall",
        "local_fallback": "python _bridge\\memory_governance.py absorb-plan; python _bridge\\memory_governance.py pmb-organize-plan; python _bridge\\memory_governance.py recall-verify; python _bridge\\external_knowledge.py doctor",
        "permission_boundary": "read_prepare_evidence_only_without_explicit_memory_write_approval",
        "validation_command": "python _bridge\\memory_governance.py validate; python _bridge\\external_knowledge.py doctor when sources captured",
        "current_turn_evidence_rule": "external memory projects are capability sources only; local evidence/candidate/proposal layers remain the authority",
    },
    {
        "capability": "cli_harness_pipeline",
        "terms": ["CLI-Anything", "cli-hub", "harness", "agent-native CLI", "cli-anything-weixin"],
        "native_mcp": "none",
        "hub_route": "none",
        "local_fallback": "python _bridge\\cli_anything_governance.py search <query>; python _bridge\\cli_anything_governance.py info <name>; cli-hub list --json",
        "permission_boundary": "read_discover_validate_by_default_install_requires_explicit_task_intent",
        "validation_command": "python _bridge\\cli_anything_governance.py validate; concrete harness --help and --json smoke when installed",
        "current_turn_evidence_rule": "installed skill plus governance command output identifies available harness capabilities; no free-form command execution",
    },
    {
        "capability": "developer_toolchain",
        "terms": ["ripgrep", "rg", "fd", "uv", "uvx", "ruff", "playwright", "developer toolchain", "base tools"],
        "native_mcp": "owning_tool_or_cli",
        "hub_route": "none",
        "local_fallback": "python _bridge\\code_maintainability.py toolchain; rg --version; fd --version; uv --version; uvx --version; ruff --version; playwright smoke for browser tasks",
        "permission_boundary": "utility_cli_supports_owner_validation_no_unapproved_package_or_format_churn",
        "validation_command": "python _bridge\\code_maintainability.py toolchain plus owning module validator/readback",
        "current_turn_evidence_rule": "utility tool success supports the task but does not replace the owning MCP/module evidence",
    },
    {
        "capability": "browser_session",
        "terms": ["browser", "browser session", "chrome", "chrome-devtools", "playwright", "page snapshot", "dom snapshot", "current page"],
        "native_mcp": "chrome-devtools|playwright",
        "hub_route": "session-bound native first; governed Chrome/Playwright Hub aliases are same-boundary fallback only",
        "local_fallback": "browser/Chrome/Playwright owner CLI or explicit manual step after session-preserving routes are unavailable",
        "permission_boundary": "current_browser_session_identity_and_existing_page_permissions",
        "validation_command": "snapshot or page-state readback from the selected current session",
        "current_turn_evidence_rule": "preserve the current browser session; do not replace it with an isolated browser merely because a stateless Hub route exists",
    },
    {
        "capability": "resource_acquisition",
        "terms": [
            "resource",
            "acquire",
            "fetch",
            "artifact",
            "file",
            "url",
            "document",
            "resource broker",
        ],
        "native_mcp": "owner_tool_after_broker_handoff",
        "hub_route": "resource.request|resource.status|resource.progress|resource.attach_result",
        "local_fallback": "python _bridge\\resource_cli.py request --path <path>|--url <url> --intent <intent> --json; python _bridge\\resource_cli.py status --request-id <id> --json",
        "permission_boundary": "resource_request_authorizes_resource_acquisition_owner_tool_orchestration_only_destructive_or_sensitive_actions_need_separate_approval",
        "validation_command": "python _bridge\\resource_fetcher_tests.py; local file request smoke; preview URL smoke; owner MCP/package-manager handoff plus attach-result smoke",
        "current_turn_evidence_rule": "completed broker receipt is consumable only with manifest; handoff_required is an internal intermediate state, then Codex calls the owner tool and attaches the result to the same request",
    },
    {
        "capability": "external_docs_research",
        "terms": [
            "联网",
            "搜索",
            "查资料",
            "相关知识",
            "official docs",
            "documentation",
            "docs",
            "api docs",
            "sdk docs",
            "Context7",
            "OpenAI Docs MCP",
            "OpenAI Developer Docs",
            "Microsoft Docs",
            "Microsoft Learn",
            "web research",
        ],
        "native_mcp": "openai-docs|context7|microsoftdocs|github|chrome-devtools|playwright",
        "hub_route": "resource.request/resource.status/resource.progress for acquisition receipts; direct known Hub owner tool or mcp_gateway.call only inside the same owner boundary; mcp_gateway.complete_route only for ambiguous/diagnostic routing",
        "local_fallback": "web.run search/open only after resource-layer ownership ends with a terminal blocker or the resource layer reports no usable owner route",
        "permission_boundary": "read_only_external_research_no_external_state_change",
        "validation_command": "tool result with source URL; python _bridge\\workflow_orchestrator.py validate; external_knowledge capture decision at closeout when reusable",
        "current_turn_evidence_rule": "explicit online research should first produce a resource-layer receipt. The resource layer routes OpenAI product docs to openai-docs with search then fetch, Microsoft/Windows/Azure to microsoftdocs, libraries/SDKs/frameworks to Context7, repository facts to GitHub MCP, and page/runtime evidence to browser/devtools/playwright; empty or metadata-only output is not completion, and generic web search must have a forward-fallback reason",
    },
    {
        "capability": "network_routing",
        "terms": [
            "network",
            "proxy",
            "dns",
            "connectivity",
            "node proxy",
            "runtime env",
            "probe-suite",
            "openai slow",
            "chatgpt slow",
            "timeout",
            "network route",
            "网络",
            "代理",
            "DNS",
            "连接慢",
            "卡断",
        ],
        "native_mcp": "local-mcp-hub when current-turn callable",
        "hub_route": "network_gateway.plan|network_gateway.env|network.plan|network.probe|network.probe_suite|network.validate",
        "local_fallback": "python _bridge\\network_doctor.py snapshot|recommend <target>|env <target> --runtime <runtime>|plan <target>|probe <target>|probe-suite|validate",
        "permission_boundary": "read_only_route_discovery_and_per_process_env_suggestion_no_system_proxy_dns_mutation",
        "validation_command": "python _bridge\\network_doctor.py validate; python _bridge\\network_doctor.py probe-suite --timeout 10; python _bridge\\local_mcp_hub.py validate",
        "current_turn_evidence_rule": "use per-target probe/recommendation evidence; do not hard-bind all Codex traffic to one proxy endpoint; resource/GitHub/browser/package owners consume network advice without losing their own permission boundary",
    },
    {
        "capability": "github_remote",
        "terms": ["github", "repo", "pull request", "issue"],
        "native_mcp": "github",
        "hub_route": "github.api uses env token then github_app.installation_token then secret_vault:github.token; github.gh uses gh keyring",
        "local_fallback": "gh CLI or GitHub REST with same credential boundary; do not pass tokens as command arguments",
        "permission_boundary": "existing_github_mcp_or_github_app_or_secret_vault_scope",
        "validation_command": "remote status/readback or returned commit SHA",
        "current_turn_evidence_rule": "native GitHub MCP call or Hub response with token_source environment|github_app.installation_token|secret_vault:github.token|gh-keyring evidence",
    },
    {
        "capability": "desktop_weixin",
        "terms": ["desktop-weixin", "weixin", "微信桌面", "cli-anything-weixin"],
        "native_mcp": "desktop-weixin",
        "hub_route": "hub.desktop_weixin.*",
        "local_fallback": "cli-anything-weixin --json status",
        "permission_boundary": "send_requires_explicit_confirmation",
        "validation_command": "desktop_weixin status or screenshot/readback",
        "current_turn_evidence_rule": "desktop UI operations are session-native-first and must return current UI evidence; Hub status is not a substitute for the bound session",
    },
    {
        "capability": "windows_desktop_automation",
        "terms": [
            "windows desktop",
            "windows-mcp",
            "screenshot",
            "snapshot",
            "waitfor",
            "uia",
            "generic gui",
        ],
        "native_mcp": "gui-automation|desktop-weixin|future-windows-mcp",
        "hub_route": "hub.desktop_weixin.* for Weixin only; none for generic Windows-MCP until explicitly registered",
        "local_fallback": "gui-automation or app-specific CLI-Anything harness under the same permission boundary",
        "permission_boundary": "read_snapshot_first_actions_guarded_high_risk_tools_disabled_by_default",
        "validation_command": "read-only screenshot/snapshot smoke plus harmless WaitFor before any action",
        "current_turn_evidence_rule": "candidate capabilities do not prove availability; a real current-turn read-only call must complete before action tools are trusted",
    },
    {
        "capability": "mobile_bridge",
        "terms": ["mobile-openclaw-bridge", "mobile", "openclaw", "owned-result", "supplement", "bridge.get_pending_batch", "bridge.ack_message"],
        "native_mcp": "mobile-openclaw-bridge",
        "hub_route": "mobile_bridge.get_pending_batch|mobile_bridge.ack_message when those known calls are needed; mcp_gateway.call profile=mobile-openclaw-bridge for other known bridge tools; complete_route only for unknown/diagnostic routing",
        "local_fallback": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id>; python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py supplement-fallback ack-message --thread-id <thread_id> --message-id <message_id>",
        "permission_boundary": "mobile_permission_table_and_owned_result_contract",
        "validation_command": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py supplement-cli-fallback-check; queue state plus receipt/delivery evidence",
        "current_turn_evidence_rule": "current-thread supplement and ack are session-native-first; task lookup/status may use owner CLI or Hub, and all routes must preserve exact thread/task identity",
    },
    {
        "capability": "sqlite_state",
        "terms": [
            "sqlite",
            "database",
            "db",
            "scratch",
            "record-store",
            "record_store",
            "email_state",
            "email scheduler",
            "resource_layer",
            "execution records",
            "structured state",
            "queue",
            "task state",
            "delivery evidence",
            "receipt evidence",
            "scheduler records",
            "inbox",
            "outbox",
            "mail",
            "email",
            "status",
            "状态",
            "结构化状态",
            "队列",
            "任务表",
            "任务状态",
            "收件箱",
            "发件箱",
            "调度",
            "回执",
            "投递",
            "邮件",
        ],
        "native_mcp": "sqlite-scratch|sqlite-bridge-ro|local-mcp-hub-record-store|local-mcp-hub-email-state",
        "hub_route": "current Codex-exposed local-mcp-hub names sqlite_scratch_sqlite_query|sqlite_bridge_sqlite_query|record_store_sqlite_query; newer alias names sqlite_scratch_query|sqlite_bridge_query|record_store_query|email_state_query are also served by Hub after tool metadata refresh",
        "local_fallback": "bounded Python sqlite3 against approved DB only",
        "permission_boundary": "scratch_writes_only_bridge_record_store_and_email_state_read_only",
        "validation_command": "query readback; local-mcp-hub sqlite_bridge_sqlite_query/sqlite_scratch_sqlite_query/record_store_sqlite_query/email_state_query or alias smoke plus bounded SELECT",
        "current_turn_evidence_rule": "state questions should use bounded SQLite MCP/Hub readback before broad log/file scans; query or mutation result must identify target DB boundary; production repair must use the owning business maintenance entrypoint, not direct DB writes",
        "query_priority": "prefer_sqlite_for_structured_state_before_logs",
        "index_file_priority": "for .sqlite/.db/index-backed evidence use sqlite MCP or Hub read-only query before rg, file scans, or broad CLI dumps",
        "record_store_query_entrypoints": [
            "record_store_query",
            "record_store_sqlite_query",
            "python _bridge\\shared\\record_store_maintenance.py query --term <term> --limit 5",
            "python _bridge\\shared\\system_maintenance_cli.py record-store query --term <term> --limit 5",
        ],
        "repair_boundary": "diagnose_with_sqlite_then_repair_via_owner_maintenance_cli_or_api",
    },
]


def matrix_hash() -> str:
    return hashlib.sha256(MATRIX.read_bytes()).hexdigest() if MATRIX.exists() else ""


def route_definition_hash(routes: list[dict[str, Any]] | None = None) -> str:
    """Hash every code-owned input that affects the generated route projection."""
    generated_routes = routes if routes is not None else [route_record(item) for item in MANUAL_ROUTES]
    identity = {
        "schema": SCHEMA,
        "revision": ROUTE_DEFINITION_REVISION,
        "routes": generated_routes,
        "route_policy": direct_hub_route_policy(),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def route_record(item: dict[str, Any]) -> dict[str, Any]:
    capability = item["capability"]
    native_mcp = item["native_mcp"]
    native_profiles = [part.strip() for part in str(native_mcp).split("|") if part.strip()]
    direct_tools: list[str] = []
    direct_hints: list[dict[str, Any]] = []
    for profile in native_profiles or [str(native_mcp)]:
        for tool in direct_hub_tools_for(profile, capability):
            if tool not in direct_tools:
                direct_tools.append(tool)
        for hint in direct_hub_hints_for(profile, capability):
            marker = f"{hint.get('hub_tool') or ''}::{hint.get('profile') or ''}"
            if marker and any(
                f"{existing.get('hub_tool') or ''}::{existing.get('profile') or ''}" == marker
                for existing in direct_hints
            ):
                continue
            direct_hints.append(hint)
    affinity = execution_affinity(native_profiles[0] if native_profiles else str(native_mcp), "", capability)
    hub_step = {
            "id": "hub_mcp_direct",
            "action": "call_known_same_boundary_hub_tool",
            "tools": direct_tools,
            "skip_allowed_only_with_evidence": [
                "no_known_same_boundary_hub_tool",
                "hub_mcp_not_exposed_in_current_turn",
                "hub_mcp_transport_closed",
                "known_direct_hub_tool_insufficient_for_target_schema",
            ],
        }
    discovery_step = {
            "id": "precise_tool_discovery",
            "action": "tool_search_exact_owner_namespace",
            "required_before": "declaring_native_tool_unbound",
        }
    native_step = {
            "id": "native_mcp",
            "action": "call_native_current_turn_tool",
            "required_before": "native_route_selected_by_affinity_or_hub_fallback",
        }
    owner_cli_step = {
            "id": "owner_cli",
            "action": "profile_specific_owner_cli",
            "fallback": item.get("local_fallback", ""),
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
    canonical_chain = [
        discovery_step,
        native_step,
        hub_step,
        gateway_step,
        {
            "id": "local_hub_cli",
            "action": "mcp_session_doctor_complete_route_or_local_hub_cli",
            "use_only_after": "Hub MCP direct/gateway unavailable or insufficient",
        },
        owner_cli_step,
        {
            "id": "terminal_local_read",
            "action": "targeted_rg_read_or_other_bottom_local_structure_read",
            "use_only_after": "same-boundary owner routes unavailable, insufficient, or explicitly inapplicable",
            "fallback": "only bottom local structure reads such as targeted rg/read; owner CLI commands belong to owner_cli",
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
    entry_index = next(index for index, step in enumerate(canonical_chain) if step["id"] == required_first_step)
    fallback_chain = canonical_chain[entry_index:]
    fallback_chain = [
        {
            **step,
            "on_failure_next_step": fallback_chain[index + 1]["id"] if index + 1 < len(fallback_chain) else "terminal_failure",
            "continue_on": ["transport_closed", "tool_unbound", "unsupported_call", "insufficient_result", "dispatch_failure"],
            "stop_on": ["call_completed", "explicit_policy_block", "permission_boundary_violation"],
        }
        for index, step in enumerate(fallback_chain)
    ]
    return {
        "capability": capability,
        **affinity,
        "owner_profile": native_profiles[0] if native_profiles else str(native_mcp),
        "hub_tool": direct_tools[0] if direct_tools else "",
        "native_tool": native_mcp,
        "terms": item["terms"],
        "native_mcp": native_mcp,
        "hub_route": item["hub_route"],
        "direct_hub_tools": direct_tools,
        "direct_hub_hints": direct_hints,
        "fallback_chain": fallback_chain,
        "required_first_step": required_first_step,
        "continuation_policy": {
            "follow_chain_until": ["call_completed", "explicit_policy_block", "permission_boundary_violation", "terminal_failure"],
            "hub_failure_does_not_release_chain": True,
            "direction": "forward_only_from_selected_entry_stage",
            "backward_jump_allowed": False,
        },
        "local_fallback": item["local_fallback"],
        "permission_boundary": item["permission_boundary"],
        "validation_command": item["validation_command"],
        "current_turn_evidence_rule": item["current_turn_evidence_rule"],
        "usage_policy": item.get("usage_policy", ""),
        "fallback_order": item.get("fallback_order", ""),
        "index_file_priority": item.get("index_file_priority", ""),
        "record_store_query_entrypoints": item.get("record_store_query_entrypoints", []),
        "query_priority": item.get("query_priority", ""),
        "repair_boundary": item.get("repair_boundary", ""),
    }


def direct_hub_route_policy() -> dict[str, Any]:
    return {
        "classified_route_order": [
            "select_execution_affinity",
            "select_entry_stage_without_reordering_fallbacks",
            "continue_forward_only_after_failure",
        ],
        "fallback_sequence": [
            "precise_tool_discovery",
            "native_mcp",
            "hub_mcp_direct",
            "hub_mcp_gateway",
            "local_hub_cli",
            "owner_cli",
            "terminal_local_read",
        ],
        "entry_stage_by_affinity": {
            "native_first": "precise_tool_discovery",
            "session_native_first": "precise_tool_discovery",
            "hub_first": "hub_mcp_direct",
            "owner_cli_first": "owner_cli",
            "unclassified": "hub_mcp_gateway",
        },
        "backward_jump_allowed": False,
        "native_failure_order": ["record_current_turn_negative_observation", "continue_same_boundary_route"],
        "complete_route_role": "diagnostic_or_dynamic_route_resolution_not_default_transit",
        "permission_boundary": "same_as_native_tool",
    }


def build(write: bool = False) -> dict[str, Any]:
    routes = [route_record(item) for item in MANUAL_ROUTES]
    payload = {
        "schema": SCHEMA,
        "ok": True,
        "generated_at": now_iso(),
        "source": str(MATRIX),
        "source_sha256": matrix_hash(),
        "route_definition_revision": ROUTE_DEFINITION_REVISION,
        "route_definition_sha256": route_definition_hash(routes),
        "machine_first": True,
        "human_readability_goal": False,
        "route_count": len(routes),
        "routes": routes,
        "execution_affinity_policy": direct_hub_route_policy(),
        "native_failure_policy": direct_hub_route_policy(),
    }
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        payload["written_to"] = str(OUT)
    return payload


def load_or_build() -> dict[str, Any]:
    if OUT.exists():
        try:
            payload = json.loads(OUT.read_text(encoding="utf-8"))
            if (
                payload.get("source_sha256") == matrix_hash()
                and payload.get("route_definition_sha256") == route_definition_hash()
            ):
                return payload
        except Exception:
            pass
    return build(write=False)


LOOKUP_ALIASES: dict[str, list[str]] = {
    "联网": ["external_docs_research", "web research", "搜索"],
    "查资料": ["external_docs_research", "documentation", "docs"],
    "相关知识": ["external_docs_research", "documentation", "docs"],
    "官方文档": ["official docs", "documentation", "docs"],
    "文档": ["documentation", "docs"],
    "issue": ["github", "github_remote"],
    "pull": ["github", "pull request"],
    "pr": ["github", "pull request"],
    "owner": ["native_mcp", "source-owning", "owner"],
    "mcp": ["native_mcp", "hub_route", "mcp"],
}


def lookup_needles(terms: list[str]) -> list[str]:
    needles: list[str] = []
    for term in terms:
        raw = str(term or "").strip().lower()
        if not raw:
            continue
        needles.append(raw)
        needles.extend(token for token in re.split(r"[\s,;|/\\]+", raw) if token)
        raw_tokens = set(re.findall(r"[\w.\-]+", raw))
        for alias_key, aliases in LOOKUP_ALIASES.items():
            key = alias_key.lower()
            non_ascii_phrase = any(ord(ch) > 127 for ch in key)
            if raw == key or key in raw_tokens or (non_ascii_phrase and key in raw):
                needles.extend(alias.lower() for alias in aliases)
    seen: set[str] = set()
    output: list[str] = []
    for needle in needles:
        if needle and needle not in seen:
            seen.add(needle)
            output.append(needle)
    return output


def _lookup_payload(payload: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    needles = lookup_needles(terms)
    matches: list[dict[str, Any]] = []
    for route in payload.get("routes", []):
        route_terms = {str(term).strip().lower() for term in route.get("terms", []) if str(term).strip()}
        identity = " ".join(
            [
                str(route.get("capability") or ""),
                str(route.get("owner_profile") or ""),
                str(route.get("hub_tool") or ""),
                str(route.get("native_mcp") or ""),
                " ".join(str(tool) for tool in route.get("direct_hub_tools", [])),
                " ".join(route_terms),
            ]
        ).lower()
        identity_tokens = set(re.findall(r"[\w.\-]+", identity))
        capability = str(route.get("capability") or "").lower()
        score = 0
        for needle in needles:
            if not needle:
                continue
            if needle == capability or needle in route_terms:
                score += 5
            elif " " in needle and needle in identity:
                score += 3
            elif needle in identity_tokens:
                score += 2
        if score:
            matches.append({"score": score, **route})
    matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("capability") or "")))
    return {
        "schema": f"{SCHEMA}.lookup",
        "ok": True,
        "generated_at": now_iso(),
        "terms": terms,
        "expanded_terms": needles,
        "matches": matches[:8],
        "route_index": str(OUT),
    }


def lookup(terms: list[str]) -> dict[str, Any]:
    return _lookup_payload(load_or_build(), terms)


def validate() -> dict[str, Any]:
    payload = build(write=False)
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {
        "capability",
        "terms",
        "native_mcp",
        "hub_route",
        "local_fallback",
        "permission_boundary",
        "validation_command",
        "current_turn_evidence_rule",
        "execution_affinity",
        "session_binding",
        "priority_source",
        "priority_reason",
        "priority_explicit",
        "required_first_step",
        "continuation_policy",
        "owner_profile",
    }
    for route in payload["routes"]:
        missing = sorted(key for key in required if key not in route or route[key] in ("", []))
        if missing:
            issues.append({"severity": "risk", "code": "route_missing_fields", "capability": route.get("capability"), "fields": missing})
        capability = str(route.get("capability") or "")
        if capability in seen:
            issues.append({"severity": "risk", "code": "duplicate_capability", "capability": capability})
        seen.add(capability)
        if route.get("execution_affinity") == "unclassified" or route.get("priority_explicit") is not True:
            issues.append({"severity": "risk", "code": "route_priority_unclassified", "capability": capability})
        if not re.match(r"^[a-z0-9_]+$", capability):
            issues.append({"severity": "risk", "code": "unstable_capability_key", "capability": capability})
        fallback_chain = route.get("fallback_chain") if isinstance(route.get("fallback_chain"), list) else []
        chain_ids = [str(step.get("id") or "") for step in fallback_chain if isinstance(step, dict)]
        required_steps_by_affinity = {
            "native_first": ("precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"),
            "session_native_first": ("precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"),
            "hub_first": ("hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"),
            "owner_cli_first": ("owner_cli", "terminal_local_read"),
        }
        for required_step in required_steps_by_affinity.get(str(route.get("execution_affinity") or ""), ()):
            if required_step not in chain_ids:
                issues.append({"severity": "risk", "code": "fallback_chain_missing_step", "capability": capability, "step": required_step})
        for step in fallback_chain:
            if not step.get("on_failure_next_step"):
                issues.append({"severity": "risk", "code": "fallback_step_missing_continuation", "capability": capability, "step": step.get("id")})
        if route.get("continuation_policy", {}).get("hub_failure_does_not_release_chain") is not True:
            issues.append({"severity": "risk", "code": "hub_failure_chain_continuation_missing", "capability": capability})
        if route.get("continuation_policy", {}).get("backward_jump_allowed") is not False:
            issues.append({"severity": "risk", "code": "fallback_backward_jump_not_disabled", "capability": capability})
        canonical_steps = ["precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"]
        if chain_ids != [item for item in canonical_steps if item in chain_ids]:
            issues.append({"severity": "risk", "code": "fallback_chain_wrong_order", "capability": capability, "chain": chain_ids})
        if route.get("execution_affinity") == "hub_first" and any(item in chain_ids for item in ("precise_tool_discovery", "native_mcp")):
            issues.append({"severity": "risk", "code": "hub_first_backward_native_fallback", "capability": capability})
        local_fallback_text = str(route.get("local_fallback") or "").lower()
        if ("rg " in local_fallback_text or "direct skill.md read" in local_fallback_text or "direct file" in local_fallback_text) and "terminal_local_read" not in chain_ids:
            issues.append({"severity": "risk", "code": "bottom_local_read_not_terminal", "capability": capability})
    sqlite_route = next((route for route in payload["routes"] if route.get("capability") == "sqlite_state"), {})
    if sqlite_route:
        if sqlite_route.get("query_priority") != "prefer_sqlite_for_structured_state_before_logs":
            issues.append({"severity": "risk", "code": "sqlite_query_priority_missing", "capability": "sqlite_state"})
        if "sqlite MCP or Hub read-only query before rg" not in str(sqlite_route.get("index_file_priority") or ""):
            issues.append({"severity": "risk", "code": "sqlite_index_file_priority_missing", "capability": "sqlite_state"})
        if not sqlite_route.get("record_store_query_entrypoints"):
            issues.append({"severity": "risk", "code": "record_store_query_entrypoints_missing", "capability": "sqlite_state"})
        if sqlite_route.get("repair_boundary") != "diagnose_with_sqlite_then_repair_via_owner_maintenance_cli_or_api":
            issues.append({"severity": "risk", "code": "sqlite_repair_boundary_missing", "capability": "sqlite_state"})
    else:
        issues.append({"severity": "risk", "code": "sqlite_state_route_missing", "capability": "sqlite_state"})
    code_route = next((route for route in payload["routes"] if route.get("capability") == "code_structure"), {})
    if code_route:
        if "source_structure" not in str(code_route.get("usage_policy") or ""):
            issues.append({"severity": "risk", "code": "codegraph_usage_policy_missing", "capability": "code_structure"})
        if "hub_codegraph_explore_then_hub_gateway_then_local_codegraph_cli" not in str(code_route.get("fallback_order") or ""):
            issues.append({"severity": "risk", "code": "codegraph_fallback_order_missing", "capability": "code_structure"})
        if "codegraph.explore" not in (code_route.get("direct_hub_tools") or []):
            issues.append({"severity": "risk", "code": "codegraph_direct_hub_tool_missing", "capability": "code_structure"})
        code_chain_ids = [str(step.get("id") or "") for step in code_route.get("fallback_chain", []) if isinstance(step, dict)]
        if "terminal_local_read" not in code_chain_ids or code_chain_ids.index("terminal_local_read") <= code_chain_ids.index("hub_mcp_direct"):
            issues.append({"severity": "risk", "code": "codegraph_terminal_read_before_hub", "capability": "code_structure"})
    else:
        issues.append({"severity": "risk", "code": "code_structure_route_missing", "capability": "code_structure"})
    browser_route = next((route for route in payload["routes"] if route.get("capability") == "browser_session"), {})
    browser_chain = [str(step.get("id") or "") for step in browser_route.get("fallback_chain", []) if isinstance(step, dict)]
    if browser_route.get("execution_affinity") != "session_native_first" or not browser_chain or browser_chain[0] != "precise_tool_discovery":
        issues.append({"severity": "risk", "code": "browser_session_affinity_invalid", "route": browser_route})
    browser_lookup = _lookup_payload(payload, ["browser session chrome"])
    browser_matches = browser_lookup.get("matches") if isinstance(browser_lookup.get("matches"), list) else []
    if not browser_matches or browser_matches[0].get("capability") != "browser_session":
        issues.append({"severity": "risk", "code": "browser_session_lookup_misrouted", "matches": [item.get("capability") for item in browser_matches[:3]]})
    lookup_probe = _lookup_payload(payload, ["联网 GitHub issue OpenAI 官方文档 owner MCP"])
    capabilities = [str(item.get("capability") or "") for item in lookup_probe.get("matches", [])]
    if "external_docs_research" not in capabilities or "github_remote" not in capabilities:
        issues.append(
            {
                "severity": "risk",
                "code": "compound_lookup_route_missing",
                "capability": "external_docs_research|github_remote",
                "matches": capabilities,
            }
        )
    policy = direct_hub_route_policy()
    if policy.get("complete_route_role") != "diagnostic_or_dynamic_route_resolution_not_default_transit":
        issues.append({"severity": "risk", "code": "complete_route_role_regressed"})
    if "select_execution_affinity" not in policy.get("classified_route_order", []):
        issues.append({"severity": "risk", "code": "classified_execution_affinity_missing"})
    if policy.get("backward_jump_allowed") is not False:
        issues.append({"severity": "risk", "code": "policy_backward_jump_allowed"})
    if policy.get("fallback_sequence") != ["precise_tool_discovery", "native_mcp", "hub_mcp_direct", "hub_mcp_gateway", "local_hub_cli", "owner_cli", "terminal_local_read"]:
        issues.append({"severity": "risk", "code": "policy_fallback_sequence_invalid"})
    stability_route = next((route for route in payload["routes"] if route.get("capability") == "mcp_stability"), {})
    if "known same-boundary Hub tool directly" not in str(stability_route.get("hub_route", "")):
        issues.append({"severity": "risk", "code": "mcp_stability_direct_hub_rule_missing"})
    contract = route_contract_check(route_policy())
    if not contract.get("ok"):
        issues.append({"severity": "risk", "code": "mcp_route_policy_contract_failed", "details": contract.get("issues")})
    github_priority = call_priority_pack("github", "search_repositories", "github_remote")
    hub_step = next((step for step in github_priority.get("steps", []) if step.get("id") == "hub_mcp_direct"), {})
    direct_hub_tools = hub_step.get("tools", [])
    if "github.api" not in direct_hub_tools or "github.gh" not in direct_hub_tools:
        issues.append({"severity": "risk", "code": "github_direct_hub_tools_missing", "tools": direct_hub_tools})
    required_direct_hub = {
        "github_remote": "github.api",
        "resource_acquisition": "resource.request",
        "mobile_bridge": "mobile_bridge.get_pending_batch",
    }
    for capability, expected_tool in required_direct_hub.items():
        route = next((item for item in payload["routes"] if item.get("capability") == capability), {})
        tools = route.get("direct_hub_tools") if isinstance(route.get("direct_hub_tools"), list) else []
        if expected_tool not in tools:
            issues.append({"severity": "risk", "code": "direct_hub_tool_missing", "capability": capability, "expected": expected_tool, "tools": tools})
    for profile in ("openai-docs", "context7", "microsoftdocs"):
        tools = direct_hub_tools_for(profile, "external_docs_research")
        if "owner_mcp.call_readonly" not in tools:
            issues.append({"severity": "risk", "code": "owner_docs_readonly_hub_call_missing", "profile": profile, "tools": tools})
    filesystem_route = next((item for item in payload["routes"] if item.get("capability") == "local_filesystem_read"), {})
    filesystem_chain = [
        str(step.get("id") or "")
        for step in filesystem_route.get("fallback_chain", [])
        if isinstance(step, dict)
    ]
    filesystem_lookup = _lookup_payload(payload, ["filesystem", "filesystem-admin", "read_multiple_files"])
    filesystem_matches = filesystem_lookup.get("matches") if isinstance(filesystem_lookup.get("matches"), list) else []
    if not filesystem_route:
        issues.append({"severity": "risk", "code": "filesystem_read_route_missing", "capability": "local_filesystem_read"})
    if filesystem_route and filesystem_route.get("execution_affinity") != "hub_first":
        issues.append({"severity": "risk", "code": "filesystem_read_affinity_not_hub_first", "route": filesystem_route})
    if filesystem_route and filesystem_route.get("required_first_step") != "hub_mcp_direct":
        issues.append({"severity": "risk", "code": "filesystem_read_first_step_invalid", "route": filesystem_route})
    if filesystem_route and filesystem_route.get("owner_profile") != "filesystem":
        issues.append({"severity": "risk", "code": "filesystem_read_owner_not_read_only_profile", "route": filesystem_route})
    if filesystem_route and (filesystem_route.get("direct_hub_tools") or []) != ["owner_mcp.call_readonly"]:
        issues.append({"severity": "risk", "code": "filesystem_read_direct_hub_tool_missing", "route": filesystem_route})
    mutating_tools = {"write_file", "edit_file", "move_file", "create_directory", "delete_file", "remove_file"}
    advertised_tools = {
        str(tool)
        for hint in filesystem_route.get("direct_hub_hints", [])
        if isinstance(hint, dict)
        for tool in (hint.get("typical_tools") or [])
    }
    if mutating_tools.intersection(advertised_tools):
        issues.append({"severity": "risk", "code": "filesystem_read_route_advertises_mutation", "tools": sorted(mutating_tools.intersection(advertised_tools))})
    if filesystem_route and filesystem_route.get("permission_boundary") != "same_filesystem_read_only_boundary_no_write_permission_expansion":
        issues.append({"severity": "risk", "code": "filesystem_read_permission_boundary_invalid", "route": filesystem_route})
    if any(step in filesystem_chain for step in ("precise_tool_discovery", "native_mcp")):
        issues.append({"severity": "risk", "code": "filesystem_read_native_stage_present", "chain": filesystem_chain})
    if not filesystem_matches or filesystem_matches[0].get("capability") != "local_filesystem_read":
        issues.append(
            {
                "severity": "risk",
                "code": "filesystem_read_lookup_misrouted",
                "matches": [item.get("capability") for item in filesystem_matches[:3]],
            }
        )
    current_definition_hash = route_definition_hash(payload["routes"])
    if payload.get("route_definition_sha256") != current_definition_hash:
        issues.append({"severity": "risk", "code": "route_definition_identity_stale"})
    risk_free = not any(issue.get("severity") == "risk" for issue in issues)
    checks = [
        {"name": "route_inventory_non_empty", "ok": bool(payload["routes"]), "count": len(payload["routes"])},
        {"name": "route_definition_identity_current", "ok": payload.get("route_definition_sha256") == current_definition_hash},
        {"name": "filesystem_read_route_present", "ok": bool(filesystem_route)},
        {"name": "filesystem_read_affinity_hub_first", "ok": filesystem_route.get("execution_affinity") == "hub_first"},
        {"name": "filesystem_read_first_step_hub_direct", "ok": filesystem_route.get("required_first_step") == "hub_mcp_direct"},
        {"name": "filesystem_read_owner_profile_is_read_only", "ok": filesystem_route.get("owner_profile") == "filesystem"},
        {"name": "filesystem_read_owner_adapter_exclusive", "ok": (filesystem_route.get("direct_hub_tools") or []) == ["owner_mcp.call_readonly"]},
        {"name": "filesystem_read_hints_exclude_mutations", "ok": not mutating_tools.intersection(advertised_tools)},
        {"name": "filesystem_read_permission_boundary_preserved", "ok": filesystem_route.get("permission_boundary") == "same_filesystem_read_only_boundary_no_write_permission_expansion"},
        {"name": "filesystem_read_chain_has_no_native_stage", "ok": not any(step in filesystem_chain for step in ("precise_tool_discovery", "native_mcp"))},
        {"name": "filesystem_read_lookup_resolves", "ok": bool(filesystem_matches) and filesystem_matches[0].get("capability") == "local_filesystem_read"},
        {"name": "route_contracts_risk_free", "ok": risk_free, "issue_count": len(issues)},
    ]
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": risk_free and all(check.get("ok") is True for check in checks),
        "generated_at": now_iso(),
        "route_count": len(payload["routes"]),
        "checks": checks,
        "issues": issues,
    }


def cli_projection(payload: dict[str, Any], command: str, *, full: bool = False) -> dict[str, Any]:
    """Keep default CLI output concise while preserving the complete route artifact."""
    if command in {"build", "snapshot"} and isinstance(payload.get("routes"), list):
        summary = {
            "schema": payload.get("schema"),
            "ok": payload.get("ok"),
            "generated_at": payload.get("generated_at"),
            "source": payload.get("source"),
            "source_sha256": payload.get("source_sha256"),
            "route_definition_revision": payload.get("route_definition_revision"),
            "route_definition_sha256": payload.get("route_definition_sha256"),
            "route_count": payload.get("route_count"),
            "routes": [
                {
                    "capability": route.get("capability"),
                    "profile": route.get("profile"),
                    "execution_affinity": route.get("execution_affinity"),
                    "required_first_step": route.get("required_first_step"),
                }
                for route in payload.get("routes", [])
                if isinstance(route, dict)
            ],
            "route_index": str(payload.get("written_to") or OUT),
            "detail_rule": "read the route index or use lookup for full route details",
        }
        return bounded_payload(
            summary,
            max_bytes=8 * 1024,
            max_items=24,
            preserve_keys=("schema", "ok", "generated_at", "route_count", "route_index"),
            artifact_ref=str(payload.get("written_to") or OUT),
        )
    if command == "validate":
        return aggregate_validator_cli_payload(
            payload,
            full=full,
            full_result_ref="command:python _bridge/mcp_capability_routes.py validate --full",
        )
    return bounded_payload(
        payload,
        max_bytes=12 * 1024,
        max_items=30,
        preserve_keys=("schema", "ok", "generated_at", "route_count", "issues", "matches"),
        artifact_ref=str(payload.get("route_index") or payload.get("written_to") or OUT),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and query machine-first MCP capability routes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    sub.add_parser("snapshot")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--full", action="store_true", help="Emit the complete validation result.")
    lookup_parser = sub.add_parser("lookup")
    lookup_parser.add_argument("--terms", nargs="*", default=[])
    lookup_parser.add_argument("--query", default="", help="Compatibility alias for older callers; split into lookup terms.")
    args = parser.parse_args(argv)
    if args.command == "build":
        payload = build(write=True)
    elif args.command == "snapshot":
        payload = load_or_build()
    elif args.command == "validate":
        payload = validate()
    else:
        terms = list(args.terms or [])
        if args.query:
            terms.extend(re.findall(r"[\w.\-:/\\]+", args.query))
        payload = lookup(terms)
    print(json.dumps(cli_projection(payload, args.command, full=bool(getattr(args, "full", False))), ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
