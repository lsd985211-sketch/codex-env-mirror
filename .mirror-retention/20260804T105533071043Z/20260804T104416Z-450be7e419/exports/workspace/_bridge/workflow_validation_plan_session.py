#!/usr/bin/env python3
"""Per-validation-session plan construction reuse.

Ownership: reuse only equivalent ``build_plan`` results during one
``workflow_orchestrator.validate`` invocation. Non-goals: persistence, TTLs,
authorization reuse, receipt reuse, validation selection, or check skipping.
State behavior: process-local memory discarded with the validation call.
Caller context: the workflow orchestrator remains the validation facade.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from workflow_validation_shadow import signature_for_value


BuildPlan = Callable[..., dict[str, Any]]
_KNOWN_DETAILS = frozenset({"micro", "standard", "full", "auto"})


def _message(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _detail(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw in _KNOWN_DETAILS else ""


class ValidationPlanSession:
    """Reuse deep-copied plans only while one validator call is in progress."""

    def __init__(
        self,
        build_plan: BuildPlan,
        *,
        skill_routing_context: Any,
        skill_context_signature: str,
        workflow_source_signature: str,
    ) -> None:
        self._build_plan = build_plan
        self._skill_routing_context = skill_routing_context
        self._skill_context_signature = str(skill_context_signature or "").strip()
        self._workflow_source_signature = str(workflow_source_signature or "").strip()
        self._entries: dict[str, dict[str, Any]] = {}
        self._plan_request_count = 0
        self._executed_plan_build_count = 0
        self._intra_validation_reuse_count = 0
        self._fail_closed_build_count = 0

    def _canonical_input(self, message: Any, detail: Any) -> dict[str, str] | None:
        normalized_message = _message(message)
        normalized_detail = _detail(detail)
        if not (
            normalized_message
            and normalized_detail
            and message == normalized_message
            and self._skill_context_signature
            and self._workflow_source_signature
        ):
            return None
        return {
            "message": normalized_message,
            "detail": normalized_detail,
            "skill_context_signature": self._skill_context_signature,
            "workflow_source_signature": self._workflow_source_signature,
        }

    def plan_for(self, message: str, *, detail: str = "full") -> dict[str, Any]:
        """Build, or return a defensive copy for an exact in-session identity."""

        self._plan_request_count += 1
        canonical = self._canonical_input(message, detail)
        identity = signature_for_value(canonical) if canonical is not None else ""
        entry = self._entries.get(identity) if identity else None
        if entry is not None:
            try:
                if entry.get("canonical_input") == canonical and isinstance(entry.get("plan"), dict):
                    reused = deepcopy(entry["plan"])
                    self._intra_validation_reuse_count += 1
                    return reused
            except Exception:  # noqa: BLE001 - a cache anomaly must never suppress a plan build.
                pass
            self._entries.pop(identity, None)

        self._executed_plan_build_count += 1
        if canonical is None:
            self._fail_closed_build_count += 1
        plan = self._build_plan(
            message,
            detail=detail,
            skill_routing_context=self._skill_routing_context,
        )
        if not identity or not isinstance(plan, dict):
            return plan
        try:
            self._entries[identity] = {
                "canonical_input": canonical,
                "plan": deepcopy(plan),
            }
        except Exception:  # noqa: BLE001 - an uncopyable plan remains an uncached normal result.
            self._fail_closed_build_count += 1
        return plan

    def report(self) -> dict[str, Any]:
        return {
            "schema": "workflow_validation_plan_session.v1",
            "ok": True,
            "plan_request_count": self._plan_request_count,
            "executed_plan_build_count": self._executed_plan_build_count,
            "intra_validation_reuse_count": self._intra_validation_reuse_count,
            "fail_closed_build_count": self._fail_closed_build_count,
            "cache_entry_count": len(self._entries),
            "scope": "single_workflow_orchestrator_validate_invocation",
            "persistent": False,
            "receipt_reuse": False,
            "authorization_reuse": False,
            "check_skipping": False,
        }
