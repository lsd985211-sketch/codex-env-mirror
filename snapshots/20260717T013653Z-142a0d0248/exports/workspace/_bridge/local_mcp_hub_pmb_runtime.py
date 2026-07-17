"""Singleflight concurrency control for local Hub PMB recovery.

Ownership: coalesce simultaneous daemon recovery attempts in one Hub process.
Non-goals: PMB HTTP, permissions, process launch, or multiple tool retries.
State behavior: in-memory only; cross-process locking belongs to PMB owner.
Caller context: ``local_mcp_hub.py`` injects the daemon-ensure callable.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class PmbRecoverySingleFlight:
    def __init__(self, *, wait_timeout_seconds: float = 60.0) -> None:
        self._condition = threading.Condition()
        self._in_flight = False
        self._generation = 0
        self._last_completed_at = 0.0
        self._last_result: dict[str, Any] | None = None
        self._wait_timeout_seconds = wait_timeout_seconds

    def recover(
        self,
        ensure: Callable[[], dict[str, Any]],
        *,
        failure_observed_at: float | None = None,
    ) -> dict[str, Any]:
        observed_at = time.monotonic() if failure_observed_at is None else float(failure_observed_at)
        with self._condition:
            if (
                self._last_result is not None
                and self._last_completed_at >= observed_at
                and bool(self._last_result.get("ok"))
            ):
                return self._annotated(self._last_result, role="reused_after_failure", coalesced=True)

            if self._in_flight:
                generation = self._generation
                deadline = time.monotonic() + self._wait_timeout_seconds
                while self._in_flight and self._generation == generation:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {
                            "ok": False,
                            "reason": "pmb_recovery_wait_timeout",
                            "_singleflight": {
                                "role": "waiter_timeout",
                                "coalesced": True,
                                "generation": generation,
                            },
                        }
                    self._condition.wait(timeout=remaining)
                result = self._last_result or {"ok": False, "reason": "pmb_recovery_result_missing"}
                return self._annotated(result, role="waiter", coalesced=True)

            self._in_flight = True
            self._generation += 1
            generation = self._generation

        try:
            result = ensure()
            if not isinstance(result, dict):
                result = {"ok": False, "reason": "pmb_recovery_result_not_object"}
        except Exception as exc:
            result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

        with self._condition:
            self._last_result = dict(result)
            self._last_completed_at = time.monotonic()
            self._in_flight = False
            self._condition.notify_all()
        return self._annotated(result, role="leader", coalesced=False, generation=generation)

    @staticmethod
    def _annotated(
        result: dict[str, Any], *, role: str, coalesced: bool,
        generation: int | None = None,
    ) -> dict[str, Any]:
        payload = dict(result)
        metadata = {"role": role, "coalesced": coalesced}
        if generation is not None:
            metadata["generation"] = generation
        payload["_singleflight"] = metadata
        return payload
