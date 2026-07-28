#!/usr/bin/env python3
"""Shared validation helpers for machine-first CLI contracts.

Ownership: small reusable CLI argument validators for _bridge tools.
Non-goals: business-specific state transitions or command execution.
State behavior: pure validation; no filesystem or process side effects.
Caller context: argparse entrypoints and programmatic guards for machine fields.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable


def _sorted_allowed(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def enum_help(allowed: Iterable[str]) -> str:
    return "|".join(_sorted_allowed(allowed))


def normalize_enum_value(
    value: str,
    *,
    allowed: Iterable[str],
    field_name: str,
    prose_destination: str = "",
) -> str:
    """Normalize and validate a machine enum value.

    Natural-language descriptions must live in summary/detail fields. This
    helper intentionally fails fast so prose cannot silently enter status,
    outcome, route, severity, or similar machine fields.
    """
    normalized = str(value or "").strip().lower()
    allowed_values = _sorted_allowed(allowed)
    if normalized in allowed_values:
        return normalized
    target_hint = f" Put prose in {prose_destination}." if prose_destination else ""
    raise argparse.ArgumentTypeError(
        f"{field_name} must be one of {enum_help(allowed_values)}; got {value!r}.{target_hint}"
    )


def enum_arg(
    field_name: str,
    allowed: Iterable[str],
    *,
    prose_destination: str = "",
):
    """Return an argparse type function for a machine enum field."""

    def parse(value: str) -> str:
        return normalize_enum_value(
            value,
            allowed=allowed,
            field_name=field_name,
            prose_destination=prose_destination,
        )

    return parse
