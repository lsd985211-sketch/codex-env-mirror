#!/usr/bin/env python3
"""Govern cached Codex dependency runtimes.

Ownership: classifies active, rollback, install-residue, and quarantined runtime directories.
Non-goals: changing Codex providers, deleting caches, or touching Desktop/CLI package files.
State behavior: read-only by default; quarantine is explicit, reversible, and manifest-backed.
Caller context: Codex maintenance, startup diagnostics, and storage governance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.json_cli import configure_utf8_stdio, now_iso, print_json


RUNTIME_ROOT = Path.home() / ".cache" / "codex-runtimes"
QUARANTINE_ROOT = Path.home() / ".cache" / "codex-runtimes-quarantine"
CURRENT_NAME = "codex-primary-runtime"
PREVIOUS_PREFIX = CURRENT_NAME + ".previous-"
INSTALL_PREFIX = "codex-runtime-install-"
MIN_AGE_HOURS = 24.0


def directory_stats(path: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            candidate = Path(root) / name
            try:
                total += candidate.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def runtime_kind(name: str) -> str:
    if name == CURRENT_NAME:
        return "current"
    if name.startswith(PREVIOUS_PREFIX):
        return "previous"
    if name.startswith(INSTALL_PREFIX):
        return "install_residue"
    return "unknown"


def runtime_rows(root: Path = RUNTIME_ROOT) -> list[dict[str, Any]]:
    generated = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.casefold()):
        size, file_count = directory_stats(path)
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "kind": runtime_kind(path.name),
                "modified_at": modified.isoformat(),
                "age_hours": round((generated - modified).total_seconds() / 3600, 2),
                "size_bytes": size,
                "size_mb": round(size / 1024 / 1024, 2),
                "file_count": file_count,
                "complete_enough_for_rollback": file_count > 0 and runtime_kind(path.name) == "previous",
            }
        )
    return rows


def classify_retention(rows: list[dict[str, Any]]) -> dict[str, Any]:
    previous = sorted(
        [row for row in rows if row["kind"] == "previous" and row["complete_enough_for_rollback"]],
        key=lambda row: str(row["modified_at"]),
        reverse=True,
    )
    keep_previous = previous[0]["name"] if previous else ""
    candidates = [
        row
        for row in rows
        if row["kind"] in {"previous", "install_residue"}
        and row["name"] != keep_previous
        and float(row["age_hours"]) >= MIN_AGE_HOURS
    ]
    return {
        "current": [row for row in rows if row["kind"] == "current"],
        "keep_previous": keep_previous,
        "quarantine_candidates": candidates,
        "unknown": [row for row in rows if row["kind"] == "unknown"],
    }


def snapshot() -> dict[str, Any]:
    rows = runtime_rows()
    retention = classify_retention(rows)
    return {
        "schema": "codex_runtime_cache.snapshot.v1",
        "ok": RUNTIME_ROOT.exists(),
        "generated_at": now_iso(),
        "runtime_root": str(RUNTIME_ROOT),
        "quarantine_root": str(QUARANTINE_ROOT),
        "runtimes": rows,
        "retention": retention,
        "summary": {
            "runtime_count": len(rows),
            "total_mb": round(sum(int(row["size_bytes"]) for row in rows) / 1024 / 1024, 2),
            "quarantine_candidate_count": len(retention["quarantine_candidates"]),
            "quarantine_candidate_mb": round(sum(int(row["size_bytes"]) for row in retention["quarantine_candidates"]) / 1024 / 1024, 2),
        },
    }


def doctor() -> dict[str, Any]:
    snap = snapshot()
    issues: list[dict[str, Any]] = []
    if len(snap["retention"]["current"]) != 1:
        issues.append({"severity": "risk", "code": "current_runtime_missing_or_ambiguous"})
    if not snap["retention"]["keep_previous"]:
        issues.append({"severity": "advisory", "code": "rollback_runtime_missing"})
    if snap["retention"]["unknown"]:
        issues.append({"severity": "advisory", "code": "unknown_runtime_directories", "names": [row["name"] for row in snap["retention"]["unknown"]]})
    if snap["retention"]["quarantine_candidates"]:
        issues.append({"severity": "advisory", "code": "old_runtime_candidates", "count": len(snap["retention"]["quarantine_candidates"])})
    return {
        "schema": "codex_runtime_cache.doctor.v1",
        "ok": not any(item["severity"] == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": snap["summary"],
        "retention": snap["retention"],
    }


def repair_plan() -> dict[str, Any]:
    snap = snapshot()
    candidates = snap["retention"]["quarantine_candidates"]
    return {
        "schema": "codex_runtime_cache.repair_plan.v1",
        "ok": bool(snap["ok"]),
        "generated_at": now_iso(),
        "default_apply": False,
        "actions": [
            {
                "id": "quarantine_old_runtime",
                "source": row["path"],
                "size_mb": row["size_mb"],
                "kind": row["kind"],
                "reason": "older_than_retained_rollback_or_stale_install_residue",
            }
            for row in candidates
        ],
        "keep": {
            "current": [row["name"] for row in snap["retention"]["current"]],
            "newest_complete_previous": snap["retention"]["keep_previous"],
        },
        "apply_command": "python _bridge\\codex_runtime_cache_governance.py quarantine --apply --confirm QUARANTINE-OLD-RUNTIMES",
        "contract": {"deletes_files": False, "moves_only_to_quarantine": True, "requires_fresh_plan": True, "minimum_age_hours": MIN_AGE_HOURS},
    }


def quarantine(*, apply: bool, confirm: str) -> dict[str, Any]:
    plan = repair_plan()
    if not apply:
        return {**plan, "schema": "codex_runtime_cache.quarantine.v1", "applied": False}
    if confirm != "QUARANTINE-OLD-RUNTIMES":
        return {"schema": "codex_runtime_cache.quarantine.v1", "ok": False, "applied": False, "reason": "confirmation_required"}
    run_root = QUARANTINE_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    moved: list[dict[str, Any]] = []
    for action in plan["actions"]:
        source = Path(str(action["source"])).resolve()
        if source.parent != RUNTIME_ROOT.resolve() or runtime_kind(source.name) not in {"previous", "install_residue"}:
            return {"schema": "codex_runtime_cache.quarantine.v1", "ok": False, "applied": False, "reason": "candidate_boundary_changed", "source": str(source)}
        destination = run_root / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append({**action, "destination": str(destination)})
    manifest = {
        "schema": "codex_runtime_cache.quarantine_manifest.v1",
        "created_at": now_iso(),
        "restore": "Move each destination back to source after confirming the active runtime is unchanged.",
        "items": moved,
    }
    manifest_path = run_root / "manifest.json"
    if moved:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"schema": "codex_runtime_cache.quarantine.v1", "ok": True, "applied": True, "moved_count": len(moved), "manifest_path": str(manifest_path) if moved else "", "items": moved}


def validate() -> dict[str, Any]:
    snap = snapshot()
    checks = [
        {"name": "exactly_one_current_runtime", "ok": len(snap["retention"]["current"]) == 1},
        {"name": "current_runtime_nonempty", "ok": bool(snap["retention"]["current"] and snap["retention"]["current"][0]["file_count"] > 0)},
        {"name": "at_most_one_retained_previous", "ok": bool(snap["retention"]["keep_previous"]) or not any(row["kind"] == "previous" for row in snap["runtimes"])},
    ]
    return {"schema": "codex_runtime_cache.validate.v1", "ok": all(item["ok"] for item in checks), "generated_at": now_iso(), "checks": checks, "summary": snap["summary"]}


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Govern Codex cached runtimes without deleting them.")
    parser.add_argument("command", choices=("snapshot", "doctor", "repair-plan", "quarantine", "validate"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    payload = {"snapshot": snapshot, "doctor": doctor, "repair-plan": repair_plan, "validate": validate}.get(args.command, lambda: quarantine(apply=args.apply, confirm=args.confirm))()
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
