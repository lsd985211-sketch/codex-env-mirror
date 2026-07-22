#!/usr/bin/env python3
"""Govern and retire the obsolete resource-library scheduler bridge.

Ownership: legacy scheduler-bridge lifecycle only. Non-goals: scheduling email,
resource, or workflow jobs. State behavior: read-only by default; confirmed
apply archives the legacy module and updates its two active documentation rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .migration_ledger import append_event, create_operation
except ImportError:
    from migration_ledger import append_event, create_operation


RESOURCE_ROOT = Path.home() / "Desktop" / "Codex\u8d44\u6e90\u5e93"
DOC_ROOT = RESOURCE_ROOT / "\u6587\u6863"
BRIDGE_DIR = DOC_ROOT / "\u8c03\u5ea6\u6865"
README_PATH = RESOURCE_ROOT / "README.md"
TASK_TABLE = DOC_ROOT / "\u5b9a\u65f6\u6a21\u5757" / "\u4efb\u52a1\u603b\u8868.txt"
SCHEDULER_TASKS = DOC_ROOT / "\u5b9a\u65f6\u6a21\u5757" / "\u8fd0\u884c\u6001" / "\u7edf\u4e00\u8c03\u5ea6" / "maintenance_tasks.json"
ARCHIVE_DIR = DOC_ROOT / "\u7cfb\u7edf\u7ef4\u62a4" / "\u5f52\u6863" / "legacy-modules" / "scheduler-bridge-20260712"
LEGACY_TASK_NAMES = {"\u53cd\u9988\u89e6\u53d1\u8054\u52a8", "\u6761\u4ef6\u89e6\u53d1\u6267\u884c"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return ""
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def task_rows() -> list[list[str]]:
    if not TASK_TABLE.exists():
        return []
    return [line.split("\t") for line in TASK_TABLE.read_text(encoding="utf-8").splitlines() if line.strip()]


def runtime_consumers() -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for row in task_rows()[1:]:
        if row and row[0] in LEGACY_TASK_NAMES and len(row) > 5 and row[5] == "\u542f\u7528":
            consumers.append({"type": "task_table", "name": row[0], "path": str(TASK_TABLE)})
    if SCHEDULER_TASKS.exists():
        try:
            payload = json.loads(SCHEDULER_TASKS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        for task in payload.get("tasks", []) if isinstance(payload, dict) else []:
            text = json.dumps(task, ensure_ascii=False)
            if "\u8c03\u5ea6\u6865" in text or "scheduler_bridge" in text:
                consumers.append({"type": "maintenance_task", "name": task.get("id", ""), "path": str(SCHEDULER_TASKS)})
    return consumers


def snapshot() -> dict[str, Any]:
    runtime_files = [str(path) for path in BRIDGE_DIR.rglob("*") if path.is_file() and path.suffix.lower() in {".py", ".ps1", ".exe", ".bat"}] if BRIDGE_DIR.exists() else []
    consumers = runtime_consumers()
    return {"schema": "scheduler-bridge-governance.snapshot.v1", "ok": True,
            "generated_at": now_iso(), "source_path": str(BRIDGE_DIR),
            "archive_path": str(ARCHIVE_DIR), "source_exists": BRIDGE_DIR.exists(),
            "archive_exists": ARCHIVE_DIR.exists(), "runtime_files": runtime_files,
            "active_consumers": consumers,
            "retirement_ready": BRIDGE_DIR.exists() and not runtime_files and not [item for item in consumers if item["type"] == "maintenance_task"],
            "retired": (not BRIDGE_DIR.exists()) and ARCHIVE_DIR.exists()}


def repair_plan() -> dict[str, Any]:
    snap = snapshot()
    actions: list[dict[str, Any]] = []
    if snap["retired"]:
        actions.append({"action": "keep_retired", "apply": False, "path": str(ARCHIVE_DIR)})
    elif snap["retirement_ready"]:
        actions.extend([
            {"action": "record_migration_plan", "apply": False, "source": str(BRIDGE_DIR), "target": str(ARCHIVE_DIR)},
            {"action": "remove_legacy_task_templates", "apply": False, "names": sorted(LEGACY_TASK_NAMES)},
            {"action": "remove_active_readme_link", "apply": False, "path": str(README_PATH)},
            {"action": "archive_legacy_module", "apply": False, "source": str(BRIDGE_DIR), "target": str(ARCHIVE_DIR)},
        ])
    else:
        actions.append({"action": "blocked", "apply": False, "reason": "runtime implementation or active maintenance consumer still exists"})
    return {"schema": "scheduler-bridge-governance.repair_plan.v1", "ok": True,
            "dry_run": True, "actions": actions, "snapshot": snap}


def _update_active_docs() -> None:
    if README_PATH.exists():
        lines = README_PATH.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if "./\u6587\u6863/\u8c03\u5ea6\u6865/" not in line and not line.lstrip().startswith("- \u8c03\u5ea6\u6865\uff1a")]
        README_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if TASK_TABLE.exists():
        lines = TASK_TABLE.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if not line.split("\t", 1)[0] in LEGACY_TASK_NAMES]
        TASK_TABLE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def retire(*, apply: bool, confirm: str = "") -> dict[str, Any]:
    plan = repair_plan()
    if not apply:
        return plan
    if confirm != "RETIRE-SCHEDULER-BRIDGE":
        return {"ok": False, "blocked": True, "reason": "confirmation_required", "expected": "RETIRE-SCHEDULER-BRIDGE"}
    snap = plan["snapshot"]
    if snap["retired"]:
        return {"ok": True, "changed": False, "reason": "already_retired", "snapshot": snap}
    if not snap["retirement_ready"]:
        return {"ok": False, "blocked": True, "reason": "retirement_not_ready", "snapshot": snap}
    source_hash = tree_digest(BRIDGE_DIR)
    operation = create_operation(domain="scheduler_bridge", owner="scheduler_bridge_governance",
        source_path=str(BRIDGE_DIR), target_path=str(ARCHIVE_DIR), reason="formal retirement of unused scheduler bridge",
        source_sha256=source_hash, rollback_action=f"move {ARCHIVE_DIR} back to {BRIDGE_DIR} and restore README/task-table backups")
    migration_id = str(operation["migration_id"])
    append_event(migration_id, "planned", actor="scheduler_bridge_governance", detail="zero-runtime retirement plan recorded")
    ARCHIVE_DIR.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE_DIR.exists():
        return {"ok": False, "blocked": True, "reason": "archive_target_exists", "archive_path": str(ARCHIVE_DIR)}
    _update_active_docs()
    shutil.move(str(BRIDGE_DIR), str(ARCHIVE_DIR))
    target_hash = tree_digest(ARCHIVE_DIR)
    append_event(migration_id, "applied", actor="scheduler_bridge_governance", detail="legacy module moved from active area", source_sha256=source_hash, target_sha256=target_hash)
    verified = (not BRIDGE_DIR.exists()) and ARCHIVE_DIR.exists() and source_hash == target_hash and not runtime_consumers()
    append_event(migration_id, "verified" if verified else "verification_failed", actor="scheduler_bridge_governance", source_sha256=source_hash, target_sha256=target_hash)
    return {"schema": "scheduler-bridge-governance.retire.v1", "ok": verified,
            "changed": True, "migration_id": migration_id, "source_sha256": source_hash,
            "target_sha256": target_hash, "snapshot": snapshot()}


def validate() -> dict[str, Any]:
    snap = snapshot()
    checks = [
        {"name": "no_active_runtime_consumer", "ok": not snap["active_consumers"]},
        {"name": "single_location", "ok": not (snap["source_exists"] and snap["archive_exists"])},
        {"name": "state_is_explicit", "ok": snap["retired"] or snap["retirement_ready"]},
    ]
    return {"schema": "scheduler-bridge-governance.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks, "snapshot": snap}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legacy scheduler bridge governance")
    parser.add_argument("command", choices=["snapshot", "doctor", "repair-plan", "retire", "validate"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command == "snapshot": payload = snapshot()
    elif args.command == "repair-plan": payload = repair_plan()
    elif args.command == "retire": payload = retire(apply=args.apply, confirm=args.confirm)
    elif args.command == "validate": payload = validate()
    else:
        val = validate()
        payload = {"schema": "scheduler-bridge-governance.doctor.v1", "ok": val["ok"], "issues": [item for item in val["checks"] if not item["ok"]], "snapshot": val["snapshot"]}
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
