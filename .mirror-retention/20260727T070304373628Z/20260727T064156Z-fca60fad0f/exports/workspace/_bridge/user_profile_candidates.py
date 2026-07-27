#!/usr/bin/env python3
"""User-profile candidate signal helpers.

Ownership: memory governance candidate intake for user-profile facts.
Non-goals: writing user_profile.json, approving facts, scanning transcripts, or
copying AGENTS/workspace rules into profile memory.
State behavior: pure/read-only transformation of caller-provided current-turn
signals into bounded candidate inputs.
Caller context: closeout and memory_governance pass explicit profile signals
after a task reveals a stable user-specific preference, context, goal, or
tradeoff that may be useful in future decisions.
"""

from __future__ import annotations

import re
from typing import Any


MAX_SIGNAL_CHARS = 600
MIN_SIGNAL_CHARS = 18

TEMPORARY_CONTEXT_PATTERNS = (
    r"\bthis turn\b",
    r"\bcurrent task\b",
    r"\btemporary\b",
    r"本轮",
    r"当前任务",
    r"这次任务",
    r"临时",
    r"一次性",
)

STABLE_PROFILE_PATTERNS = (
    r"\bI prefer\b",
    r"\bmy preference\b",
    r"\bfor future\b",
    r"\blong[- ]term\b",
    r"我希望",
    r"我偏好",
    r"我更倾向",
    r"对我来说",
    r"以后",
    r"长期",
    r"优先",
    r"取舍",
    r"画像",
    r"偏好",
)


def normalize_profile_signal(text: str) -> str:
    """Return a compact, bounded signal string suitable for candidate review."""
    normalized = " ".join(str(text or "").split())
    if len(normalized) > MAX_SIGNAL_CHARS:
        normalized = normalized[:MAX_SIGNAL_CHARS].rstrip()
    return normalized


def looks_temporary_signal(text: str) -> bool:
    """Detect signals that look like one-off task state instead of profile facts."""
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in TEMPORARY_CONTEXT_PATTERNS)


def looks_profile_worthy_signal(text: str) -> bool:
    """Detect whether a caller-provided signal has stable profile shape."""
    if len(text) < MIN_SIGNAL_CHARS:
        return False
    if looks_temporary_signal(text):
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in STABLE_PROFILE_PATTERNS)


def profile_signal_sources(signals: list[str] | None, *, source_prefix: str = "closeout_profile_signal") -> list[dict[str, Any]]:
    """Normalize explicit current-turn profile signals without writing memory."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, signal in enumerate(signals or [], start=1):
        text = normalize_profile_signal(signal)
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(
            {
                "text": text,
                "source": f"{source_prefix}:{index}",
                "profile_worthy": looks_profile_worthy_signal(text),
                "skip_reason": "" if looks_profile_worthy_signal(text) else "not_stable_profile_shape",
            }
        )
    return results


def profile_candidate_review_item(candidate: dict[str, Any]) -> dict[str, Any]:
    """Convert a profile candidate into the shared closeout review-card shape."""
    fact = candidate.get("proposed_fact") if isinstance(candidate.get("proposed_fact"), dict) else {}
    value = str(fact.get("value") or "")
    old_action = str(candidate.get("old_profile_action") or "new")
    related_id = str(candidate.get("related_existing_fact_id") or "")
    return {
        "source_item_id": str(candidate.get("id") or fact.get("id") or ""),
        "title": f"User profile candidate: {fact.get('category') or 'preference'}",
        "summary": value,
        "source_url": str(candidate.get("source") or ""),
        "trust_tier": "inferred_from_current_turn",
        "freshness_class": "current_turn_candidate",
        "proposed_destination_namespace": "user_profile",
        "approval_action": "approve_modify_or_reject_profile_fact",
        "required_checks": [
            "confirm this is a stable user-specific preference/context/goal/tradeoff",
            "confirm it is not already owned by AGENTS, workspace rules, or a skill",
            f"profile_action={old_action}" + (f"; related_existing_fact_id={related_id}" if related_id else ""),
        ],
    }
