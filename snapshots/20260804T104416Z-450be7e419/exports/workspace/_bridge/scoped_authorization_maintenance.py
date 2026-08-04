"""Effect-transition planning for the scoped authorization owner.

Ownership: define the small, owner-only compatibility bridge that keeps an
operation's effect journal monotonic when a consumer can only report a verified
effect observation.  Non-goals: authenticate callers, persist state, alter
scope, or relax permit consumption.  State behavior: pure transition planning;
``scoped_authorization`` remains the sole journal writer.
"""

from __future__ import annotations

from typing import Any


_TRANSITIONS = {
    "permit_reserved": {"effect_started", "cancelled"},
    "effect_started": {"effect_unknown", "effect_observed", "compensation_required"},
    "effect_unknown": {"effect_observed", "compensation_required"},
    "effect_observed": {"completed", "compensation_required"},
}


def effect_transition_plan(current: str, requested: str) -> dict[str, Any]:
    """Return the owner-safe plan for one requested operation transition."""

    if requested == current:
        return {"ok": True, "reused": True}
    if current == "completed" and requested in {"effect_observed", "completed"}:
        return {"ok": True, "reused": True, "recovered_terminal_effect": True}
    if current == "permit_reserved" and requested == "effect_observed":
        return {
            "ok": True,
            "implicit_effect_started": True,
            "implicit_from": current,
            "implicit_to": "effect_started",
        }
    if requested not in _TRANSITIONS.get(current, set()):
        return {
            "ok": False,
            "reason": "authorization_effect_transition_invalid",
            "from": current,
            "to": requested,
        }
    return {"ok": True}
