"""Govern PMB workspace identity rebind and retirement.

Ownership: identity-only rebinds plus lifecycle checks and reversible retirement
for PMB workspace data.
Non-goals: editing memories, changing workspace ids, or deleting data.
State behavior: plans are read-only; rebind apply preserves the event database,
while retirement apply moves an eligible workspace into a quarantine root and
writes a durable negative tombstone.
Caller context: invoked through ``memory_governance.py`` after explicit user
approval and an exact workspace identifier.
"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TOP_LEVEL_META_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yaml_scalar(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            parsed = json.loads(text)
            return str(parsed) if parsed is not None else ""
        except json.JSONDecodeError:
            return text
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _read_workspace_meta(path: Path) -> tuple[dict[str, str], list[str], dict[str, int], str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return {}, [], {}, f"{type(exc).__name__}: {exc}"
    values: dict[str, str] = {}
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t")):
            continue
        match = TOP_LEVEL_META_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in positions:
            return {}, lines, positions, f"duplicate_top_level_key:{key}"
        positions[key] = index
        values[key] = _yaml_scalar(match.group(2))
    missing = [key for key in ("id", "name", "root", "created_at", "source") if key not in positions]
    if missing:
        return values, lines, positions, "missing_top_level_keys:" + ",".join(missing)
    return values, lines, positions, ""


def workspace_rebind_plan(
    workspaces_root: Path,
    workspace_id: str,
    *,
    target_name: str,
    target_root: str,
) -> dict[str, Any]:
    workspace_path, path_error = _safe_workspace_path(workspaces_root, workspace_id)
    blockers: list[dict[str, str]] = []
    if path_error:
        blockers.append({"code": path_error, "detail": "Use a single PMB workspace id, not a path."})
    clean_name = str(target_name or "").strip()
    clean_root = str(target_root or "").strip()
    if not clean_name:
        blockers.append({"code": "target_name_required", "detail": "Provide the current workspace display name."})
    root_is_absolute = clean_root.startswith(("/", "\\\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", clean_root))
    if not root_is_absolute:
        blockers.append({"code": "target_root_not_absolute", "detail": clean_root})
    if workspace_path is None:
        return {
            "schema": "memory_pmb_workspaces.rebind_plan.v1",
            "ok": False,
            "eligible": False,
            "workspace_id": workspace_id,
            "blockers": blockers,
        }
    meta_path = workspace_path / "meta.yaml"
    db_path = workspace_path / "events.sqlite"
    if not workspace_path.is_dir():
        blockers.append({"code": "workspace_missing", "detail": str(workspace_path)})
    if not meta_path.is_file():
        blockers.append({"code": "workspace_meta_missing", "detail": str(meta_path)})
    values, _, _, meta_error = _read_workspace_meta(meta_path) if meta_path.is_file() else ({}, [], {}, "missing")
    if meta_error:
        blockers.append({"code": "workspace_meta_invalid", "detail": meta_error})
    if values.get("id") and values.get("id") != workspace_id:
        blockers.append({"code": "workspace_meta_id_mismatch", "detail": str(values.get("id"))})
    counts = _event_counts(db_path) if db_path.is_file() else {
        "ok": False,
        "db_exists": False,
        "total_events": None,
        "active_events": None,
        "quick_check": "missing",
    }
    if not counts.get("ok"):
        blockers.append({"code": "workspace_db_unhealthy", "detail": str(counts.get("error") or counts.get("quick_check"))})
    before = {key: values.get(key, "") for key in ("id", "name", "root", "source", "created_at")}
    after = {**before, "name": clean_name, "root": clean_root, "source": "explicit"}
    return {
        "schema": "memory_pmb_workspaces.rebind_plan.v1",
        "ok": not blockers,
        "eligible": not blockers,
        "workspace_id": workspace_id,
        "workspace_path": str(workspace_path),
        "meta_path": str(meta_path),
        "meta_sha256": _sha256(meta_path) if meta_path.is_file() else "",
        "db_path": str(db_path),
        "before": before,
        "after": after,
        "would_change": before != after,
        "event_counts": counts,
        "events_sha256": _sha256(db_path) if db_path.is_file() else "",
        "blockers": blockers,
        "apply_contract": {
            "requires_confirm_apply": True,
            "requires_routed_backup": True,
            "preserves_workspace_id": True,
            "preserves_event_database": True,
            "daemon_restart_required": True,
        },
    }


def workspace_rebind_apply(
    workspaces_root: Path,
    workspace_id: str,
    *,
    target_name: str,
    target_root: str,
    confirm: bool,
    expected_meta_sha256: str = "",
) -> dict[str, Any]:
    plan = workspace_rebind_plan(
        workspaces_root,
        workspace_id,
        target_name=target_name,
        target_root=target_root,
    )
    if not confirm:
        return {
            "schema": "memory_pmb_workspaces.rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "confirmation_required",
            "plan": plan,
        }
    if not plan.get("eligible"):
        return {
            "schema": "memory_pmb_workspaces.rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_not_eligible",
            "plan": plan,
        }
    current_meta_sha256 = str(plan.get("meta_sha256") or "")
    if expected_meta_sha256 and current_meta_sha256 != expected_meta_sha256:
        return {
            "schema": "memory_pmb_workspaces.rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_meta_changed_after_backup",
            "expected_meta_sha256": expected_meta_sha256,
            "current_meta_sha256": current_meta_sha256,
        }
    meta_path = Path(str(plan["meta_path"]))
    values, lines, positions, meta_error = _read_workspace_meta(meta_path)
    if meta_error:
        return {
            "schema": "memory_pmb_workspaces.rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_meta_changed_after_plan",
            "detail": meta_error,
        }
    if _sha256(meta_path) != current_meta_sha256:
        return {
            "schema": "memory_pmb_workspaces.rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_meta_changed_before_write",
        }
    replacements = {
        "name": str(plan["after"]["name"]),
        "root": str(plan["after"]["root"]),
        "source": "explicit",
    }
    for key, value in replacements.items():
        lines[positions[key]] = f"{key}: {json.dumps(value, ensure_ascii=False)}"
    payload = "\n".join(lines) + "\n"
    temp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    temp.write_text(payload, encoding="utf-8", newline="\n")
    temp.replace(meta_path)
    after_plan = workspace_rebind_plan(
        workspaces_root,
        workspace_id,
        target_name=target_name,
        target_root=target_root,
    )
    db_unchanged = str(after_plan.get("events_sha256") or "") == str(plan.get("events_sha256") or "")
    identity_matches = after_plan.get("before") == plan.get("after")
    return {
        "schema": "memory_pmb_workspaces.rebind_apply.v1",
        "ok": bool(after_plan.get("ok") and db_unchanged and identity_matches),
        "applied": True,
        "workspace_id": workspace_id,
        "meta_path": str(meta_path),
        "before": plan.get("before"),
        "after": after_plan.get("before"),
        "event_counts": after_plan.get("event_counts"),
        "postconditions": {
            "workspace_id_preserved": str((after_plan.get("before") or {}).get("id") or "") == workspace_id,
            "identity_matches": identity_matches,
            "event_database_unchanged": db_unchanged,
        },
        "daemon_restart_required": True,
        "previous_values": values,
    }


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
