#!/usr/bin/env python3
"""Evaluate context projections without changing caller-visible output.

Ownership: read-only comparison metrics and signature-deduplicated aggregates.
Non-goals: replacing output, persisting source content, invoking Headroom,
scheduling, permission decisions, or activating projection policies.
State behavior: pure/read-only. Caller context: pre-activation shadow tests.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import context_projection_owner


def evaluate_shadow(
    payload: Mapping[str, Any],
    *,
    source_kind: str,
    source_signature: str,
    consumer_purpose: str,
    inline_budget: int | None = None,
    artifact_ref: str = "",
    reversible_compression_available: bool = False,
    estimated_compression_ratio: float = 1.0,
    already_projected: bool = False,
) -> dict[str, Any]:
    decision = context_projection_owner.decide_projection(
        payload,
        source_kind=source_kind,
        source_signature=source_signature,
        consumer_purpose=consumer_purpose,
        inline_budget=inline_budget,
        artifact_ref=artifact_ref,
        reversible_compression_available=reversible_compression_available,
        estimated_compression_ratio=estimated_compression_ratio,
        already_projected=already_projected,
    )
    saved = max(0, int(decision["input_bytes"]) - int(decision["projected_bytes"]))
    return {
        "schema": "context_projection_shadow.observation.v1",
        "ok": True,
        "actual_output_unchanged": True,
        "decision_signature": decision["decision_signature"],
        "source_signature": str(source_signature),
        "source_kind": str(source_kind),
        "consumer_purpose": str(consumer_purpose),
        "projection_mode": decision["mode"],
        "functional_integrity": decision["functional_integrity"],
        "functional_recall": decision["functional_recall"],
        "input_bytes": decision["input_bytes"],
        "projected_bytes": decision["projected_bytes"],
        "estimated_bytes_saved": saved,
        "estimated_savings_ratio": saved / max(1, int(decision["input_bytes"])),
        "artifact_ref": decision["artifact_ref"],
        "already_projected": decision["already_projected"],
        "headroom_recommended": decision["mode"] == "reversible_compress",
        "activation_allowed": False,
    }


def aggregate_shadow(observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    total = 0
    for item in observations:
        total += 1
        signature = str(item.get("decision_signature") or "")
        if signature and signature not in unique:
            unique[signature] = dict(item)
    rows = list(unique.values())
    input_bytes = sum(int(item.get("input_bytes") or 0) for item in rows)
    projected_bytes = sum(int(item.get("projected_bytes") or 0) for item in rows)
    recalls = [float(item.get("functional_recall") or 0.0) for item in rows]
    return {
        "schema": "context_projection_shadow.aggregate.v1",
        "ok": all(item.get("functional_integrity") != "blocked_no_reference" for item in rows),
        "observation_count": len(rows),
        "duplicate_observation_count": max(0, total - len(rows)),
        "input_bytes": input_bytes,
        "projected_bytes": projected_bytes,
        "estimated_bytes_saved": max(0, input_bytes - projected_bytes),
        "minimum_functional_recall": min(recalls, default=1.0),
        "headroom_recommended_count": sum(bool(item.get("headroom_recommended")) for item in rows),
        "activation_allowed": False,
        "rule": "shadow metrics never replace caller output or activate projection",
    }
