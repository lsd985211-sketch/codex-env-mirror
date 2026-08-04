#!/usr/bin/env python3
"""Read-only structural observation for workflow validation.

Ownership: measure validator semantic groups and already-produced plan evidence.
Non-goals: select, skip, reorder, parallelize, cache, or judge validation checks.
State behavior: process-local and read-only; no metrics store or runtime writes.
Caller context: the workflow_orchestrator validation facade attaches one bounded
observation projection after all existing validation decisions are complete.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any


Clock = Callable[[], int]


class ValidationObservation:
    """Collect process-local structure metrics without affecting validation."""

    def __init__(self, *, clock_ns: Clock = time.perf_counter_ns) -> None:
        self._clock_ns = clock_ns
        self._active_groups: dict[str, int] = {}
        self._elapsed_ms: dict[str, float] = {}
        self._plan_build_count = 0
        self._maintenance_source_scan_count = 0

    def start_group(self, name: str) -> None:
        key = str(name or "").strip()
        if not key or key in self._active_groups or key in self._elapsed_ms:
            raise ValueError("validation_observation_group_not_startable")
        self._active_groups[key] = self._clock_ns()

    def finish_group(self, name: str) -> None:
        key = str(name or "").strip()
        started = self._active_groups.pop(key, None)
        if started is None:
            raise ValueError("validation_observation_group_not_active")
        self._elapsed_ms[key] = round((self._clock_ns() - started) / 1_000_000, 3)

    def record_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Count one completed plan and consume only its existing metric projection."""

        self._plan_build_count += 1
        route_pack = plan.get("execution_route_pack") if isinstance(plan.get("execution_route_pack"), dict) else {}
        environment = route_pack.get("environment_context") if isinstance(route_pack.get("environment_context"), dict) else {}
        metrics = environment.get("maintenance_query_metrics") if isinstance(environment.get("maintenance_query_metrics"), dict) else {}
        try:
            scans = int(metrics.get("source_scan_count") or 0)
        except (TypeError, ValueError):
            scans = 0
        self._maintenance_source_scan_count += max(0, scans)
        return plan

    def attach(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copied payload with non-enforcing observation metadata."""

        if self._active_groups:
            raise ValueError("validation_observation_group_unfinished")
        output_bytes = len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        observed = deepcopy(payload)
        observed["validation_observation"] = {
            "schema": "workflow_validation_observation.v1",
            "semantic_group_elapsed_ms": dict(self._elapsed_ms),
            "plan_build_count": self._plan_build_count,
            "maintenance_source_scan_count": self._maintenance_source_scan_count,
            "maintenance_source_scan_scope": "recorded_plan_environment_contexts",
            "serialized_output_bytes": output_bytes,
            "serialized_output_scope": "validation_payload_before_observation",
            "read_only": True,
            "enforcement": False,
            "slo_gate": False,
        }
        return observed
