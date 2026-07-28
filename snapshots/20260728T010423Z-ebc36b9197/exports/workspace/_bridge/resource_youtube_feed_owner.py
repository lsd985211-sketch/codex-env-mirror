#!/usr/bin/env python3
"""Read-only resource owner adapter for the global youtube-feed skill.

Ownership: execute the existing youtube-feed discovery script and normalize its
JSON output into resource candidates.
Non-goals: transcript download, media materialization, channel-list mutation,
credential handling, or generic YouTube search.
State behavior: read-only subprocess and network access; no persistent writes.
Caller context: resource_owner_executor.py invokes this adapter only when a
resource request explicitly prefers the `youtube-feed` owner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from resource_network_execution import execution_package_from_gateway_plan


def _metadata(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("metadata")
    return value if isinstance(value, dict) else {}


def _constraints(request: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(request)
    custom = metadata.get("custom_delegation")
    if isinstance(custom, dict) and isinstance(custom.get("constraints"), dict):
        value = custom["constraints"]
    else:
        value = metadata.get("constraints")
    if not isinstance(value, dict):
        return {}
    nested = value.get("constraints")
    return {**value, **nested} if isinstance(nested, dict) else value


def request_days(request: dict[str, Any]) -> int:
    metadata = _metadata(request)
    constraints = _constraints(request)
    raw = constraints.get("days", metadata.get("days", 2))
    try:
        return max(1, min(int(raw), 30))
    except (TypeError, ValueError):
        return 2


def request_views(request: dict[str, Any]) -> bool:
    metadata = _metadata(request)
    constraints = _constraints(request)
    return bool(constraints.get("include_views", metadata.get("include_views", False)))


def script_path() -> Path:
    return Path.home() / ".codex" / "skills" / "youtube-feed" / "scripts" / "get_updates.py"


def normalize_videos(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        video_id = str(item.get("video_id") or "").strip()
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url:
            continue
        candidates.append(
            {
                "title": str(item.get("title") or url),
                "url": url,
                "landing_url": url,
                "summary": str(item.get("summary") or item.get("description") or "")[:1000],
                "source": "youtube-feed",
                "source_id": video_id or url,
                "resource_kind": "video",
                "channel": str(item.get("channel") or ""),
                "published": str(item.get("published") or ""),
                "views": item.get("views"),
                "duration": item.get("duration"),
            }
        )
    return candidates


def execute_youtube_feed(
    request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int = 30
) -> dict[str, Any]:
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return {
            "ok": False,
            "status": "handoff_required",
            "reason": "network_package_unavailable",
            "next_action": "refresh_network_route_and_retry",
        }

    script = script_path()
    if not script.is_file():
        return {
            "ok": False,
            "status": "failed",
            "reason": "youtube_feed_script_missing",
            "next_action": "repair_or_disable_youtube_feed_skill",
        }

    command = [sys.executable, str(script), "--days", str(request_days(request)), "--json"]
    if request_views(request):
        command.append("--views")

    env = os.environ.copy()
    for key in package.get("unset_env") or []:
        env.pop(str(key), None)
    for key, value in (package.get("env") or {}).items():
        env[str(key)] = str(value)

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, min(int(timeout), 120)),
            env=env,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "status": "failed",
            "error_class": "timeout",
            "reason": "youtube_feed_timeout",
            "next_action": "retry_with_larger_budget_or_narrower_channel_set",
        }

    if completed.returncode != 0:
        return {
            "ok": False,
            "status": "failed",
            "error_class": "owner_process_failed",
            "reason": (completed.stderr or completed.stdout or "youtube_feed_failed")[-2000:],
            "next_action": "inspect_youtube_feed_owner_error",
        }

    try:
        raw_items = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "failed",
            "error_class": "invalid_owner_output",
            "reason": "youtube_feed_output_not_json",
            "next_action": "repair_youtube_feed_output_contract",
        }

    candidates = normalize_videos(raw_items)
    content = json.dumps(
        {
            "days": request_days(request),
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
        ensure_ascii=False,
    )
    return {
        "ok": True,
        "status": "completed",
        "source": "youtube-feed",
        "result_kind": "youtube_feed_candidates",
        "content": content,
        "candidates": candidates,
        "metadata": {
            "days": request_days(request),
            "result_count": len(candidates),
            "items": candidates,
            "owner_execution_route": "local_skill_owner_adapter",
            "route_mode": package.get("route_mode", ""),
        },
        "next_action": "consume_resource",
    }


def validate() -> dict[str, Any]:
    sample = normalize_videos(
        [
            {
                "channel": "Example",
                "title": "Example video",
                "video_id": "abc123",
                "published": "2026-07-12 10:00",
                "summary": "Example summary",
            }
        ]
    )
    return {
        "schema": "resource_youtube_feed_owner.validate.v1",
        "ok": bool(
            request_days({"metadata": {"constraints": {"days": 99}}}) == 30
            and len(sample) == 1
            and sample[0]["url"].endswith("abc123")
        ),
        "script": str(script_path()),
        "script_exists": script_path().is_file(),
        "sample_count": len(sample),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
