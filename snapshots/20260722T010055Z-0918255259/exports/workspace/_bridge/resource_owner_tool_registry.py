#!/usr/bin/env python3
"""Shared resource owner-tool capability registry.

Ownership: define canonical owner-tool names accepted by structured resource
delegation and the subset executable by the local read-only owner executor.
Non-goals: route ordering, permission decisions, network policy, or executing
owner tools.
State behavior: immutable in-process constants only.
Caller context: codex_resource_delegation.py validates requested owner names;
resource_owner_executor.py exposes local read-only execution support.
"""

from __future__ import annotations


READ_ONLY_EXECUTABLE_OWNER_TOOLS = frozenset(
    {
        "chrome-devtools",
        "context7",
        "generic_search",
        "github",
        "markitdown",
        "microsoftdocs",
        "openai-docs",
        "package_manager",
        "playwright",
        "youtube-feed",
    }
)

ROUTING_OWNER_TOOLS = frozenset({"resource_cli", "resource_router"})

SUPPORTED_DELEGATION_OWNER_TOOLS = frozenset(
    READ_ONLY_EXECUTABLE_OWNER_TOOLS | ROUTING_OWNER_TOOLS
)


def validate() -> dict[str, object]:
    return {
        "schema": "resource_owner_tool_registry.validate.v1",
        "ok": bool(
            READ_ONLY_EXECUTABLE_OWNER_TOOLS <= SUPPORTED_DELEGATION_OWNER_TOOLS
            and ROUTING_OWNER_TOOLS <= SUPPORTED_DELEGATION_OWNER_TOOLS
            and "youtube-feed" in READ_ONLY_EXECUTABLE_OWNER_TOOLS
            and "generic_search" in SUPPORTED_DELEGATION_OWNER_TOOLS
            and "openai-docs" in READ_ONLY_EXECUTABLE_OWNER_TOOLS
        ),
        "read_only_executable": sorted(READ_ONLY_EXECUTABLE_OWNER_TOOLS),
        "routing_only": sorted(ROUTING_OWNER_TOOLS),
        "delegation_supported": sorted(SUPPORTED_DELEGATION_OWNER_TOOLS),
    }
