#!/usr/bin/env python3
"""Derived maintenance capability registry and bounded query surface.

Ownership: machine-readable discovery over the maintenance surface map.
Non-goals: owner business state, arbitrary command execution, scheduling, or
replacing owner validators and repair commands.
State behavior: read-only except explicit ``build --apply`` of a derived SQLite
index under ``_bridge/runtime``.
Caller context: Codex workflow facade, scheduler planning, and maintenance UX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounded_output import bounded_payload


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
MAP_PATH = BRIDGE / "docs" / "maintenance_surface_map.md"
INDEX_PATH = BRIDGE / "runtime" / "maintenance_capabilities.sqlite"
KNOWN_ACTIONS = (
    "snapshot",
    "doctor",
    "repair-plan",
    "validate",
    "metrics",
    "plan",
    "status",
    "progress",
    "inspect",
    "query",
    "state-query",
    "commands",
    "interfaces",
    "recommend",
    "task-drift",
    "override-plan",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capability_id(module_path: str) -> str:
    normalized = module_path.replace("\\", "/").strip().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


def infer_system(module_path: str, text: str) -> str:
    normalized_module = module_path.replace("\\", "/").lower()
    module_routes = (
        ("startup", ("codex_config_guard", "codex_session_store", "codex_runtime_cache", "codex_model_provider")),
        ("bridge", ("mobile_openclaw", "mobile_bridge", "weixin")),
        ("mail", ("email_", "/email", "mail_")),
        ("scheduler", ("scheduler", "schedule_")),
        ("resource", ("resource_", "/resource")),
        ("network", ("network_", "/network")),
        ("mcp", ("mcp_", "/mcp", "local_mcp_hub")),
        ("memory", ("memory_", "/memory", "pmb_")),
        ("skills", ("skill_", "/skill", "code_maintainability", "module_asset")),
        ("records", ("record_store", "codex_reporter", "migration_")),
        ("backup", ("backup_", "/backup", "codex_environment_mirror", "recovery_mirror")),
        ("office", ("office", "document", "pdf_")),
        ("workflow", ("workflow_", "/workflow", "closeout", "slash_")),
    )
    for system, terms in module_routes:
        if any(term in normalized_module for term in terms):
            return system
    haystack = f"{module_path} {text}".lower()
    routes = (
        ("workflow", ("workflow", "closeout", "slash")),
        ("resource", ("resource", "download", "package")),
        ("network", ("network", "gateway", "proxy")),
        ("mcp", ("mcp", "tool_registry", "tool-registry")),
        ("mail", ("email", "mail", "outbox", "inbox")),
        ("scheduler", ("scheduler", "schedule", "定时")),
        ("memory", ("memory", "pmb", "checkpoint")),
        ("skills", ("skill", "module_capability", "code_maintainability")),
        ("records", ("record_store", "record-store", "incident", "migration")),
        ("startup", ("startup", "config_guard", "runtime_cache", "model_provider", "session_store", "session-store", "restore-performance")),
        ("bridge", ("mobile_openclaw", "bridge", "weixin")),
        ("backup", ("backup", "encoding")),
        ("office", ("office", "document", "pdf")),
    )
    for system, terms in routes:
        if any(term in haystack for term in terms):
            return system
    return "general"


def extract_actions(text: str) -> list[str]:
    actions = []
    lowered = text.lower()
    for action in KNOWN_ACTIONS:
        if re.search(rf"(?<![a-z0-9-]){re.escape(action)}(?![a-z0-9-])", lowered):
            actions.append(action)
    return actions


def parse_surface_map() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = MAP_PATH.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] == "---":
            continue
        surface, owns, non_goals, usual_entry = cells[:4]
        module_paths = [
            token.replace("\\", "/")
            for token in re.findall(r"`([^`]+)`", surface)
            if token.lower().endswith(".py")
        ]
        for module_path in module_paths:
            normalized_module = module_path if module_path.startswith("_bridge/") else f"_bridge/{module_path}"
            script = (ROOT / normalized_module).resolve()
            try:
                script.relative_to(BRIDGE.resolve())
            except ValueError:
                continue
            actions = extract_actions(usual_entry)
            rows.append(
                {
                    "capability_id": capability_id(normalized_module),
                    "system": infer_system(normalized_module, f"{owns} {usual_entry}"),
                    "module_path": normalized_module,
                    "surface": re.sub(r"`", "", surface),
                    "owns": owns,
                    "non_goals": non_goals,
                    "usual_entry": usual_entry,
                    "actions": actions,
                    "read_only_actions": [action for action in actions if action != "repair-plan"],
                    "source_line": line_number,
                    "source_mtime_ns": MAP_PATH.stat().st_mtime_ns,
                    "script_exists": script.is_file(),
                }
            )
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = unique.get(row["capability_id"])
        if not current:
            unique[row["capability_id"]] = row
            continue
        current["actions"] = sorted(set(current["actions"]) | set(row["actions"]))
        current["read_only_actions"] = sorted(set(current["read_only_actions"]) | set(row["read_only_actions"]))
    return sorted(unique.values(), key=lambda item: (item["system"], item["module_path"]))


def build_index(*, apply: bool) -> dict[str, Any]:
    rows = parse_surface_map()
    result = {
        "schema": "maintenance_capability_registry.build.v1",
        "ok": bool(rows),
        "apply_requested": apply,
        "applied": False,
        "capability_count": len(rows),
        "index_path": str(INDEX_PATH),
        "source_path": str(MAP_PATH),
    }
    if not apply or not rows:
        return result
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    connection = sqlite3.connect(tmp)
    try:
        connection.execute(
            """CREATE TABLE capabilities (
                capability_id TEXT PRIMARY KEY,
                system TEXT NOT NULL,
                module_path TEXT NOT NULL,
                surface TEXT NOT NULL,
                owns TEXT NOT NULL,
                non_goals TEXT NOT NULL,
                usual_entry TEXT NOT NULL,
                actions_json TEXT NOT NULL,
                read_only_actions_json TEXT NOT NULL,
                source_line INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                script_exists INTEGER NOT NULL
            )"""
        )
        connection.execute("CREATE INDEX idx_capabilities_system ON capabilities(system)")
        connection.execute("CREATE INDEX idx_capabilities_module ON capabilities(module_path)")
        connection.executemany(
            "INSERT INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["capability_id"],
                    row["system"],
                    row["module_path"],
                    row["surface"],
                    row["owns"],
                    row["non_goals"],
                    row["usual_entry"],
                    json.dumps(row["actions"], ensure_ascii=False),
                    json.dumps(row["read_only_actions"], ensure_ascii=False),
                    row["source_line"],
                    row["source_mtime_ns"],
                    int(row["script_exists"]),
                )
                for row in rows
            ],
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (("schema", "maintenance_capability_registry.v1"), ("generated_at", now_iso()), ("source_mtime_ns", str(MAP_PATH.stat().st_mtime_ns))),
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(tmp, INDEX_PATH)
    return {**result, "applied": True}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "capability_id": row["capability_id"],
        "system": row["system"],
        "module_path": row["module_path"],
        "surface": row["surface"],
        "owns": row["owns"],
        "usual_entry": row["usual_entry"],
        "actions": json.loads(row["actions_json"]),
        "read_only_actions": json.loads(row["read_only_actions_json"]),
        "source_line": row["source_line"],
        "script_exists": bool(row["script_exists"]),
    }


def query_registry(*, system: str = "", term: str = "", action: str = "", limit: int = 20) -> dict[str, Any]:
    effective_limit = max(1, min(int(limit or 20), 100))
    if not INDEX_PATH.is_file():
        return {"schema": "maintenance_capability_registry.query.v1", "ok": False, "reason": "index_missing", "next_action": "build --apply"}
    clauses = []
    params: list[Any] = []
    if system:
        clauses.append("system = ?")
        params.append(system)
    if term:
        clauses.append("(module_path LIKE ? OR surface LIKE ? OR owns LIKE ? OR usual_entry LIKE ?)")
        needle = f"%{term}%"
        params.extend([needle, needle, needle, needle])
    if action:
        clauses.append("actions_json LIKE ?")
        params.append(f'%"{action}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = sqlite3.connect(INDEX_PATH)
    connection.row_factory = sqlite3.Row
    try:
        total = connection.execute(f"SELECT COUNT(*) FROM capabilities {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM capabilities {where} ORDER BY system, module_path LIMIT ?",
            [*params, effective_limit],
        ).fetchall()
    finally:
        connection.close()
    return bounded_payload(
        {
            "schema": "maintenance_capability_registry.query.v1",
            "ok": True,
            "filters": {"system": system, "term": term, "action": action},
            "total": total,
            "returned": len(rows),
            "has_more": total > len(rows),
            "limit": effective_limit,
            "items": [_row_to_dict(row) for row in rows],
        },
        max_bytes=8 * 1024,
        max_items=max(20, effective_limit),
        preserve_keys=("schema", "ok", "filters", "total", "returned", "has_more", "limit", "items"),
    )


def resolve_capability(capability: str, action: str) -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {"ok": False, "reason": "index_missing"}
    connection = sqlite3.connect(INDEX_PATH)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM capabilities WHERE capability_id = ?", (capability,)).fetchone()
    finally:
        connection.close()
    if not row:
        return {"ok": False, "reason": "capability_not_found", "capability_id": capability}
    item = _row_to_dict(row)
    if action not in item["actions"]:
        return {"ok": False, "reason": "action_not_declared", "capability_id": capability, "action": action, "declared_actions": item["actions"]}
    script = (ROOT / item["module_path"]).resolve()
    try:
        script.relative_to(BRIDGE.resolve())
    except ValueError:
        return {"ok": False, "reason": "capability_outside_bridge"}
    if not script.is_file():
        return {"ok": False, "reason": "capability_script_missing", "module_path": item["module_path"]}
    return {"ok": True, **item, "script": str(script), "action": action}


def metrics() -> dict[str, Any]:
    rows = parse_surface_map()
    systems: dict[str, int] = {}
    for row in rows:
        systems[row["system"]] = systems.get(row["system"], 0) + 1
    return {
        "schema": "maintenance_capability_registry.metrics.v1",
        "ok": bool(rows),
        "capability_count": len(rows),
        "system_count": len(systems),
        "systems": systems,
        "index_exists": INDEX_PATH.is_file(),
        "index_fresh": INDEX_PATH.is_file() and INDEX_PATH.stat().st_mtime_ns >= MAP_PATH.stat().st_mtime_ns,
    }


def doctor() -> dict[str, Any]:
    metric = metrics()
    issues = []
    if not metric["capability_count"]:
        issues.append("maintenance surface map produced no capabilities")
    if not metric["index_exists"]:
        issues.append("maintenance capability index missing")
    elif not metric["index_fresh"]:
        issues.append("maintenance capability index stale")
    return {"schema": "maintenance_capability_registry.doctor.v1", "ok": not issues, "issues": issues, "metrics": metric}


def print_json(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derived maintenance capability registry")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--apply", action="store_true")
    query = sub.add_parser("query")
    query.add_argument("--system", default="")
    query.add_argument("--term", default="")
    query.add_argument("--action", default="")
    query.add_argument("--limit", type=int, default=20)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--capability-id", required=True)
    resolve.add_argument("--action", required=True)
    for name in ("snapshot", "doctor", "validate", "metrics"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    if args.command == "build":
        payload = build_index(apply=args.apply)
    elif args.command == "query":
        payload = query_registry(system=args.system, term=args.term, action=args.action, limit=args.limit)
    elif args.command == "resolve":
        payload = resolve_capability(args.capability_id, args.action)
    elif args.command == "metrics":
        payload = metrics()
    elif args.command in {"doctor", "validate"}:
        payload = doctor()
    else:
        payload = {"schema": "maintenance_capability_registry.snapshot.v1", **metrics(), "source_path": str(MAP_PATH), "index_path": str(INDEX_PATH)}
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
