#!/usr/bin/env python3
"""Read-only governance for draft artifacts.

Ownership: draft artifact metadata and draft index consistency.
Non-goals: create approval queues, apply proposals, mutate drafts, or own active
incidents and follow-up tasks.
State behavior: read-only snapshot and validation of `_bridge/shared/drafts`.
Caller context: workflow maintenance and closeout regression verification.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from _bridge.shared.json_cli import now_iso
except ModuleNotFoundError:
    from shared.json_cli import now_iso


BRIDGE = Path(__file__).resolve().parent
DRAFTS = BRIDGE / "shared" / "drafts"
INDEX = DRAFTS / "INDEX.md"
CONTENT_MATURITY = {"draft", "final"}
WORKFLOW_STATUS = {"retained_reference", "pending_review", "approved", "in_progress", "resolved"}
FIELD_PATTERN = re.compile(r"^(Content maturity|Workflow status|Pending action):\s*(.+?)\s*$", re.IGNORECASE)


def parse_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines()[:40]:
        match = FIELD_PATTERN.match(line.strip())
        if not match:
            continue
        key = match.group(1).lower().replace(" ", "_")
        metadata[key] = match.group(2).strip()
    return metadata


def index_rows() -> list[dict[str, str]]:
    if not INDEX.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---") or "| ID |" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != 6:
            continue
        rows.append(dict(zip(("id", "title", "content_maturity", "workflow_status", "pending_action", "updated"), parts)))
    return rows


def snapshot() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if DRAFTS.exists():
        for path in sorted(DRAFTS.glob("*.md")):
            if path.name in {"README.md", "INDEX.md"}:
                continue
            items.append({"id": path.stem, "path": str(path), **parse_metadata(path)})
    return {
        "schema": "draft_governance.snapshot.v1",
        "ok": DRAFTS.exists() and INDEX.exists(),
        "generated_at": now_iso(),
        "draft_root": str(DRAFTS),
        "artifact_count": len(items),
        "items": items,
        "index_rows": index_rows(),
        "contract": "draft storage is not a queue; closeout requires an explicit Review Queue item with artifact_ref",
    }


def validate() -> dict[str, Any]:
    payload = snapshot()
    issues: list[dict[str, str]] = []
    item_map = {str(item.get("id") or ""): item for item in payload["items"]}
    row_map = {str(item.get("id") or ""): item for item in payload["index_rows"]}
    for item_id, item in item_map.items():
        maturity = str(item.get("content_maturity") or "")
        status = str(item.get("workflow_status") or "")
        action = str(item.get("pending_action") or "")
        if maturity not in CONTENT_MATURITY:
            issues.append({"id": item_id, "code": "invalid_content_maturity", "value": maturity})
        if status not in WORKFLOW_STATUS:
            issues.append({"id": item_id, "code": "invalid_workflow_status", "value": status})
        if not action:
            issues.append({"id": item_id, "code": "missing_pending_action", "value": ""})
        if status == "retained_reference" and action.lower() != "none":
            issues.append({"id": item_id, "code": "retained_reference_has_pending_action", "value": action})
        if status == "pending_review" and action.lower() == "none":
            issues.append({"id": item_id, "code": "pending_review_missing_action", "value": action})
    for missing in sorted(set(item_map) - set(row_map)):
        issues.append({"id": missing, "code": "missing_index_row", "value": ""})
    for stale in sorted(set(row_map) - set(item_map)):
        issues.append({"id": stale, "code": "index_row_without_artifact", "value": ""})
    for item_id in sorted(set(item_map) & set(row_map)):
        for field in ("content_maturity", "workflow_status", "pending_action"):
            if str(item_map[item_id].get(field) or "") != str(row_map[item_id].get(field) or ""):
                issues.append({"id": item_id, "code": f"index_{field}_mismatch", "value": str(row_map[item_id].get(field) or "")})
    return {
        "schema": "draft_governance.validate.v1",
        "ok": bool(payload.get("ok")) and not issues,
        "generated_at": now_iso(),
        "artifact_count": payload["artifact_count"],
        "issues": issues,
        "checks": {
            "draft_root_exists": DRAFTS.exists(),
            "index_exists": INDEX.exists(),
            "metadata_complete": not any(item["code"].startswith(("invalid_", "missing_", "retained_", "pending_")) for item in issues),
            "index_consistent": not any("index" in item["code"] for item in issues),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft artifact governance")
    parser.add_argument("command", choices=("snapshot", "validate"))
    args = parser.parse_args()
    payload = snapshot() if args.command == "snapshot" else validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
