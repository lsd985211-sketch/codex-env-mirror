#!/usr/bin/env python3
"""Pure environment inputs for the scoped-authorization authority.

This module owns no policy, permission, or state.  It only canonicalizes stable
references supplied by existing owners and compares two snapshots.  The
``scoped_authorization`` facade remains the single PDP and persistence owner.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


SOURCE_EFFECTS = {"neutral", "tighten", "incompatible", "expand", "unavailable"}
REQUIRED_SOURCES = (
    "workflow",
    "owner_capability",
    "system_membership",
    "rule_governance",
    "maintenance_capability",
    "state_write_authority",
)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_environment_snapshot(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return a bounded canonical projection of owner-provided environment facts."""

    raw = value if isinstance(value, dict) else {}
    sources: dict[str, dict[str, Any]] = {}
    for name, item in sorted((raw.get("sources") or {}).items()):
        if not isinstance(item, dict):
            continue
        effect = str(item.get("authorization_effect") or "neutral").strip()
        if effect not in SOURCE_EFFECTS:
            effect = "unavailable"
        sources[str(name)] = {
            "signature": str(item.get("signature") or "").strip(),
            "status": str(item.get("status") or "ok").strip(),
            "authorization_effect": effect,
            "authority_ref": str(item.get("authority_ref") or "").strip(),
        }
    required = sorted({str(x) for x in (raw.get("required_sources") or []) if str(x)})
    return {
        "schema": "authorization_environment_snapshot.v1",
        "ok": bool(raw.get("ok", True)),
        "workflow_semantic_hash": str(raw.get("workflow_semantic_hash") or "").strip(),
        "authorization_semantic_signature": str(raw.get("authorization_semantic_signature") or "").strip(),
        "required_sources": required,
        "sources": sources,
    }


def environment_signature(value: dict[str, Any] | None) -> str:
    return _digest(normalize_environment_snapshot(value))


def classify_environment_change(
    previous: dict[str, Any] | None, current: dict[str, Any] | None
) -> dict[str, Any]:
    """Classify drift without ever treating unknown change as an implicit grant."""

    before = normalize_environment_snapshot(previous)
    after = normalize_environment_snapshot(current)
    before_sig = environment_signature(before)
    after_sig = environment_signature(after)
    required = sorted(set(before["required_sources"]) | set(after["required_sources"]))
    missing = [name for name in required if not after["sources"].get(name, {}).get("signature")]
    changed = sorted(
        name for name in set(before["sources"]) | set(after["sources"])
        if before["sources"].get(name) != after["sources"].get(name)
    )
    effects = {
        str(after["sources"].get(name, {}).get("authorization_effect") or "unavailable")
        for name in changed
    }
    statuses = {str(item.get("status") or "") for item in after["sources"].values()}
    if before_sig == after_sig:
        classification = "equivalent"
    elif not after["ok"] or missing or "unavailable" in effects or "unavailable" in statuses:
        classification = "unavailable"
    elif "incompatible" in effects or "incompatible" in statuses:
        classification = "incompatible"
    elif "expand" in effects:
        classification = "expansion_required"
    elif "tighten" in effects:
        classification = "tightened"
    elif (
        before["authorization_semantic_signature"]
        and before["authorization_semantic_signature"] == after["authorization_semantic_signature"]
        and before["workflow_semantic_hash"] == after["workflow_semantic_hash"]
    ):
        classification = "equivalent"
    else:
        # Unclassified semantic drift is never an authorization grant.
        classification = "expansion_required"
    return {
        "schema": "authorization_environment_change.v1",
        "ok": classification == "equivalent",
        "classification": classification,
        "previous_signature": before_sig,
        "current_signature": after_sig,
        "changed_sources": changed,
        "missing_required_sources": missing,
        "automatic_action": {
            "equivalent": "reuse",
            "tightened": "fence",
            "incompatible": "fence",
            "unavailable": "pause",
            "expansion_required": "reconcile",
        }[classification],
        "authorization_reconciliation_required": classification == "expansion_required",
    }
