#!/usr/bin/env python3
"""Deterministic business acceptance contract for resource acquisition.

This module owns the distinction between transport/tool success and a resource
result that actually satisfies the caller.  It is pure: no network, file, queue,
or broker state is read or written.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from structured_task_envelope import resource_contract_from_metadata


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else ([value] if value else [])
    return list(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_acceptance_contract(request: dict[str, Any]) -> dict[str, Any]:
    """Compile one canonical, stable acceptance contract from a request."""

    metadata = _dict(request.get("metadata"))
    envelope = resource_contract_from_metadata(metadata)
    resource = _dict(envelope.get("resource"))
    execution = _dict(resource.get("execution"))
    source_policy = _dict(resource.get("source_policy"))
    freshness = _dict(resource.get("freshness"))
    structured_acceptance = _dict(execution.get("acceptance"))
    batch = _dict(metadata.get("batch_item_contract"))
    batch_acceptance = _dict(batch.get("acceptance"))
    custom = _dict(metadata.get("custom_delegation"))
    constraints = _dict(custom.get("constraints"))

    requested = _items(execution.get("deliverables"))
    required = _items(structured_acceptance.get("required_deliverables")) or requested
    explicit = bool(required)
    need_materialization = bool(request.get("need_materialization"))
    consumable_required = bool(batch_acceptance.get("consumable_required", True))
    if not required:
        required = ["artifact"] if need_materialization else (["consumable"] if consumable_required else ["metadata"])

    minimum = max(
        1,
        _positive_int(batch_acceptance.get("minimum_candidates"), 0),
        _positive_int(batch_acceptance.get("minimum_quantity"), 0),
        _positive_int(structured_acceptance.get("minimum_quantity"), 0),
    )
    payload = {
        "schema": "resource_acceptance_contract.v1",
        "resource_kind": str(resource.get("kind") or metadata.get("resource_kind_hint") or metadata.get("resource_kind") or "").strip().lower(),
        "required_deliverables": required,
        "requested_deliverables": requested,
        "minimum_quantity": minimum,
        "allow_partial": bool(structured_acceptance.get("allow_partial") or batch_acceptance.get("allow_partial")),
        "consumable_required": consumable_required,
        "authority": str(source_policy.get("authority") or constraints.get("authority") or metadata.get("authority") or "").strip().lower(),
        "domains": _items(source_policy.get("domains") or constraints.get("site_or_domain")),
        "language": str(resource.get("language") or constraints.get("language") or metadata.get("language") or "").strip().lower(),
        "region": str(resource.get("region") or constraints.get("region") or metadata.get("region") or "").strip().lower(),
        "freshness": str(freshness.get("mode") or constraints.get("freshness") or metadata.get("freshness") or "").strip().lower(),
        "format": _items(constraints.get("allowed_extensions") or metadata.get("allowed_extensions")),
        "license": str(constraints.get("license_policy") or metadata.get("license_policy") or "").strip().lower(),
        "materialization_required": need_materialization,
        "explicit": explicit,
    }
    signature_payload = {key: value for key, value in payload.items() if key not in {"schema", "signature"}}
    payload["signature"] = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _delivered(result: dict[str, Any]) -> tuple[set[str], int]:
    metadata = _dict(result.get("metadata"))
    delivered = set(_items(metadata.get("completed_deliverables")))
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    items = metadata.get("items") if isinstance(metadata.get("items"), list) else []
    candidate_count = max(len([item for item in candidates if isinstance(item, dict)]), len([item for item in items if isinstance(item, dict)]))
    if candidate_count:
        delivered.update({"candidates", "metadata", "consumable"})
    artifact = str(result.get("stored_path") or result.get("local_path") or result.get("artifact_path") or "").strip()
    if artifact:
        delivered.update({"artifact", "content", "consumable"})
    content = "".join(
        str(value or "").strip()
        for value in (
            result.get("content"), result.get("text"), result.get("markdown"), result.get("body"),
            result.get("preview_text"), metadata.get("preview_text"),
        )
    )
    declared_kind = str(result.get("result_kind") or "").strip().lower()
    metadata_only = declared_kind in {"metadata", "classification", "classified_by_policy"} or str(result.get("reason") or "") == "classified_by_policy"
    if content and not metadata_only:
        delivered.update({"content", "consumable"})
    if metadata or declared_kind:
        delivered.add("metadata")
    return delivered, max(candidate_count, 1 if artifact or content else 0)


def evaluate_acceptance(contract: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    delivered, quantity = _delivered(result)
    required = _items(contract.get("required_deliverables"))
    missing = [item for item in required if item not in delivered]
    minimum = max(1, _positive_int(contract.get("minimum_quantity"), 1))
    quantity_ok = quantity >= minimum
    transport_ok = bool(result.get("ok"))
    full = transport_ok and not missing and quantity_ok
    partial = bool(contract.get("allow_partial")) and transport_ok and bool(delivered) and quantity > 0
    accepted = full or partial
    if not transport_ok:
        reason = str(result.get("reason") or result.get("error_class") or "result_not_ok")
    elif missing:
        reason = "required_deliverables_not_met"
    elif not quantity_ok:
        reason = "minimum_quantity_not_met"
    else:
        reason = "acceptance_contract_satisfied" if full else "partial_acceptance_allowed"
    return {
        "schema": "resource_acceptance_decision.v1",
        "accepted": accepted,
        "full": full,
        "partial": partial and not full,
        "reason": reason,
        "signature": str(contract.get("signature") or ""),
        "required_deliverables": required,
        "delivered": sorted(delivered),
        "missing_deliverables": missing,
        "minimum_quantity": minimum,
        "actual_quantity": quantity,
        "next_action": "consume_resource" if accepted else "try_next_route",
    }
