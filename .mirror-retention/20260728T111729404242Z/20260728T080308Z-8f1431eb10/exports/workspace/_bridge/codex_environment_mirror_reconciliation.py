#!/usr/bin/env python3
"""Pure scoped-drift selection for the Codex environment mirror.

Ownership: derive the declared publish closure from an already validated global
drift-review result. Non-goals: read or write receipts, run owner validators,
select sources, or mutate mirror state. State behavior: pure. Caller context:
``codex_environment_mirror`` passes its signature-bound global review result
before creating a refresh preflight.
"""

from __future__ import annotations

from typing import Any


def partition_scoped_drift_rows(
    rows: list[dict[str, Any]],
    allowed_source_ids: set[str],
    *,
    global_review_consumed: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Keep unreviewed external drift fail-closed without re-reviewing it.

    A true ``global_review_consumed`` is supplied only by the mirror owner's
    signature- and owner-evidence-validated receipt consumer. Such rows remain
    outside the publication closure and are represented as isolated evidence;
    they are never selected for capture or reverse absorption.
    """

    scoped_rows: list[dict[str, Any]] = []
    isolated_reviewed_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row.get("source_id") or "")
        if source_id in allowed_source_ids:
            scoped_rows.append(row)
            continue
        item_id = str(row.get("item_id") or "")
        classification = str(row.get("classification") or "")
        if global_review_consumed:
            isolated_reviewed_rows.append({
                key: row.get(key)
                for key in (
                    "item_id",
                    "source_id",
                    "classification",
                    "owner",
                    "review_disposition",
                    "owner_receipt_ref",
                )
            })
            continue
        blockers.append({
            "item_id": item_id,
            "source_id": source_id,
            "classification": classification,
            "reason": "drift_outside_declared_publish_closure",
        })
    return {
        "scoped_rows": scoped_rows,
        "isolated_reviewed_rows": isolated_reviewed_rows,
        "blockers": blockers,
    }
