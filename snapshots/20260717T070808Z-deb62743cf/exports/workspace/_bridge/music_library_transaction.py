#!/usr/bin/env python3
"""Transactional move, rollback, and verification for music library plans.

Ownership: plan-bound same-volume moves, preflight hashes, non-overwrite checks,
process locking, durable journals, rollback, and post-state verification.
Non-goals: planning, network access, device discovery, deletion, transcoding,
tag rewriting, cross-volume copies, or cleanup of source directories.
State behavior: mutation occurs only through explicit apply or rollback calls
with a matching plan id and fresh hardware evidence.
Caller context: music_library_owner.py.
"""

from __future__ import annotations

import csv
import io
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from music_library_planner import SCHEMA, sha256_file, validate_plan_structure


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_path(root: Path, relative: str) -> Path:
    candidate_relative = Path(str(relative or ""))
    if candidate_relative.is_absolute() or ".." in candidate_relative.parts:
        raise ValueError(f"unsafe_relative_path:{relative}")
    candidate = (root / candidate_relative).resolve()
    if os.path.commonpath([str(root.resolve()), str(candidate)]) != str(root.resolve()):
        raise ValueError(f"path_outside_root:{relative}")
    return candidate


def write_new_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return
        raise FileExistsError(f"refusing_to_overwrite:{path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_journal_line:{line_number}") from exc
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            raise ValueError(f"invalid_journal_row:{line_number}")
    return rows


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise RuntimeError(f"music_library_operation_busy:{path}") from exc
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def verify_hardware(plan: dict[str, Any], fresh: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected = plan.get("hardware_binding") if isinstance(plan.get("hardware_binding"), dict) else {}
    if not expected.get("stable_fingerprint"):
        issues.append({"code": "plan_hardware_binding_missing"})
    if expected.get("stable_fingerprint") != fresh.get("stable_fingerprint"):
        issues.append(
            {
                "code": "hardware_fingerprint_changed",
                "expected": expected.get("stable_fingerprint"),
                "actual": fresh.get("stable_fingerprint"),
            }
        )
    if not fresh.get("safe_for_content_mutation"):
        issues.append({"code": "hardware_not_safe_for_content_mutation", "hardware_issues": fresh.get("issues", [])})
    expected_drive = Path(str(plan.get("root") or "")).drive.rstrip(":").upper()
    if expected_drive and expected_drive != str(fresh.get("drive_letter") or "").upper():
        issues.append({"code": "hardware_drive_letter_changed", "expected": expected_drive, "actual": fresh.get("drive_letter")})
    return issues


def journal_state(rows: list[dict[str, Any]], plan_id: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if rows and any(str(row.get("plan_id") or "") != plan_id for row in rows):
        issues.append({"code": "journal_plan_id_mismatch"})
    complete_count = sum(1 for row in rows if row.get("event") == "complete")
    if complete_count > 1:
        issues.append({"code": "journal_multiple_complete_events"})
    rollback_complete_count = sum(1 for row in rows if row.get("event") == "rollback_complete")
    if rollback_complete_count > 1:
        issues.append({"code": "journal_multiple_rollback_complete_events"})
    return {
        "issues": issues,
        "complete": complete_count == 1,
        "rollback_complete": rollback_complete_count == 1,
        "move_ids": {str(row.get("item_id") or "") for row in rows if row.get("event") == "move"},
        "rollback_ids": {str(row.get("item_id") or "") for row in rows if row.get("event") == "rollback"},
    }


def preflight_entries(plan: dict[str, Any], *, mode: str) -> list[dict[str, Any]]:
    issues = validate_plan_structure(plan)
    if issues:
        return issues
    root = Path(plan["root"]).resolve()
    for row in plan["entries"]:
        source = safe_path(root, row["source"])
        target = safe_path(root, row["target"])
        expected_size = int(row["size_bytes"])
        expected_hash = str(row["sha256"])
        if mode == "apply":
            if source.is_file():
                if source.stat().st_size != expected_size:
                    issues.append({"code": "source_size_changed", "item_id": row["item_id"], "source": row["source"]})
                elif sha256_file(source) != expected_hash:
                    issues.append({"code": "source_hash_changed", "item_id": row["item_id"], "source": row["source"]})
                if target.exists():
                    issues.append({"code": "target_already_exists", "item_id": row["item_id"], "target": row["target"]})
            elif target.is_file() and target.stat().st_size == expected_size and sha256_file(target) == expected_hash:
                continue
            else:
                issues.append({"code": "source_missing", "item_id": row["item_id"], "source": row["source"]})
        else:
            if target.is_file():
                if target.stat().st_size != expected_size or sha256_file(target) != expected_hash:
                    issues.append({"code": "rollback_target_changed", "item_id": row["item_id"], "target": row["target"]})
                if source.exists():
                    issues.append({"code": "rollback_source_already_exists", "item_id": row["item_id"], "source": row["source"]})
            elif source.is_file() and source.stat().st_size == expected_size and sha256_file(source) == expected_hash:
                continue
            else:
                issues.append({"code": "rollback_target_missing", "item_id": row["item_id"], "target": row["target"]})
    return issues


def _record_artifacts(root: Path, plan: dict[str, Any], plan_path: Path, receipt: dict[str, Any]) -> dict[str, str]:
    records = root / "整理记录"
    plan_copy = records / f"plan-{plan['plan_id']}.json"
    receipt_path = records / f"apply-receipt-{plan['plan_id']}.json"
    inventory_path = records / f"inventory-{plan['plan_id']}.csv"
    write_new_text(plan_copy, plan_path.read_text(encoding="utf-8"))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=("item_id", "kind", "source", "target", "size_bytes", "sha256", "disposition"),
    )
    writer.writeheader()
    for row in plan["entries"]:
        writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    write_new_text(inventory_path, "\ufeff" + buffer.getvalue())
    receipt_text = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if receipt_path.is_file():
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("plan_id") != plan["plan_id"] or not existing.get("ok"):
            raise ValueError(f"invalid_existing_receipt:{receipt_path}")
    else:
        write_new_text(receipt_path, receipt_text)
    pending = root / "待确认"
    if pending.is_dir():
        marker = pending / ".nomedia"
        if not marker.exists():
            marker.write_bytes(b"")
    return {"plan": str(plan_copy), "receipt": str(receipt_path), "inventory": str(inventory_path)}


def apply_plan(
    plan: dict[str, Any],
    *,
    plan_path: Path,
    confirm_plan_id: str,
    fresh_hardware: dict[str, Any],
    journal_path: Path,
) -> dict[str, Any]:
    if confirm_plan_id != plan.get("plan_id"):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "confirmation_plan_id_mismatch"}
    hardware_issues = verify_hardware(plan, fresh_hardware)
    if hardware_issues:
        return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "hardware_binding_failed", "issues": hardware_issues}
    lock_path = journal_path.with_suffix(journal_path.suffix + ".lock")
    with process_lock(lock_path):
        issues = preflight_entries(plan, mode="apply")
        if issues:
            return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "preflight_failed", "issues": issues}
        root = Path(plan["root"]).resolve()
        try:
            existing = load_jsonl(journal_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "journal_invalid", "detail": str(exc)}
        state = journal_state(existing, str(plan["plan_id"]))
        if state["issues"]:
            return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "journal_invalid", "issues": state["issues"]}
        if state["rollback_complete"]:
            return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "plan_already_rolled_back"}
        if state["complete"]:
            validation = validate_state(plan, expected="applied")
            return {
                "schema": f"{SCHEMA}.apply_receipt",
                "ok": bool(validation.get("ok")),
                "reason": "already_complete" if validation.get("ok") else "completed_state_validation_failed",
                "plan_id": plan["plan_id"],
                "already_complete": True,
                "validation": validation,
            }
        if not existing:
            append_jsonl(journal_path, {"event": "header", "plan_id": plan["plan_id"], "time": now_iso(), "root": str(root)})
        moved = 0
        resumed = 0
        for row in plan["entries"]:
            source = safe_path(root, row["source"])
            target = safe_path(root, row["target"])
            if (
                not source.exists()
                and target.is_file()
                and target.stat().st_size == int(row["size_bytes"])
                and sha256_file(target) == row["sha256"]
            ):
                resumed += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)
            if target.stat().st_size != int(row["size_bytes"]) or sha256_file(target) != row["sha256"]:
                if not source.exists() and target.exists():
                    os.rename(target, source)
                raise RuntimeError(f"post_move_integrity_mismatch:{row['item_id']}")
            append_jsonl(
                journal_path,
                {
                    "event": "move",
                    "plan_id": plan["plan_id"],
                    "time": now_iso(),
                    "item_id": row["item_id"],
                    "source": row["source"],
                    "target": row["target"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                },
            )
            moved += 1
        validation = validate_state(plan, expected="applied")
        if not validation.get("ok"):
            return {
                "schema": f"{SCHEMA}.apply",
                "ok": False,
                "reason": "post_apply_validation_failed",
                "plan_id": plan["plan_id"],
                "validation": validation,
            }
        receipt = {
            "schema": f"{SCHEMA}.apply_receipt",
            "ok": True,
            "plan_id": plan["plan_id"],
            "completed_at": now_iso(),
            "moved_count": moved,
            "resumed_count": resumed,
            "journal": str(journal_path),
            "hardware_fingerprint": fresh_hardware.get("stable_fingerprint"),
            "content_rewritten": False,
            "files_deleted": False,
            "validation": validation,
        }
        receipt["artifacts"] = _record_artifacts(root, plan, plan_path, receipt)
        append_jsonl(journal_path, {"event": "complete", "plan_id": plan["plan_id"], "time": now_iso()})
        return receipt


def rollback_plan(
    plan: dict[str, Any],
    *,
    confirm_plan_id: str,
    fresh_hardware: dict[str, Any],
    journal_path: Path,
) -> dict[str, Any]:
    if confirm_plan_id != plan.get("plan_id"):
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "confirmation_plan_id_mismatch"}
    hardware_issues = verify_hardware(plan, fresh_hardware)
    if hardware_issues:
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "hardware_binding_failed", "issues": hardware_issues}
    if not journal_path.is_file():
        return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "journal_missing"}
    lock_path = journal_path.with_suffix(journal_path.suffix + ".lock")
    with process_lock(lock_path):
        try:
            rows = load_jsonl(journal_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "journal_invalid", "detail": str(exc)}
        state = journal_state(rows, str(plan["plan_id"]))
        if state["issues"]:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "journal_invalid", "issues": state["issues"]}
        if state["rollback_complete"]:
            validation = validate_state(plan, expected="source")
            return {
                "schema": f"{SCHEMA}.rollback",
                "ok": bool(validation.get("ok")),
                "reason": "already_rolled_back" if validation.get("ok") else "rolled_back_state_validation_failed",
                "plan_id": plan["plan_id"],
                "already_rolled_back": True,
                "validation": validation,
            }
        if not state["complete"] and not state["move_ids"]:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "no_applied_moves"}
        issues = preflight_entries(plan, mode="rollback")
        if issues:
            return {"schema": f"{SCHEMA}.rollback", "ok": False, "reason": "preflight_failed", "issues": issues}
        root = Path(plan["root"]).resolve()
        restored = 0
        for row in reversed(plan["entries"]):
            source = safe_path(root, row["source"])
            target = safe_path(root, row["target"])
            if source.is_file() and not target.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            os.rename(target, source)
            append_jsonl(
                journal_path,
                {"event": "rollback", "plan_id": plan["plan_id"], "time": now_iso(), "item_id": row["item_id"]},
            )
            restored += 1
        validation = validate_state(plan, expected="source")
        if not validation.get("ok"):
            return {
                "schema": f"{SCHEMA}.rollback",
                "ok": False,
                "reason": "post_rollback_validation_failed",
                "plan_id": plan["plan_id"],
                "validation": validation,
            }
        append_jsonl(journal_path, {"event": "rollback_complete", "plan_id": plan["plan_id"], "time": now_iso()})
        return {
            "schema": f"{SCHEMA}.rollback",
            "ok": True,
            "plan_id": plan["plan_id"],
            "restored_count": restored,
            "completed_at": now_iso(),
            "records_retained": True,
            "validation": validation,
        }


def validate_state(plan: dict[str, Any], *, expected: str) -> dict[str, Any]:
    issues = validate_plan_structure(plan)
    if issues:
        return {"schema": f"{SCHEMA}.state_validation", "ok": False, "issues": issues}
    if expected not in {"source", "applied"}:
        raise ValueError("expected_must_be_source_or_applied")
    root = Path(plan["root"]).resolve()
    checked = 0
    total_bytes = 0
    for row in plan["entries"]:
        relative = row["source"] if expected == "source" else row["target"]
        path = safe_path(root, relative)
        if not path.is_file():
            issues.append({"code": "expected_file_missing", "item_id": row["item_id"], "path": relative})
            continue
        actual_size = path.stat().st_size
        if actual_size != int(row["size_bytes"]):
            issues.append({"code": "size_mismatch", "item_id": row["item_id"], "path": relative})
            continue
        if sha256_file(path) != row["sha256"]:
            issues.append({"code": "hash_mismatch", "item_id": row["item_id"], "path": relative})
            continue
        checked += 1
        total_bytes += actual_size
    return {
        "schema": f"{SCHEMA}.state_validation",
        "ok": not issues,
        "plan_id": plan.get("plan_id"),
        "expected": expected,
        "checked_count": checked,
        "checked_bytes": total_bytes,
        "issues": issues,
    }
