#!/usr/bin/env python3
"""Focused isolated behavior regression for persistent_task_kernel.

Ownership: task lifecycle behavior checks.
Non-goals: mutate production scheduler, mail, bridge, or runtime state.
State behavior: temporary SQLite database only.
Caller context: targeted validation for the sidecar kernel.
"""

from __future__ import annotations

import json

from persistent_task_kernel import behavior_evaluation


def main() -> int:
    result = behavior_evaluation()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
