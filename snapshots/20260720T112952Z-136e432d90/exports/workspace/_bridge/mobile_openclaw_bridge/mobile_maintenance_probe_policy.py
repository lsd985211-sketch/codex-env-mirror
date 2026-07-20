#!/usr/bin/env python3
"""Read-only probe policy helpers for mobile bridge maintenance.

Ownership: deep/quick maintenance probe gating, skipped-probe payloads, and
timed probe wrappers used by mobile maintenance inspection.
Non-goals: read bridge queues, inspect processes, run repairs, mutate runtime
state, or decide whether a maintenance issue is actionable.
State behavior: read-only; callers supply probe functions and receive payloads.
Caller context: mobile_maintenance.py uses this module to keep probe policy
stable and reusable while the CLI remains the maintenance facade.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


DEEP_PROBE_SKIPPED = {
    "ok": None,
    "skipped": True,
    "reason": "deep probes are skipped by maintenance summary; use maintenance summary --deep, inspect, or doctor",
}
QUICK_PROBE_SKIPPED = {
    "ok": None,
    "skipped": True,
    "reason": "quick maintenance summary skips this slower probe; use maintenance summary --deep, inspect, or doctor",
}


def parse_deep_probe_allowlist(raw: Any) -> set[str]:
    """Normalize config allowlist data into a stable probe-name set."""

    if isinstance(raw, str):
        return {raw} if raw else set()
    if isinstance(raw, list):
        return {str(item) for item in raw if str(item)}
    return set()


class DeepProbePolicy:
    """Small read-only policy object for deciding and recording probe skips."""

    def __init__(self, *, deep_probes: bool, allowlist: set[str] | None = None) -> None:
        self.deep_probes = bool(deep_probes)
        self.allowlist = set(allowlist or set())

    def enabled(self, name: str) -> bool:
        return self.deep_probes and (not self.allowlist or name in self.allowlist)

    def skipped(self, name: str, extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {**DEEP_PROBE_SKIPPED, "layer": "skipped", "probe": name}
        if extra:
            payload.update(extra)
        timing = {"name": name, "ok": True, "status": "skipped_by_allowlist", "elapsed_ms": 0}
        return payload, timing


def timed_probe(name: str, fn: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    """Run a probe and return its value with a compact timing record."""

    started = time.monotonic()
    try:
        value = fn()
        status = "ok" if not (isinstance(value, dict) and value.get("ok") is False) else "non_ok"
        return value, {
            "name": name,
            "ok": not (isinstance(value, dict) and value.get("ok") is False),
            "status": status,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "layer": name,
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }, {
            "name": name,
            "ok": False,
            "status": "exception",
            "error_type": type(exc).__name__,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
