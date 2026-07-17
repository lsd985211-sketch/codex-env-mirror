#!/usr/bin/env python3
"""Purpose-owned workflow route keyword extensions.

Ownership: workflow routing hints that are useful across the workspace but do
not belong in a business module.
Non-goals: no task execution, permissions, network probing, state mutation, or
replacement for the MCP capability matrix.
State behavior: read-only constants.
Caller context: imported by workflow_orchestrator.py when building domain
definitions.
"""

from __future__ import annotations


NETWORK_ROUTING_EXTRA_KEYWORDS: tuple[str, ...] = (
    "网络层",
    "网络策略",
    "网络配置",
    "网络适配",
    "网络兼容",
    "网络网关",
    "联网策略",
    "资源联网",
    "codex网关",
    "network_policy",
    "network_doctor",
    "network layer",
    "network policy",
    "network compatibility",
    "network routing",
    "network gateway",
    "route compatibility",
    "route health",
    "retry budget",
    "failover policy",
)
