"""Parsers for mobile bridge control command text.

Owns: recognizing exact worker-level commands and scoped repair modes.
Non-goals: permission checks, command execution, queue mutation, or replies.
"""

from __future__ import annotations


def exact_control_command(text: str) -> str | None:
    normalized = " ".join((text or "").strip().split()).lower()
    if normalized in {"stop", "resume", "status", "repair"}:
        return normalized
    if normalized.startswith("hardstop"):
        return "hardstop"
    return None


def parse_repair_control_command(text: str) -> str | None:
    normalized = " ".join((text or "").strip().split()).lower()
    if not normalized:
        return None
    if normalized.startswith("/repair_bridge"):
        remainder = normalized.removeprefix("/repair_bridge").strip()
    elif normalized.startswith("repair bridge"):
        remainder = normalized.removeprefix("repair bridge").strip()
    else:
        return None
    if not remainder:
        return "safe"
    first = remainder.split()[0]
    aliases = {
        "status": "status",
        "summary": "status",
        "safe": "safe",
        "deep": "deep",
        "last": "last",
        "active": "active",
        "cdp": "cdp",
        "backlog": "backlog",
        "supplement": "supplement",
        "plugins": "plugins",
        "tools": "tools",
    }
    return aliases.get(first)
