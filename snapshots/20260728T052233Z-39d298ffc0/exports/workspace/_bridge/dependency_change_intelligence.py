#!/usr/bin/env python3
"""Stable CLI facade for external dependency change intelligence.

Ownership: stable command routing and JSON output for dependency intelligence.
Non-goals: resource transport, scheduling, repair execution, or product changes.
State behavior: delegates read-only product probes and owner-runtime writes to
``dependency_change_intelligence_process``.
Caller context: launch helpers, manual maintenance, and self-update governance.
"""

from __future__ import annotations

from dependency_change_intelligence_process import main


if __name__ == "__main__":
    raise SystemExit(main())
