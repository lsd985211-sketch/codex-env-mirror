#!/usr/bin/env python3
"""Current-turn MCP observation evidence for resource process diagnostics.

Owns read-only parsing, freshness filtering, and process-group mapping for
current-turn MCP observation logs. Non-goals: process snapshot collection,
process cleanup, repair planning, permission decisions, or route fallback
execution. Normal callers are resource_process_doctor.py and future
resource/MCP diagnostics that need the same current-turn evidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES = 30
CURRENT_TURN_NEGATIVE_STATUSES = {"transport_closed", "tool_unbound", "schema_mismatch", "tool_surface_unstable"}
CURRENT_TURN_POSITIVE_STATUSES = {
    "current_turn_callable",
    "tool_available",
    "session_tool_available",
    "tool_call_succeeded",
    "call_succeeded",
    "mcp_session_available",
}
CURRENT_TURN_SOURCE_MARKERS = ("current-codex-turn", "active-codex-turn", "this-codex-turn", "current-turn")
CURRENT_SESSION_SOURCE_MARKERS = (
    *CURRENT_TURN_SOURCE_MARKERS,
    "current-codex-session",
    "active-codex-session",
    "this-codex-session",
    "current-session",
)
PROFILE_TO_PROCESS_GROUP = {
    "codegraph": "codegraph_mcp",
    "filesystem": "filesystem_mcp",
    "filesystem-admin": "filesystem_admin_mcp",
    "custom-slash-commands": "custom_slash_commands_mcp",
    "sqlite-scratch": "sqlite_scratch_mcp",
    "sqlite-bridge-ro": "sqlite_bridge_ro_mcp",
    "context7": "context7_mcp",
    "microsoftdocs": "microsoftdocs_mcp",
    "openai-docs": "openai_docs_mcp",
    "myskills": "myskills-mcp",
    "playwright": "playwright",
    "chrome-devtools": "chrome-devtools",
    "next-ai-drawio": "next_ai_drawio_mcp",
    "desktop-weixin": "desktop_weixin_mcp",
    "markitdown": "markitdown-mcp",
    "local-pmb-memory": "local_pmb_proxy",
    "mobile-openclaw-bridge": "mobile_bridge_mcp_server",
    "agent-bridge": "bridge_server_v2",
}


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_current_turn_source(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in CURRENT_TURN_SOURCE_MARKERS)


def is_current_session_source(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in CURRENT_SESSION_SOURCE_MARKERS)


def current_turn_observation_cutoff(max_age_minutes: int, anchor_at: datetime | None) -> datetime:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    if anchor_at is not None:
        if anchor_at.tzinfo is None:
            anchor_at = anchor_at.replace(tzinfo=timezone.utc)
        cutoff = min(anchor_at.astimezone(timezone.utc), cutoff)
    return cutoff


def observation_allowed_for_current_turn(item: dict[str, Any], status: str) -> bool:
    if status in CURRENT_TURN_POSITIVE_STATUSES:
        return is_current_turn_source(item.get("source"))
    return is_current_session_source(item.get("source"))


def parse_observation_log_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        item = json.loads(text)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None


def current_turn_observation_key(
    item: dict[str, Any],
    *,
    cutoff: datetime,
) -> tuple[str, datetime] | None:
    recorded_at = parse_iso_datetime(item.get("recorded_at"))
    if not recorded_at or recorded_at < cutoff:
        return None
    profile = str(item.get("profile") or "").strip()
    status = str(item.get("status") or "").strip()
    if not profile or status not in CURRENT_TURN_NEGATIVE_STATUSES | CURRENT_TURN_POSITIVE_STATUSES:
        return None
    if not observation_allowed_for_current_turn(item, status):
        return None
    return profile, recorded_at


def should_replace_observation(previous: dict[str, Any] | None, recorded_at: datetime) -> bool:
    previous_at = parse_iso_datetime(previous.get("recorded_at")) if isinstance(previous, dict) else None
    return previous_at is None or recorded_at >= previous_at


def latest_current_turn_observations_by_profile(
    *,
    log_path: Path,
    cutoff: datetime,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = parse_observation_log_line(line)
            if not item:
                continue
            observation_key = current_turn_observation_key(item, cutoff=cutoff)
            if observation_key is None:
                continue
            profile, recorded_at = observation_key
            previous = latest.get(profile)
            if should_replace_observation(previous, recorded_at):
                latest[profile] = item
    return latest


def current_turn_observation_groups(
    latest: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    negative_by_group: dict[str, dict[str, Any]] = {}
    positive_by_group: dict[str, dict[str, Any]] = {}
    for profile, item in latest.items():
        status = str(item.get("status") or "")
        group = PROFILE_TO_PROCESS_GROUP.get(profile)
        if group and status in CURRENT_TURN_POSITIVE_STATUSES:
            positive_by_group[group] = item
        if group and status in CURRENT_TURN_NEGATIVE_STATUSES:
            negative_by_group[group] = item
    return negative_by_group, positive_by_group


def current_turn_tool_observations(
    *,
    log_path: Path,
    max_age_minutes: int = CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES,
    anchor_at: datetime | None = None,
) -> dict[str, Any]:
    if not log_path.exists():
        return {"ok": True, "state": "missing", "negative_by_group": {}, "latest_by_profile": {}}
    try:
        cutoff = current_turn_observation_cutoff(max_age_minutes, anchor_at)
        latest = latest_current_turn_observations_by_profile(log_path=log_path, cutoff=cutoff)
    except Exception as exc:
        return {"ok": False, "state": "error", "error": str(exc), "negative_by_group": {}, "latest_by_profile": {}}

    negative_by_group, positive_by_group = current_turn_observation_groups(latest)
    return {
        "ok": True,
        "state": "ok",
        "negative_by_group": negative_by_group,
        "positive_by_group": positive_by_group,
        "latest_by_profile": latest,
    }
