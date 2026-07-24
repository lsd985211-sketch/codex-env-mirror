#!/usr/bin/env python3
"""Tool specification builders for the local MCP Hub."""

from __future__ import annotations

from typing import Any


def pmb_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "pmb.workspace_info",
            "description": "Read PMB workspace identity and database path through the warm local daemon.",
            "annotations": {"title": "PMB Workspace Info", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "pmb.prepare",
            "description": "Read-first PMB bundle for a task message. Returns project context, lessons, recent activity, and open goals.",
            "annotations": {"title": "PMB Prepare", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False},
        },
        {
            "name": "pmb.recall",
            "description": "Search PMB memory for relevant project/user/tool facts.",
            "annotations": {"title": "PMB Recall", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20}, "project": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "pmb.project_overview",
            "description": "Read PMB overview for a named project.",
            "annotations": {"title": "PMB Project Overview", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
        },
        {
            "name": "pmb.stats",
            "description": "Read PMB workspace and memory statistics.",
            "annotations": {"title": "PMB Stats", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "pmb.list_goals",
            "description": "Read open PMB goals with optional status and limit.",
            "annotations": {"title": "PMB List Goals", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                "additionalProperties": False,
            },
        },
    ]


def gateway_tool_specs() -> list[dict[str, Any]]:
    profile_schema = {
        "type": "string",
        "description": "Configured MCP profile name such as filesystem, filesystem-admin, sqlite-scratch, local-pmb-memory, mobile-openclaw-bridge, gui-automation, chrome-devtools, playwright, github, or myskills.",
    }
    return [
        {
            "name": "mcp_gateway.route",
            "description": "Read the governed Hub fallback route for one MCP profile/tool. This is diagnostic and does not call the target tool.",
            "annotations": {"title": "MCP Gateway Route", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"profile": profile_schema, "tool": {"type": "string"}},
                "required": ["profile"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mcp_gateway.call",
            "description": "Call a target MCP through the controlled fresh-stdio gateway only after native MCP is unavailable in the current turn. Original permissions still apply.",
            "annotations": {"title": "MCP Gateway Call", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": profile_schema,
                    "tool": {"type": "string", "description": "Target MCP tool name exactly as exposed by the target server."},
                    "arguments": {"type": "object", "description": "Target MCP tool arguments.", "additionalProperties": True},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "fallback_ack": {
                        "type": "string",
                        "description": "Required exact acknowledgement: native-mcp-unavailable-and-original-permissions-apply",
                    },
                },
                "required": ["profile", "tool", "fallback_ack"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mcp_gateway.complete_route",
            "description": "Hub diagnostic route handler after a native MCP current-turn failure when no known same-boundary Hub tool should be called directly, or when route/permission/schema evidence is needed. Records negative evidence, attempts the governed gateway call when supported, and returns compact route evidence. Use CLI complete-route only when Hub is unavailable.",
            "annotations": {"title": "MCP Gateway Complete Route", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": profile_schema,
                    "tool": {"type": "string", "description": "Target MCP tool name exactly as exposed by the target server."},
                    "status": {"type": "string", "description": "Native current-turn failure status such as transport_closed or tool_unbound."},
                    "detail": {"type": "string", "description": "Short current-turn failure detail."},
                    "arguments": {"type": "object", "description": "Target MCP tool arguments.", "additionalProperties": True},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "fallback_ack": {
                        "type": "string",
                        "description": "Required exact acknowledgement: native-mcp-unavailable-and-original-permissions-apply",
                    },
                },
                "required": ["profile", "tool", "fallback_ack"],
                "additionalProperties": False,
            },
        },
    ]


def metamcp_lab_tool_specs() -> list[dict[str, Any]]:
    ack = "gateway-lab-readonly-and-production-native-first"
    return [
        {
            "name": "metamcp_lab.catalog",
            "description": "Return a compact catalog of tools behind the isolated MetaMCP lab without expanding every child tool schema. Includes per-child network route metadata when configured.",
            "annotations": {"title": "MetaMCP Lab Catalog", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"include_descriptions": {"type": "boolean"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "metamcp_lab.search",
            "description": "Search the isolated MetaMCP lab catalog by compact tool/server text. Use before describing a tool to keep token cost low.",
            "annotations": {"title": "MetaMCP Lab Search", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "metamcp_lab.describe",
            "description": "Expand one isolated MetaMCP lab tool schema on demand. This avoids exposing all child MCP schemas every turn.",
            "annotations": {"title": "MetaMCP Lab Describe", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"tool": {"type": "string"}},
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
        {
            "name": "metamcp_lab.call_readonly",
            "description": "Call one read-only tool through the isolated MetaMCP lab. Requires explicit lab acknowledgement and never expands production permissions.",
            "annotations": {"title": "MetaMCP Lab Read-Only Call", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": True},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 180},
                    "gateway_lab_ack": {"type": "string", "description": f"Required exact acknowledgement: {ack}"},
                },
                "required": ["tool", "gateway_lab_ack"],
                "additionalProperties": False,
            },
        },
        {
            "name": "metamcp_lab.validate",
            "description": "Validate the Hub-facing isolated MetaMCP lab adapter and summarize tool/network metadata. Does not integrate with production.",
            "annotations": {"title": "MetaMCP Lab Validate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def codegraph_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "codegraph.explore",
            "description": "Hub-first CodeGraph explore using a validated local index with non-blocking stale-while-refresh freshness handling.",
            "annotations": {"title": "CodeGraph Explore", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Architecture, symbol, file, or flow question."},
                    "projectPath": {"type": "string", "description": "Absolute project path. Defaults to this workspace."},
                    "maxFiles": {"type": "integer", "minimum": 1, "maximum": 12},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "freshness_targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit files/directories for deterministic target freshness evidence. If omitted, obvious paths are extracted from the query.",
                    },
                    "exclude_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional paths that must not appear in accepted results. Scope acceptance is checked after exploration.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
    ]


def graph_tool_specs() -> list[dict[str, Any]]:
    timeout = {"type": "integer", "minimum": 1, "maximum": 120}
    return [
        {
            "name": "gitnexus.list_tools",
            "description": "List the current GitNexus MCP tool catalog through a fresh, isolated WSL stdio session. No index, hook, or editor configuration is created.",
            "annotations": {"title": "GitNexus List Tools", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"working_directory": {"type": "string"}, "timeout_seconds": timeout}, "additionalProperties": False},
        },
        {
            "name": "gitnexus.call",
            "description": "Call an upstream GitNexus MCP tool through a fresh WSL stdio session. Only tools that GitNexus advertises as read-only are forwarded.",
            "annotations": {"title": "GitNexus MCP Call", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}, "working_directory": {"type": "string"}, "timeout_seconds": timeout},
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
        {
            "name": "graphify.list_tools",
            "description": "List the current Graphify graph MCP tool catalog for a managed local graph.json through a fresh stdio session.",
            "annotations": {"title": "Graphify List Tools", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"graph_path": {"type": "string"}, "timeout_seconds": timeout}, "additionalProperties": False},
        },
        {
            "name": "graphify.call",
            "description": "Call an upstream Graphify MCP tool for a managed local graph.json. Only a fixed query-only allowlist for the pinned Graphify version is forwarded.",
            "annotations": {"title": "Graphify MCP Call", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}, "graph_path": {"type": "string"}, "timeout_seconds": timeout},
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
    ]


def headroom_tool_specs() -> list[dict[str, Any]]:
    timeout = {"type": "integer", "minimum": 1, "maximum": 120}
    return [
        {
            "name": "headroom.compress",
            "description": "Compress large JSON, logs, code, search results, or tool output into a smaller reversible representation. Use the returned hash with headroom.retrieve only when omitted detail is needed.",
            "annotations": {"title": "Headroom Compress", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"content": {"type": "string"}, "timeout_seconds": timeout},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "headroom.retrieve",
            "description": "Retrieve the original content for a hash returned by headroom.compress while its 30-minute context-cache entry remains valid.",
            "annotations": {"title": "Headroom Retrieve", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"hash": {"type": "string", "pattern": "^[0-9a-fA-F]{12,64}$"}, "timeout_seconds": timeout},
                "required": ["hash"],
                "additionalProperties": False,
            },
        },
        {
            "name": "headroom.stats",
            "description": "Read Headroom compression and retrieval statistics for the current TTL context cache. This is not PMB or long-term memory statistics.",
            "annotations": {"title": "Headroom Stats", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"timeout_seconds": timeout}, "additionalProperties": False},
        },
    ]


def chrome_devtools_tool_specs() -> list[dict[str, Any]]:
    """Return governed Hub aliases for Chrome DevTools gateway fallback.

    The target chrome-devtools MCP schemas can evolve, so these aliases keep a
    narrow wrapper contract and pass all non-wrapper fields through unchanged.
    """

    fallback_ack = {
        "type": "string",
        "description": "Required exact acknowledgement: native-mcp-unavailable-and-original-permissions-apply",
    }
    timeout_seconds = {"type": "integer", "minimum": 1, "maximum": 120}

    def spec(
        tool: str,
        description: str,
        *,
        read_only: bool,
        destructive: bool = False,
        open_world: bool = False,
    ) -> dict[str, Any]:
        return {
            "name": f"chrome_devtools.{tool}",
            "description": (
                f"{description} Uses the existing chrome-devtools MCP through the governed Hub gateway after "
                "native current-turn failure; original permissions and browser policy still apply."
            ),
            "annotations": {
                "title": f"Chrome DevTools {tool.replace('_', ' ').title()}",
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": False,
                "openWorldHint": open_world,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fallback_ack": fallback_ack,
                    "timeout_seconds": timeout_seconds,
                },
                "required": ["fallback_ack"],
                "additionalProperties": True,
            },
        }

    return [
        spec("list_pages", "List Chrome pages/targets.", read_only=True),
        spec("new_page", "Open a new Chrome page/target.", read_only=False, open_world=True),
        spec("select_page", "Select an existing Chrome page/target.", read_only=False),
        spec("navigate_page", "Navigate a Chrome page/target.", read_only=False, open_world=True),
        spec("take_snapshot", "Capture the accessibility/DOM snapshot for a Chrome page.", read_only=True),
        spec("evaluate_script", "Evaluate JavaScript in a Chrome page.", read_only=False),
        spec("take_screenshot", "Capture a screenshot from a Chrome page.", read_only=True),
        spec("list_console_messages", "List console messages for a Chrome page.", read_only=True),
        spec("list_network_requests", "List network requests for a Chrome page.", read_only=True),
        spec("wait_for", "Wait for a specific page condition or text.", read_only=True),
        spec("resize_page", "Resize a Chrome page viewport.", read_only=False),
        spec("close_page", "Close a Chrome page/target.", read_only=False, destructive=True),
    ]


def github_tool_specs() -> list[dict[str, Any]]:
    write_ack = "github-write-through-hub-uses-existing-permissions"
    return [
        {
            "name": "github.api",
            "description": "Call GitHub REST API through the Hub using environment credentials, GitHub App installation tokens, or Secret Vault PAT fallback. Supports read and write methods under the original credential permissions.",
            "annotations": {"title": "GitHub REST API", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"]},
                    "path": {"type": "string", "description": "GitHub API path, for example /repos/owner/repo or repos/owner/repo/issues."},
                    "query": {"type": "object", "additionalProperties": True},
                    "body": {"type": "object", "additionalProperties": True},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "write_ack": {"type": "string", "description": f"Required for non-GET methods: {write_ack}"},
                },
                "required": ["method", "path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "github.gh",
            "description": "Run the local GitHub CLI through the Hub. Full GitHub CLI capability is available under existing gh/keyring permissions; token-printing commands are blocked.",
            "annotations": {"title": "GitHub CLI", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments after gh, for example ['repo','view','owner/repo','--json','name']."},
                    "stdin": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                    "write_ack": {"type": "string", "description": f"Required for likely mutating gh commands: {write_ack}"},
                },
                "required": ["args"],
                "additionalProperties": False,
            },
        },
    ]


def github_app_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "github_app.snapshot",
            "description": "Read non-secret GitHub App auth configuration status from Secret Vault aliases.",
            "annotations": {"title": "GitHub App Snapshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "github_app.doctor",
            "description": "Diagnose GitHub App Secret Vault aliases and local JWT generation without exposing secret values.",
            "annotations": {"title": "GitHub App Doctor", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "github_app.validate",
            "description": "Validate GitHub App authentication. Offline mode checks local JWT generation; online mode exchanges for a redacted installation token.",
            "annotations": {"title": "GitHub App Validate", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {"online": {"type": "boolean"}},
                "additionalProperties": False,
            },
        },
    ]


def secret_vault_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "secret_vault.snapshot",
            "description": "Read non-secret Secret Vault metadata and backend availability. Never returns secret values.",
            "annotations": {"title": "Secret Vault Snapshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "secret_vault.doctor",
            "description": "Diagnose Secret Vault metadata and Windows Credential Manager backend without exposing secret values.",
            "annotations": {"title": "Secret Vault Doctor", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "secret_vault.validate",
            "description": "Run a non-secret Secret Vault round-trip self-test using a temporary synthetic value.",
            "annotations": {"title": "Secret Vault Validate", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def resource_tool_specs() -> list[dict[str, Any]]:
    request_properties: dict[str, Any] = {
        "target": {"type": "string", "description": "URL or local path. Optional when url/path is provided."},
        "url": {"type": "string"},
        "path": {"type": "string"},
        "task": {"type": "string"},
        "name": {"type": "string"},
        "intent": {
            "type": "string",
            "description": "Resource intent such as explicit_local_file, explicit_user_url, documentation_lookup, external_dependency, package_dependency, explicit_attachment_local, or explicit_attachment_url.",
        },
        "need_materialization": {"type": "boolean"},
        "allow_network": {"type": "boolean"},
        "allow_filesystem_write": {"type": "boolean"},
        "max_bytes": {"type": "integer", "minimum": 1},
        "expected_sha256": {"type": "string"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
        "retry_budget": {"type": "integer", "minimum": 0, "maximum": 5},
        "target_dir": {"type": "string"},
        "store_root": {"type": "string", "description": "Optional resource manifest/store root. Defaults to the workspace resource store."},
        "auto_owner": {"type": "boolean", "description": "Run supported read-only owner adapters before returning a handoff contract."},
        "owner_execution_mode": {"type": "string", "enum": ["read_only"]},
        "metadata": {"type": "object", "additionalProperties": True},
    }
    batch_request_properties = {key: value for key, value in request_properties.items() if key != "store_root"}
    batch_item_properties: dict[str, Any] = {
        **batch_request_properties,
        "item_id": {"type": "string", "description": "Stable item identifier preserved in status, progress, refinement, and aggregate receipts."},
        "required": {"type": "boolean", "description": "Whether this item must satisfy acceptance before the batch can complete."},
        "acceptance": {
            "type": "object",
            "properties": {
                "minimum_candidates": {"type": "integer", "minimum": 0},
                "minimum_quantity": {"type": "integer", "minimum": 0},
                "provenance_required": {"type": "boolean"},
                "consumable_required": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        "quantity": {"type": "object", "additionalProperties": True},
        "source": {"type": "object", "additionalProperties": True},
        "freshness": {"type": "object", "additionalProperties": True},
        "diversity": {"type": "object", "additionalProperties": True},
    }
    return [
        {
            "name": "resource.request",
            "description": "Submit a structured resource request to the local broker. Resource requests authorize automatic owner-tool orchestration only inside resource-acquisition boundaries; owner MCP/browser/domain/package-manager results must be attached back to the same request.",
            "annotations": {"title": "Resource Request", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {"type": "object", "properties": request_properties, "additionalProperties": False},
        },
        {
            "name": "resource.request_batch",
            "description": "Submit multiple independent structured resource needs as one bounded job. Each item keeps its own target, routing fields, acceptance predicate, status, and retry boundary; the aggregate completes only when every required item is accepted.",
            "annotations": {"title": "Resource Batch Request", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "enum": ["resource.batch_request.v1"]},
                    "batch_name": {"type": "string"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": batch_item_properties,
                            "required": ["item_id"],
                            "additionalProperties": False,
                        },
                    },
                    "execution": {
                        "type": "object",
                        "properties": {
                            "max_active": {"type": "integer", "minimum": 1, "maximum": 32},
                            "per_host_limit": {"type": "integer", "minimum": 1, "maximum": 16},
                            "fail_fast": {"type": "boolean"},
                            "plan_only": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    "store_root": {"type": "string"},
                    "detail": {"type": "string", "enum": ["compact", "full"]},
                    "item_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
        {
            "name": "resource.status",
            "description": "Read the latest local resource broker receipt for a request id.",
            "annotations": {"title": "Resource Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"request_id": {"type": "string"}},
                "required": ["request_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "resource.progress",
            "description": "Read a compact conversation-oriented progress/result view for one resource request, request manifest, or batch manifest.",
            "annotations": {"title": "Resource Progress", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "manifest_path": {"type": "string"},
                    "batch_manifest_path": {"type": "string"},
                    "include_items": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "resource.attach_result",
            "description": "Attach the result produced by an owner MCP/tool to an existing resource request manifest and receipt.",
            "annotations": {"title": "Resource Attach Result", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "source_tool": {"type": "string"},
                    "result_kind": {"type": "string"},
                    "content": {"type": "string"},
                    "artifact_path": {"type": "string"},
                    "metadata": {"type": "object", "additionalProperties": True},
                },
                "required": ["request_id", "source_tool"],
                "additionalProperties": False,
            },
        },
    ]


def workflow_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "workflow.route_pack",
            "description": "Return the compact per-turn execution route pack for a task message without exposing the full workflow plan.",
            "annotations": {
                "title": "Workflow Route Pack",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Task message to classify and route."},
                    "risk": {"type": "string", "description": "Optional task risk label. Defaults to unknown."},
                    "detail": {
                        "type": "string",
                        "enum": ["micro", "standard", "full", "auto"],
                        "description": "Workflow detail level used to build the route pack. Defaults to micro.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        }
    ]


def network_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "network_gateway.snapshot",
            "description": "Read the Codex-facing network gateway control-plane snapshot. Does not modify system proxy, DNS, Clash config, or Codex conversation routing.",
            "annotations": {"title": "Network Gateway Snapshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "network_gateway.interfaces",
            "description": "Return the stable request interfaces supported by the Codex network gateway, including the protected current Codex model baseurl and separate official OpenAI experiment target.",
            "annotations": {"title": "Network Gateway Interfaces", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "network_gateway.plan",
            "description": "Return a Codex-facing route plan for one target kind. With probe=true, compares direct and current proxy for this request and returns probe_selected_direct/proxy plus per-process env/unset_env.",
            "annotations": {"title": "Network Gateway Plan", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_kind": {"type": "string", "description": "codex_chat/codex_model_api for the current Codex configured baseurl, or openai/github/package/docs/browser/paper/image/dataset/web/external."},
                    "target": {"type": "string", "description": "Optional explicit URL. Overrides target_kind default."},
                    "owner_tool": {"type": "string", "description": "Optional source owner such as github, microsoftdocs, context7, playwright, resource_router, or package_manager; included in route-cache identity."},
                    "runtime": {"type": "string", "description": "generic, node, python, uv, pip, curl, browser, etc."},
                    "isolation": {"type": "string", "enum": ["auto", "never", "prefer", "required", "wrapper"]},
                    "group": {"type": "string"},
                    "node": {"type": "string"},
                    "probe": {"type": "boolean"},
                    "probe_timeout": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.env",
            "description": "Return Codex-facing per-process network environment for one target kind/runtime. Does not persist environment globally.",
            "annotations": {"title": "Network Gateway Env", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_kind": {"type": "string"},
                    "target": {"type": "string"},
                    "runtime": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.smoke",
            "description": "Run a bounded network gateway smoke against current proxy, proxy-chain wrapper, or isolated mihomo mode. Does not mutate system proxy, DNS, Clash config, or Codex conversation routing.",
            "annotations": {"title": "Network Gateway Smoke", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["current", "proxy-chain", "isolated"]},
                    "target_kind": {"type": "string"},
                    "target": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
                    "group": {"type": "string"},
                    "node": {"type": "string"},
                    "upstream_proxy": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.lease_start",
            "description": "Start a bounded localhost-only isolated mihomo proxy lease for one target kind and return per-process proxy env plus a cleanup command. Does not change system proxy, DNS, Clash config, main Clash node, or Codex conversation routing.",
            "annotations": {"title": "Network Gateway Lease Start", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_kind": {"type": "string", "description": "openai/github/package/docs/browser/paper/image/dataset/web/external. codex_chat/codex_model_api are rejected as protected production routes."},
                    "group": {"type": "string"},
                    "node": {"type": "string"},
                    "ttl_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
                    "check_url": {"type": "string", "description": "Optional URL to verify through the isolated lease before returning it."},
                    "check_method": {"type": "string", "enum": ["GET", "HEAD"]},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.lease_status",
            "description": "Inspect active isolated proxy leases and their expiry/process state. Does not reveal secrets.",
            "annotations": {"title": "Network Gateway Lease Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"lease_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.lease_stop",
            "description": "Stop one gateway-created isolated proxy lease and archive its metadata. Only stops the recorded mihomo process for that lease.",
            "annotations": {"title": "Network Gateway Lease Stop", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {"lease_id": {"type": "string"}},
                "required": ["lease_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "network_gateway.lease_cleanup",
            "description": "Stop expired or dead isolated proxy leases and archive their metadata. Does not touch production network state.",
            "annotations": {"title": "Network Gateway Lease Cleanup", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "network_gateway.validate",
            "description": "Validate the Codex-facing network gateway control plane and its lower-level network/component/Clash dependencies.",
            "annotations": {"title": "Network Gateway Validate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "network.snapshot",
            "description": "Read current proxy candidates and network route discovery state. Does not modify system settings.",
            "annotations": {"title": "Network Snapshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "network.recommend",
            "description": "Recommend a per-target network route for Codex/MCP/resource work without hard-binding all traffic to one proxy.",
            "annotations": {"title": "Network Recommend", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "network.env",
            "description": "Return per-process proxy environment suggestions for a target and runtime such as node, python, uv, curl, or browser.",
            "annotations": {"title": "Network Env", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "context": {"type": "string"},
                    "runtime": {"type": "string"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "network.plan",
            "description": "Return a practical per-target network work plan with probe, caller-env, and owner-boundary steps.",
            "annotations": {"title": "Network Plan", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "network.probe",
            "description": "Compare direct and detected-proxy timing for one network target.",
            "annotations": {"title": "Network Probe", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "context": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "network.probe_suite",
            "description": "Probe Codex-critical network targets such as OpenAI, ChatGPT, GitHub, npm, PyPI, and Microsoft Docs.",
            "annotations": {"title": "Network Probe Suite", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120}
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "network.validate",
            "description": "Validate network route discovery prerequisites.",
            "annotations": {"title": "Network Validate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def agent_bridge_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "agent_bridge.status",
            "description": "Read Reasonix/agent-bridge status through a fresh stdio call. This is read-only and does not claim the native MCP current turn is callable.",
            "annotations": {"title": "Agent Bridge Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]


def maintenance_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "hub.capabilities",
            "description": "Return the Hub-native and governed gateway capability map for choosing stable tool entry points.",
            "annotations": {"title": "Hub Capabilities", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "hub.validate",
            "description": "Validate the local HTTP MCP hub configuration and exposed tool set.",
            "annotations": {"title": "Hub Validate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "hub.metrics",
            "description": "Return local HTTP MCP hub request and tool-call metrics.",
            "annotations": {"title": "Hub Metrics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "mcp_session.validate",
            "description": "Run the read-only MCP session validator and summarize current tool-layer health.",
            "annotations": {"title": "MCP Session Validate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "mcp_session.metrics",
            "description": "Return read-only MCP session metrics.",
            "annotations": {"title": "MCP Session Metrics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "mcp_session.recover_plan",
            "description": "Return the bounded read-only recovery plan for one MCP profile after Transport closed, tool_unbound, timeout, or cancellation.",
            "annotations": {"title": "MCP Session Recover Plan", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["profile"],
                "additionalProperties": False,
            },
        },
    ]
