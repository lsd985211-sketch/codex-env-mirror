#!/usr/bin/env python3
"""Shared monotonic deadline budget for resource execution.

The declared timeout is a total request or batch budget. Callers pass only the
remaining time to each downstream phase so probes, owner resolution, retries,
and fallbacks cannot each consume a fresh copy of the original timeout.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceExecutionBudget:
    total_seconds: float
    started_monotonic: float
    deadline_monotonic: float

    @classmethod
    def start(cls, total_seconds: float | int | None) -> "ResourceExecutionBudget":
        total = max(0.0, float(total_seconds or 0.0))
        started = time.monotonic()
        deadline = started + total if total > 0 else 0.0
        return cls(total_seconds=total, started_monotonic=started, deadline_monotonic=deadline)

    @property
    def bounded(self) -> bool:
        return self.deadline_monotonic > 0

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def remaining_seconds(self) -> float:
        if not self.bounded:
            return float("inf")
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def exhausted(self) -> bool:
        return self.bounded and self.remaining_seconds() <= 0.0

    def timeout_seconds(self, *, cap: float | int | None = None, minimum: int = 1) -> int:
        remaining = self.remaining_seconds()
        if cap is not None:
            remaining = min(remaining, max(0.0, float(cap)))
        if math.isinf(remaining):
            remaining = max(float(minimum), float(cap or minimum))
        if remaining <= 0:
            return 0
        if self.bounded and remaining < minimum:
            return 0
        return max(minimum, int(math.floor(remaining)))

    def snapshot(self, *, phase: str = "") -> dict[str, Any]:
        remaining = self.remaining_seconds()
        return {
            "schema": "resource_execution_budget.v1",
            "phase": phase,
            "bounded": self.bounded,
            "total_seconds": self.total_seconds,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "remaining_seconds": None if math.isinf(remaining) else round(remaining, 3),
            "exhausted": self.exhausted(),
            "rule": "downstream phases receive only the remaining monotonic deadline budget",
        }


def validate() -> dict[str, Any]:
    bounded = ResourceExecutionBudget.start(2)
    unbounded = ResourceExecutionBudget.start(0)
    return {
        "schema": "resource_execution_budget.validate.v1",
        "ok": bounded.bounded and bounded.timeout_seconds(cap=1) == 1 and not unbounded.bounded,
        "bounded": bounded.snapshot(phase="validate"),
        "unbounded": unbounded.snapshot(phase="validate"),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
