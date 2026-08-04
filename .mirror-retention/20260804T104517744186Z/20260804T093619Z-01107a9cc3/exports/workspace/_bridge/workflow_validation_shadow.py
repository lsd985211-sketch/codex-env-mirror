#!/usr/bin/env python3
"""Read-only shadow of workflow validation plan construction.

Ownership: normalize validation scenario identities and observe repeated plan
construction inputs. Non-goals: build, cache, select, skip, reorder, execute, or
judge plans and checks. State behavior: process-local and read-only. Caller
context: workflow_orchestrator records already-completed validator plan calls
and attaches the shadow only after the authoritative result is complete.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, set):
            return sorted(item, key=repr)
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"validation_shadow_value_not_serializable:{type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    )


def signature_for_value(value: Any) -> str:
    try:
        canonical = _canonical(value)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def signature_for_files(paths: list[Path]) -> str:
    rows: list[dict[str, str]] = []
    for path in sorted((Path(item).resolve() for item in paths), key=str):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""
        rows.append({"path": path.name, "sha256": digest})
    return signature_for_value(rows) if rows else ""


def _message(value: str) -> str:
    return " ".join(str(value or "").split())


class ValidationPlanShadow:
    """Observe completed plan calls without becoming a validation authority."""

    def __init__(self, skill_context_signature: str, workflow_source_signature: str) -> None:
        self._skill_context_signature = str(skill_context_signature or "").strip()
        self._workflow_source_signature = str(workflow_source_signature or "").strip()
        self._manifest: list[dict[str, Any]] = []
        self._scenario_ids: set[str] = set()

    def record_plan(
        self,
        plan: dict[str, Any],
        *,
        message: str,
        detail: str,
        scenario_id: str,
        consumer: str,
        source_owner: str = "workflow_validation",
    ) -> dict[str, Any]:
        scenario = str(scenario_id or "").strip()
        owner = str(consumer or "").strip()
        authority = str(source_owner or "").strip()
        normalized_message = _message(message)
        normalized_detail = str(detail or "full").strip().lower()
        if not scenario or not owner or not authority or not normalized_message or not normalized_detail:
            raise ValueError("validation_shadow_scenario_incomplete")
        if scenario in self._scenario_ids:
            raise ValueError("validation_shadow_scenario_id_duplicate")
        self._scenario_ids.add(scenario)
        complete = bool(self._skill_context_signature and self._workflow_source_signature)
        identity = signature_for_value({
            "message": normalized_message,
            "detail": normalized_detail,
            "skill_context_signature": self._skill_context_signature,
            "workflow_source_signature": self._workflow_source_signature,
        }) if complete else ""
        self._manifest.append({
            "scenario_id": scenario,
            "source_owner": authority,
            "consumer": owner,
            "detail": normalized_detail,
            "plan_identity": identity,
            "identity_complete": complete,
        })
        return plan

    def attach(self, payload: dict[str, Any]) -> dict[str, Any]:
        identities = [str(item["plan_identity"]) for item in self._manifest if item["plan_identity"]]
        incomplete = sum(not bool(item["identity_complete"]) for item in self._manifest)
        observed = deepcopy(payload)
        observed["validation_shadow"] = {
            "schema": "workflow_validation_shadow.v1",
            "ok": incomplete == 0,
            "reason": "" if incomplete == 0 else "validation_shadow_identity_incomplete",
            "scenario_count": len(self._manifest),
            "plan_build_count": len(self._manifest),
            "unique_plan_identity_count": len(set(identities)),
            "duplicate_plan_build_count": len(identities) - len(set(identities)),
            "identity_incomplete_count": incomplete,
            "manifest": deepcopy(self._manifest),
            "read_only": True,
            "enforcement": False,
            "cache_enabled": False,
            "slo_gate": False,
        }
        return observed


def compact_shadow(value: dict[str, Any]) -> dict[str, Any]:
    """Project bounded low-cardinality fields for the default CLI."""

    fields = (
        "schema", "ok", "reason", "scenario_count", "plan_build_count",
        "unique_plan_identity_count", "duplicate_plan_build_count",
        "identity_incomplete_count", "read_only", "enforcement",
        "cache_enabled", "slo_gate",
    )
    projected = {key: value.get(key) for key in fields}
    projected["full_result_ref"] = "command:python _bridge/workflow_orchestrator.py validate --full"
    return projected
