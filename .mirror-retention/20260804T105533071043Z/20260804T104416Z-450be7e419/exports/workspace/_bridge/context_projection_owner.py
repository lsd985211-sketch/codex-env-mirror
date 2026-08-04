#!/usr/bin/env python3
"""Choose a bounded context projection without owning source facts.

Ownership: deterministic projection mode and L0/L1 field selection.
Non-goals: source execution, artifact persistence, permission decisions,
Headroom execution, durable memory, or owner-specific fact interpretation.
State behavior: pure/read-only; policy is loaded from the declarative contract.
Caller context: shadow evaluation first, then explicitly allowlisted facades.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bounded_output import bounded_payload, json_size_bytes


CONTRACT_PATH = Path(__file__).resolve().parent / "policies" / "context_projection_contracts.json"
MODES = {"direct", "project", "reference", "reversible_compress", "block_for_reference"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_contracts(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if str(item).strip()))


def contract_for(source_kind: str, *, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = dict(policy or load_contracts())
    contracts = selected.get("contracts") if isinstance(selected.get("contracts"), dict) else {}
    default = contracts.get("default") if isinstance(contracts.get("default"), dict) else {}
    specific = contracts.get(str(source_kind or "default"))
    specific = specific if isinstance(specific, dict) else {}
    return {
        "required_fields": list(dict.fromkeys((*_strings(default.get("required_fields")), *_strings(specific.get("required_fields"))))),
        "preserve_fields": list(dict.fromkeys((*_strings(default.get("preserve_fields")), *_strings(specific.get("preserve_fields"))))),
        "selectors": list(dict.fromkeys((*_strings(default.get("selectors")), *_strings(specific.get("selectors"))))),
    }


def _existing_fields(payload: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(field for field in fields if field in payload)


def decide_projection(
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
    required_field_names: Sequence[str] = (),
    preserve_field_names: Sequence[str] = (),
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic projection decision and suggested view."""

    source = dict(payload)
    policy_payload = dict(policy or load_contracts())
    contract = contract_for(source_kind, policy=policy_payload)
    required_fields = _existing_fields(
        source,
        tuple(dict.fromkeys((*contract["required_fields"], *_strings(required_field_names)))),
    )
    preserve_fields = _existing_fields(
        source,
        tuple(dict.fromkeys((*contract["preserve_fields"], *_strings(preserve_field_names)))),
    )
    budget = max(256, int(inline_budget or policy_payload.get("default_inline_bytes") or 8192))
    input_bytes = json_size_bytes(source)
    existing_projection = bool(already_projected or isinstance(source.get("output_budget"), dict))
    existing_compression = (source.get("output_budget") or {}).get("functional_compression") if isinstance(source.get("output_budget"), dict) else {}
    existing_integrity = str((existing_compression or {}).get("functional_integrity") or "")
    decision_signature = _digest({
        "source_signature": str(source_signature),
        "policy_version": str(policy_payload.get("policy_version") or ""),
        "consumer_purpose": str(consumer_purpose),
        "required_fields": required_fields,
        "preserve_fields": preserve_fields,
        "source_kind": str(source_kind),
        "inline_budget": budget,
        "artifact_ref": str(artifact_ref),
        "reversible_compression_available": bool(reversible_compression_available),
        "estimated_compression_ratio": float(estimated_compression_ratio),
        "already_projected": existing_projection,
    })

    if existing_projection and existing_integrity in {"blocked_no_reference", "reference_required"}:
        mode = "reference" if existing_integrity == "reference_required" and artifact_ref else "block_for_reference"
        return {
            "schema": "context_projection_owner.decision.v1",
            "ok": mode != "block_for_reference",
            "mode": mode,
            "decision_signature": decision_signature,
            "source_signature": str(source_signature),
            "source_kind": str(source_kind),
            "consumer_purpose": str(consumer_purpose),
            "required_fields": list(required_fields),
            "preserve_fields": list(preserve_fields),
            "selectors": contract["selectors"],
            "functional_integrity": existing_integrity,
            "functional_recall": 0.0,
            "input_bytes": input_bytes,
            "projected_bytes": input_bytes,
            "artifact_ref": str(artifact_ref),
            "already_projected": True,
            "projection": source,
        }

    if existing_projection or input_bytes <= budget:
        return {
            "schema": "context_projection_owner.decision.v1",
            "ok": True,
            "mode": "direct",
            "decision_signature": decision_signature,
            "source_signature": str(source_signature),
            "source_kind": str(source_kind),
            "consumer_purpose": str(consumer_purpose),
            "required_fields": list(required_fields),
            "preserve_fields": list(preserve_fields),
            "selectors": contract["selectors"],
            "functional_integrity": "preserved",
            "functional_recall": 1.0,
            "input_bytes": input_bytes,
            "projected_bytes": input_bytes,
            "artifact_ref": str(artifact_ref),
            "already_projected": existing_projection,
            "projection": source,
        }

    projection = bounded_payload(
        source,
        max_bytes=budget,
        preserve_keys=preserve_fields,
        required_keys=required_fields,
        artifact_ref=str(artifact_ref),
    )
    compression = (projection.get("output_budget") or {}).get("functional_compression") or {}
    integrity = str(compression.get("functional_integrity") or "blocked_no_reference")
    required_present = len(required_fields)
    preserved = len([field for field in required_fields if field in compression.get("preserved_inline", [])])
    functional_recall = 1.0 if required_present == 0 else preserved / required_present
    minimum_bytes = int(policy_payload.get("reversible_compression_min_bytes") or 16384)
    minimum_savings = float(policy_payload.get("reversible_compression_min_savings_ratio") or 0.25)
    estimated_savings = max(0.0, min(1.0, 1.0 - float(estimated_compression_ratio)))

    if integrity == "blocked_no_reference":
        mode = "block_for_reference"
    elif integrity == "reference_required":
        mode = "reference"
    elif (
        reversible_compression_available
        and bool(artifact_ref)
        and input_bytes >= minimum_bytes
        and estimated_savings >= minimum_savings
    ):
        mode = "reversible_compress"
    else:
        mode = "project"

    return {
        "schema": "context_projection_owner.decision.v1",
        "ok": mode != "block_for_reference",
        "mode": mode,
        "decision_signature": decision_signature,
        "source_signature": str(source_signature),
        "source_kind": str(source_kind),
        "consumer_purpose": str(consumer_purpose),
        "required_fields": list(required_fields),
        "preserve_fields": list(preserve_fields),
        "selectors": contract["selectors"],
        "functional_integrity": integrity,
        "functional_recall": functional_recall,
        "input_bytes": input_bytes,
        "projected_bytes": json_size_bytes(projection),
        "artifact_ref": str(artifact_ref),
        "already_projected": False,
        "estimated_compression_savings_ratio": estimated_savings,
        "headroom_execution_allowed": mode == "reversible_compress",
        "projection": projection,
    }
