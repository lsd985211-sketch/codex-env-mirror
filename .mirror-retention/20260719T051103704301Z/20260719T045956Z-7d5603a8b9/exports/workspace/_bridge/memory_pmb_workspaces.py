"""Govern PMB workspace retirement.

Ownership: lifecycle checks and reversible retirement for PMB workspace data.
Non-goals: editing memories, selecting the active workspace, or deleting data.
State behavior: plans are read-only; apply moves an eligible workspace into a
quarantine root and writes a durable negative tombstone.
Caller context: invoked through ``memory_governance.py`` after explicit user
approval and an exact workspace identifier.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_workspace_path(workspaces_root: Path, workspace_id: str) -> tuple[Path | None, str]:
    candidate_id = str(workspace_id or "").strip()
    if not WORKSPACE_ID_RE.fullmatch(candidate_id):
        return None, "invalid_workspace_id"
    root = workspaces_root.resolve()
    candidate = (root / candidate_id).resolve()
    if candidate.parent != root:
        return None, "workspace_path_escapes_root"
    return candidate, ""


def _event_counts(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "ok": True,
            "db_exists": False,
            "total_events": 0,
            "active_events": 0,
            "quick_check": "missing",
        }
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            quick_check_row = conn.execute("PRAGMA quick_check").fetchone()
            quick_check = str(quick_check_row[0]) if quick_check_row else "unknown"
            table_row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
            if not table_row:
                return {
                    "ok": quick_check == "ok",
                    "db_exists": True,
                    "total_events": 0,
                    "active_events": 0,
                    "quick_check": quick_check,
                    "events_table_exists": False,
                }
            total = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            if "archived_at" in columns:
                active = int(
                    conn.execute("SELECT COUNT(*) FROM events WHERE archived_at IS NULL").fetchone()[0]
                )
            else:
                active = total
        finally:
            conn.close()
        return {
            "ok": quick_check == "ok",
            "db_exists": True,
            "total_events": total,
            "active_events": active,
            "quick_check": quick_check,
            "events_table_exists": True,
        }
    except Exception as exc:
        return {
            "ok": False,
            "db_exists": True,
            "total_events": None,
            "active_events": None,
            "quick_check": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _bounded_inventory(workspace_path: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    if workspace_path.exists():
        for path in workspace_path.rglob("*"):
            if not path.is_file():
                continue
            file_count += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return {"file_count": file_count, "total_bytes": total_bytes}


def workspace_retire_plan(
    workspaces_root: Path,
    workspace_id: str,
    *,
    active_workspace_id: str,
    quarantine_root: Path,
    tombstone_path: Path,
) -> dict[str, Any]:
    workspace_path, path_error = _safe_workspace_path(workspaces_root, workspace_id)
    blockers: list[dict[str, str]] = []
    if path_error:
        blockers.append({"code": path_error, "detail": "Use a single PMB workspace id, not a path."})
    if workspace_path is None:
        return {
            "schema": "memory_pmb_workspaces.retire_plan.v1",
            "ok": False,
            "eligible": False,
            "workspace_id": workspace_id,
            "blockers": blockers,
        }

    exists = workspace_path.is_dir()
    if not exists:
        blockers.append({"code": "workspace_missing", "detail": str(workspace_path)})
    if workspace_id == active_workspace_id:
        blockers.append(
            {"code": "active_workspace_protected", "detail": "The current PMB workspace cannot be retired."}
        )

    counts = _event_counts(workspace_path / "events.sqlite") if exists else {
        "ok": False,
        "db_exists": False,
        "total_events": None,
        "active_events": None,
        "quick_check": "missing",
    }
    if exists and not counts.get("ok"):
        blockers.append(
            {
                "code": "workspace_db_unhealthy",
                "detail": str(counts.get("error") or counts.get("quick_check")),
            }
        )
    if int(counts.get("total_events") or 0) > 0:
        blockers.append(
            {"code": "workspace_not_empty", "detail": f"total_events={counts.get('total_events')}"}
        )

    inventory = _bounded_inventory(workspace_path) if exists else {"file_count": 0, "total_bytes": 0}
    return {
        "schema": "memory_pmb_workspaces.retire_plan.v1",
        "ok": not blockers,
        "eligible": not blockers,
        "workspace_id": workspace_id,
        "active_workspace_id": active_workspace_id,
        "workspace_path": str(workspace_path),
        "quarantine_root": str(quarantine_root),
        "tombstone_path": str(tombstone_path),
        "counts": counts,
        "inventory": inventory,
        "blockers": blockers,
        "apply_contract": {
            "requires_confirm_apply": True,
            "action": "move_to_quarantine_and_write_negative_tombstone",
            "deletes_data": False,
            "restore": "Move the quarantined directory back only after removing or superseding the tombstone.",
        },
    }


def workspace_retire_apply(
    workspaces_root: Path,
    workspace_id: str,
    *,
    active_workspace_id: str,
    quarantine_root: Path,
    tombstone_path: Path,
    reason: str,
    confirm: bool,
) -> dict[str, Any]:
    plan = workspace_retire_plan(
        workspaces_root,
        workspace_id,
        active_workspace_id=active_workspace_id,
        quarantine_root=quarantine_root,
        tombstone_path=tombstone_path,
    )
    if not confirm:
        return {
            "schema": "memory_pmb_workspaces.retire_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "confirmation_required",
            "plan": plan,
        }
    if not plan.get("eligible"):
        return {
            "schema": "memory_pmb_workspaces.retire_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_not_eligible",
            "plan": plan,
        }

    source = Path(str(plan["workspace_path"]))
    retired_at = datetime.now(timezone.utc)
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / f"{retired_at.strftime('%Y%m%d-%H%M%S')}-{workspace_id}"
    if destination.exists():
        return {
            "schema": "memory_pmb_workspaces.retire_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "quarantine_destination_exists",
            "destination": str(destination),
        }

    shutil.move(str(source), str(destination))
    tombstone = {
        "schema": "memory_pmb_workspaces.negative_tombstone.v1",
        "workspace_id": workspace_id,
        "retired_at": retired_at.isoformat(),
        "reason": reason or "empty_accidental_workspace",
        "original_path": str(source),
        "quarantine_path": str(destination),
        "event_counts": plan.get("counts"),
        "inventory": plan.get("inventory"),
        "active_effect": "retired workspace must not be discovered, selected, started, indexed, or shown as active",
        "restore_contract": "Explicitly review this tombstone, move the directory back, then append a superseding restoration record.",
    }
    tombstone_path.parent.mkdir(parents=True, exist_ok=True)
    with tombstone_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(tombstone, ensure_ascii=False, sort_keys=True) + "\n")
    (destination / "RETIREMENT_TOMBSTONE.json").write_text(
        json.dumps(tombstone, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    postconditions = {
        "source_absent": not source.exists(),
        "quarantine_present": destination.is_dir(),
        "tombstone_present": tombstone_path.is_file(),
    }
    return {
        "schema": "memory_pmb_workspaces.retire_apply.v1",
        "ok": all(postconditions.values()),
        "applied": True,
        "workspace_id": workspace_id,
        "destination": str(destination),
        "tombstone_path": str(tombstone_path),
        "postconditions": postconditions,
        "tombstone": tombstone,
    }
