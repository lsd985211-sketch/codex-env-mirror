#!/usr/bin/env python3
"""CodeGraph maintenance checks for this workspace.

Most commands are read-only and intentionally block CodeGraph mutating commands
such as install, init, index, upgrade, unlock, or uninstall. The only bounded
write entry is ``ensure-fresh``, which may run ``codegraph sync`` when
``freshness`` proves pending changes or target-newer-than-index evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_CODEGRAPH_CMD = ROOT / "_bridge" / "tools" / "codegraph" / "node_modules" / ".bin" / "codegraph.cmd"
PROJECT_CODEGRAPH_MCP_WRAPPER = ROOT / "_bridge" / "mcp_profile_launcher.py"
PROJECT_CODEGRAPH_MCP_PROFILE = "cg"
DEFAULT_INDEXES = [
    ROOT / ".codegraph",
    Path(r"C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u\.codegraph"),
]
REASONIX_ROOT = Path(r"C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u")
REASONIX_TOML = REASONIX_ROOT / "reasonix.toml"
REASONIX_WORKSPACE_KNOWLEDGE = REASONIX_ROOT / ".reasonix" / "skills" / "workspace-knowledge" / "SKILL.md"
CODEGRAPH_MUTATING_COMMANDS = {
    "install",
    "uninstall",
    "init",
    "uninit",
    "index",
    "sync",
    "unlock",
    "upgrade",
}
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
GENERATED_NOISE_PREFIXES = (
    "web/public/assets/",
    "dist/",
    "build/",
)
RELEVANCE_SMOKES = (
    {
        "id": "local_pmb_file_anchor",
        "query": "local_pmb_memory",
        "expected_file": "_bridge/local_pmb_memory.py",
    },
    {
        "id": "pmb_prepare_symbol_anchor",
        "query": "pmb_prepare",
        "expected_file": "_bridge/local_pmb_memory.py",
    },
    {
        "id": "mcp_profile_launcher_file_anchor",
        "query": "_bridge/mcp_profile_launcher.py",
        "expected_file": "_bridge/mcp_profile_launcher.py",
    },
    {
        "id": "bridge_worker_active_recovery_anchor",
        "query": "worker_active_recovery",
        "expected_file": "_bridge/mobile_openclaw_bridge/worker_active_recovery.py",
    },
    {
        "id": "bridge_worker_dispatch_permission_anchor",
        "query": "worker_dispatch_permission",
        "expected_file": "_bridge/mobile_openclaw_bridge/worker_dispatch_permission.py",
    },
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_command_output(value: str, *, limit: int = 4000) -> str:
    text = ANSI_RE.sub("", str(value or "")).replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line.strip())
    return compact[-limit:]


def file_time_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def text_contains(path: Path, needle: str) -> bool | None:
    if not path.exists():
        return None
    try:
        return needle.lower() in path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return None


def text_claims_codegraph_enabled(path: Path) -> bool | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        lowered = line.lower()
        if "codegraph" not in lowered:
            continue
        if "未启用" in line or "not enabled" in lowered or "disabled" in lowered:
            continue
        if "enabled" in lowered or "启用" in line:
            return True
    return False


def path_mentioned_in_text(path: Path, text: str) -> bool:
    """Match Windows paths in TOML whether they are raw, escaped, or slash-normalized."""
    raw = str(path)
    variants = {
        raw,
        raw.replace("\\", "\\\\"),
        raw.replace("\\", "/"),
        raw.lower(),
        raw.replace("\\", "\\\\").lower(),
        raw.replace("\\", "/").lower(),
    }
    haystack = text.lower()
    return any(variant.lower() in haystack for variant in variants)


def run_command(cmd: list[str], timeout: int = 12, *, allow_mutating: bool = False) -> dict[str, Any]:
    blocked = [part for part in cmd[1:] if part in CODEGRAPH_MUTATING_COMMANDS]
    if cmd and Path(cmd[0]).name.lower().startswith("codegraph") and blocked and not allow_mutating:
        return {
            "ok": False,
            "blocked": True,
            "reason": "mutating_codegraph_command_blocked",
            "command": cmd,
        }
    resolved = shutil.which(cmd[0])
    if not resolved:
        return {"ok": False, "missing": True, "command": cmd}
    exec_cmd = [resolved, *cmd[1:]]
    try:
        proc = subprocess.run(
            exec_cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"ok": False, "missing": True, "command": cmd}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": cmd,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": cmd,
        "stdout": clean_command_output(proc.stdout),
        "stderr": clean_command_output(proc.stderr),
    }


def run_readonly_command(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    return run_command(cmd, timeout=timeout, allow_mutating=False)


def run_codegraph_sync(timeout: int = 30) -> dict[str, Any]:
    return run_command([*codegraph_command(), "sync", str(ROOT)], timeout=timeout, allow_mutating=True)


def codegraph_command() -> list[str]:
    return [str(PROJECT_CODEGRAPH_CMD)] if PROJECT_CODEGRAPH_CMD.exists() else ["codegraph"]


def codegraph_status() -> dict[str, Any]:
    command = [*codegraph_command(), "status", str(ROOT), "--json"]
    result = run_readonly_command(command, timeout=15)
    payload: dict[str, Any] | None = None
    if result.get("ok"):
        try:
            parsed = json.loads(str(result.get("stdout") or "{}"))
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError as exc:
            result["json_error"] = str(exc)
    return {"command": result, "payload": payload}


def codegraph_query(search: str, *, limit: int = 5) -> dict[str, Any]:
    result = run_readonly_command(
        [*codegraph_command(), "query", search, "--path", str(ROOT), "--limit", str(limit), "--json"],
        timeout=12,
    )
    payload: list[Any] | None = None
    if result.get("ok"):
        try:
            parsed = json.loads(str(result.get("stdout") or "[]"))
            if isinstance(parsed, list):
                payload = parsed
        except json.JSONDecodeError as exc:
            result["json_error"] = str(exc)
    return {"command": result, "payload": payload}


def parse_status_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_target(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def target_freshness(targets: list[str], indexed_at: datetime | None, db_mtime: datetime | None) -> list[dict[str, Any]]:
    threshold = indexed_at or db_mtime
    rows: list[dict[str, Any]] = []
    for value in targets:
        path = resolve_target(value)
        item: dict[str, Any] = {
            "target": value,
            "path": str(path),
            "exists": path.exists(),
        }
        if not path.exists():
            item["fresh"] = False
            item["reason"] = "target_missing"
            rows.append(item)
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError as exc:
            item["fresh"] = False
            item["reason"] = "target_stat_failed"
            item["error"] = str(exc)
            rows.append(item)
            continue
        item["mtime"] = mtime.isoformat()
        item["indexed_threshold"] = threshold.isoformat() if threshold else None
        if threshold is None:
            item["fresh"] = False
            item["reason"] = "index_time_unknown"
        elif mtime <= threshold:
            item["fresh"] = True
            item["reason"] = "target_not_newer_than_index"
        else:
            item["fresh"] = False
            item["reason"] = "target_newer_than_index"
        rows.append(item)
    return rows


def pending_total_from_status(status_payload: dict[str, Any] | None) -> int:
    pending = status_payload.get("pendingChanges") if isinstance(status_payload, dict) else {}
    if not isinstance(pending, dict):
        return 0
    total = 0
    for key in ("added", "modified", "removed"):
        try:
            total += int(pending.get(key) or 0)
        except Exception:
            total += 0
    return total


def pending_counts_from_status(status_payload: dict[str, Any] | None) -> dict[str, int]:
    pending = status_payload.get("pendingChanges") if isinstance(status_payload, dict) else {}
    if not isinstance(pending, dict):
        return {"added": 0, "modified": 0, "removed": 0, "total": 0}
    counts: dict[str, int] = {}
    for key in ("added", "modified", "removed"):
        try:
            counts[key] = int(pending.get(key) or 0)
        except Exception:
            counts[key] = 0
    counts["total"] = counts["added"] + counts["modified"] + counts["removed"]
    return counts


def freshness(targets: list[str] | None = None) -> dict[str, Any]:
    targets = targets or []
    status = codegraph_status()
    payload = status.get("payload") if isinstance(status.get("payload"), dict) else None
    index = inspect_index(ROOT / ".codegraph")
    indexed_at = parse_status_time(payload.get("lastIndexed") if payload else None)
    db_mtime = parse_status_time(index.get("db_mtime"))
    target_rows = target_freshness(targets, indexed_at, db_mtime) if targets else []
    pending_counts = pending_counts_from_status(payload)
    pending_total = pending_counts["total"]
    stale_targets = [item for item in target_rows if not item.get("fresh")]
    global_pending_requires_sync = pending_total > 0 and not targets
    target_pending_requires_sync = bool(stale_targets)
    sync_needed = bool(global_pending_requires_sync or target_pending_requires_sync)
    return {
        "schema": "codegraph_health.freshness.v1",
        "generated_at": now_iso(),
        "ok": status.get("command", {}).get("ok") is True,
        "read_only": True,
        "workspace": str(ROOT),
        "status_ok": status.get("command", {}).get("ok") is True,
        "pending_total": pending_total,
        "pending_counts": pending_counts,
        "pending_changes": (payload or {}).get("pendingChanges") if payload else None,
        "last_indexed": (payload or {}).get("lastIndexed") if payload else None,
        "db_mtime": index.get("db_mtime"),
        "targets": target_rows,
        "sync_needed": sync_needed,
        "decision": "sync_recommended" if sync_needed else "fresh_enough",
        "rule": "Run before CodeGraph exploration when target freshness matters. With explicit targets, unrelated added pending files do not force repeated sync once the targets are fresh.",
    }


def ensure_fresh(targets: list[str] | None = None, *, timeout: int = 30) -> dict[str, Any]:
    before = freshness(targets)
    if not before.get("sync_needed"):
        return {
            "schema": "codegraph_health.ensure_fresh.v1",
            "generated_at": now_iso(),
            "ok": bool(before.get("ok")),
            "changed": False,
            "phase": "freshness_check",
            "before": before,
            "after": before,
            "rule": "No sync was run because the index was fresh enough for the requested targets.",
        }
    sync = run_codegraph_sync(timeout=timeout)
    after = freshness(targets)
    return {
        "schema": "codegraph_health.ensure_fresh.v1",
        "generated_at": now_iso(),
        "ok": bool(sync.get("ok")) and bool(after.get("ok")),
        "changed": True,
        "phase": "sync",
        "sync": sync,
        "before": before,
        "after": after,
        "residual_sync_needed": bool(after.get("sync_needed")),
        "rule": "Bounded CodeGraph sync only runs when pending changes or target-newer-than-index evidence exists.",
    }


def relevance_smokes() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in RELEVANCE_SMOKES:
        query = str(item["query"])
        expected_file = str(item["expected_file"]).replace("\\", "/")
        result = codegraph_query(query, limit=5)
        rows = result.get("payload") if isinstance(result.get("payload"), list) else []
        seen_files: list[str] = []
        top_file: str | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            node = row.get("node")
            if not isinstance(node, dict):
                continue
            file_path = str(node.get("filePath") or "").replace("\\", "/")
            if file_path and file_path not in seen_files:
                seen_files.append(file_path)
            if top_file is None and file_path:
                top_file = file_path
        checks.append(
            {
                "id": item["id"],
                "query": query,
                "expected_file": expected_file,
                "ok": result.get("command", {}).get("ok") is True and expected_file in seen_files,
                "top_file": top_file,
                "seen_files": seen_files[:5],
                "command_ok": result.get("command", {}).get("ok"),
                "command_error": result.get("command", {}).get("stderr") or result.get("command", {}).get("json_error"),
            }
        )
    return checks


@dataclass
class Issue:
    severity: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


def inspect_index(index_dir: Path) -> dict[str, Any]:
    db_path = index_dir / "codegraph.db"
    out: dict[str, Any] = {
        "path": str(index_dir),
        "exists": index_dir.exists(),
        "db_exists": db_path.exists(),
    }
    if index_dir.exists():
        try:
            out["items"] = sorted(p.name for p in index_dir.iterdir())
        except OSError as exc:
            out["items_error"] = str(exc)
    if not db_path.exists():
        return out

    out["db_size"] = db_path.stat().st_size
    out["db_mtime"] = file_time_iso(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        out["journal_mode"] = conn.execute("pragma journal_mode").fetchone()[0]
        out["integrity_check"] = conn.execute("pragma integrity_check").fetchone()[0]
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        }
        out["tables"] = sorted(tables)
        counts: dict[str, int] = {}
        for table in ["files", "nodes", "edges", "unresolved_refs"]:
            if table in tables:
                counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
        out["counts"] = counts
        if "files" in tables:
            out["languages"] = [
                dict(row)
                for row in conn.execute(
                    "select language, count(*) as files, coalesce(sum(node_count), 0) as nodes "
                    "from files group by language order by files desc"
                )
            ]
            out["file_errors"] = int(
                conn.execute(
                    "select count(*) from files where errors is not null and errors<>''"
                ).fetchone()[0]
            )
            total_nodes = int(counts.get("nodes", 0))
            noise_rows: list[dict[str, Any]] = []
            for prefix in GENERATED_NOISE_PREFIXES:
                row = conn.execute(
                    "select count(*) as files, coalesce(sum(node_count), 0) as nodes, coalesce(sum(size), 0) as bytes "
                    "from files where replace(path, '\\\\', '/') like ?",
                    (f"{prefix}%",),
                ).fetchone()
                noise_rows.append(
                    {
                        "prefix": prefix,
                        "files": int(row["files"]),
                        "nodes": int(row["nodes"] or 0),
                        "bytes": int(row["bytes"] or 0),
                    }
                )
            noise_nodes = sum(int(row["nodes"]) for row in noise_rows)
            out["generated_noise"] = {
                "prefixes": noise_rows,
                "nodes": noise_nodes,
                "node_ratio": round(noise_nodes / max(total_nodes, 1), 3),
            }
        if "project_metadata" in tables:
            out["metadata"] = [
                dict(row) for row in conn.execute("select * from project_metadata order by key")
            ]
        nodes = counts.get("nodes", 0)
        unresolved = counts.get("unresolved_refs", 0)
        out["unresolved_per_node"] = round(unresolved / max(nodes, 1), 3)
        conn.close()
    except Exception as exc:  # noqa: BLE001 - diagnostic output should not crash
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def detect_installed_version() -> dict[str, Any]:
    exe = shutil.which("codegraph")
    local_exe = PROJECT_CODEGRAPH_CMD if PROJECT_CODEGRAPH_CMD.exists() else None
    effective = exe or (str(local_exe) if local_exe else None)
    result: dict[str, Any] = {
        "path": exe,
        "project_local_path": str(local_exe) if local_exe else None,
        "available": bool(effective),
        "available_via": "path" if exe else ("project_local" if local_exe else None),
    }
    if exe:
        result["version_command"] = run_readonly_command(["codegraph", "version"], timeout=10)
    elif local_exe:
        result["version_command"] = run_readonly_command([str(local_exe), "version"], timeout=10)
    npm_prefix = run_readonly_command(["npm", "prefix", "-g"], timeout=8)
    result["npm_prefix"] = npm_prefix
    npm_list = run_readonly_command(["npm", "list", "-g", "@colbymchenry/codegraph", "--depth=0"], timeout=12)
    result["npm_global_list"] = npm_list
    return result


def config_snapshot() -> dict[str, Any]:
    codex_config = Path.home() / ".codex" / "config.toml"
    config_text = ""
    if codex_config.exists():
        try:
            config_text = codex_config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            config_text = ""
    return {
        "codex_config": {
            "path": str(codex_config),
            "exists": codex_config.exists(),
            "mentions_codegraph": text_contains(codex_config, "codegraph"),
            "mentions_project_local_codegraph": path_mentioned_in_text(PROJECT_CODEGRAPH_CMD, config_text),
            "mentions_project_codegraph_wrapper": path_mentioned_in_text(PROJECT_CODEGRAPH_MCP_WRAPPER, config_text),
        },
        "reasonix_toml": {
            "path": str(REASONIX_TOML),
            "exists": REASONIX_TOML.exists(),
            "mentions_codegraph": text_contains(REASONIX_TOML, "codegraph"),
            "mtime": file_time_iso(REASONIX_TOML) if REASONIX_TOML.exists() else None,
        },
        "reasonix_workspace_knowledge": {
            "path": str(REASONIX_WORKSPACE_KNOWLEDGE),
            "exists": REASONIX_WORKSPACE_KNOWLEDGE.exists(),
            "mentions_codegraph": text_contains(REASONIX_WORKSPACE_KNOWLEDGE, "codegraph"),
            "claims_codegraph_enabled": text_claims_codegraph_enabled(REASONIX_WORKSPACE_KNOWLEDGE),
            "mtime": file_time_iso(REASONIX_WORKSPACE_KNOWLEDGE)
            if REASONIX_WORKSPACE_KNOWLEDGE.exists()
            else None,
        },
    }


def snapshot(indexes: list[Path]) -> dict[str, Any]:
    return {
        "schema": "codegraph_health.snapshot.v1",
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "path_has_codegraph": bool(shutil.which("codegraph")),
            "project_local_codegraph": PROJECT_CODEGRAPH_CMD.exists(),
        },
        "installed": detect_installed_version(),
        "codegraph_status": codegraph_status(),
        "config": config_snapshot(),
        "relevance_smokes": relevance_smokes(),
        "indexes": [inspect_index(path) for path in indexes],
        "safety": {
            "read_only": True,
            "blocked_mutating_commands": sorted(CODEGRAPH_MUTATING_COMMANDS),
            "bounded_mutating_entrypoints": ["ensure-fresh"],
        },
    }


def diagnose(snap: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    if not snap["installed"].get("available"):
        issues.append(
            Issue(
                "risk",
                "codegraph_cli_unavailable",
                "CodeGraph CLI is not visible on PATH and no project-local CLI was found.",
            )
        )
    codex_cfg = snap["config"]["codex_config"]
    if not codex_cfg.get("mentions_codegraph"):
        issues.append(
            Issue(
                "advisory",
                "codex_mcp_not_configured",
                "Codex config does not mention CodeGraph MCP; current Codex tool surface will not expose it.",
            )
        )
    elif (
        not codex_cfg.get("mentions_project_local_codegraph")
        and not codex_cfg.get("mentions_project_codegraph_wrapper")
        and not shutil.which("codegraph")
    ):
        issues.append(
            Issue(
                "risk",
                "codex_mcp_command_not_resolvable",
                "Codex config mentions CodeGraph but does not point at the project-local CLI, and codegraph is not on PATH.",
            )
        )
    reasonix_toml = snap["config"]["reasonix_toml"]
    reasonix_knowledge = snap["config"]["reasonix_workspace_knowledge"]
    if reasonix_knowledge.get("claims_codegraph_enabled") and not reasonix_toml.get("mentions_codegraph"):
        issues.append(
            Issue(
                "risk",
                "reasonix_knowledge_config_drift",
                "Reasonix workspace knowledge says CodeGraph is enabled, but reasonix.toml does not mention it.",
            )
        )

    any_index = False
    current_workspace_index = False
    status_payload = ((snap.get("codegraph_status") or {}).get("payload") or {})
    status_index = status_payload.get("index") if isinstance(status_payload, dict) else {}
    reindex_recommended = isinstance(status_index, dict) and bool(status_index.get("reindexRecommended"))
    index_metadata_complete = isinstance(status_index, dict) and (
        status_index.get("builtWithVersion") is not None
        and status_index.get("builtWithExtractionVersion") is not None
    )
    if reindex_recommended:
        issues.append(
            Issue(
                "advisory",
                "codegraph_reindex_recommended",
                "CodeGraph status recommends reindexing the current workspace; keep MCP usable with sync/preflight, but schedule a controlled reindex if this persists.",
            )
        )
    if isinstance(status_index, dict) and not index_metadata_complete:
        issues.append(
            Issue(
                "advisory",
                "codegraph_index_metadata_incomplete",
                "Current workspace CodeGraph index is queryable but lacks build metadata, likely from an interrupted earlier init/index run.",
            )
        )
    pending = status_payload.get("pendingChanges") if isinstance(status_payload, dict) else {}
    if isinstance(pending, dict):
        pending_added = int(pending.get("added") or 0)
        pending_modified = int(pending.get("modified") or 0)
        pending_removed = int(pending.get("removed") or 0)
        pending_total = pending_added + pending_modified + pending_removed
        if pending_total > 0:
            if (
                pending_added > 0
                and pending_modified == 0
                and pending_removed == 0
                and index_metadata_complete
                and not reindex_recommended
            ):
                issues.append(
                    Issue(
                        "advisory",
                        "codegraph_pending_zero_node_additions",
                        f"Current workspace CodeGraph status reports {pending_added} added pending file(s), but full index metadata is current and relevance smokes pass; treat as non-blocking scanner drift unless modified/removed pending appears.",
                    )
                )
                return_pending_reindex = False
            else:
                return_pending_reindex = True
            if return_pending_reindex:
                issues.append(
                    Issue(
                        "advisory",
                        "codegraph_pending_changes",
                        f"Current workspace CodeGraph index has {pending_total} pending change(s); MCP launch guard should sync before serve.",
                    )
                )
    for idx in snap["indexes"]:
        if idx.get("db_exists"):
            any_index = True
            if Path(idx["path"]).resolve() == (ROOT / ".codegraph").resolve():
                current_workspace_index = True
            if idx.get("integrity_check") != "ok":
                issues.append(
                    Issue(
                        "blocker",
                        "codegraph_db_integrity_failed",
                        f"CodeGraph DB integrity check failed at {idx['path']}.",
                    )
                )
            ratio = idx.get("unresolved_per_node")
            if isinstance(ratio, (int, float)) and ratio > 2.0:
                issues.append(
                    Issue(
                        "advisory",
                        "high_unresolved_reference_ratio",
                        f"Index at {idx['path']} has unresolved_refs/nodes={ratio}.",
                    )
                )
            noise = idx.get("generated_noise") or {}
            noise_ratio = noise.get("node_ratio")
            if Path(idx["path"]).resolve() == (ROOT / ".codegraph").resolve() and isinstance(noise_ratio, (int, float)) and noise_ratio > 0.25:
                issues.append(
                    Issue(
                        "advisory",
                        "codegraph_generated_noise_high",
                        f"Current workspace index has generated/noisy paths contributing {noise_ratio:.1%} of nodes; tighten codegraph.json excludes and reindex.",
                    )
                )
    smoke_failures = [
        item for item in (snap.get("relevance_smokes") or []) if isinstance(item, dict) and not item.get("ok")
    ]
    if smoke_failures:
        issues.append(
            Issue(
                "advisory",
                "codegraph_query_relevance_degraded",
                "One or more CodeGraph relevance smokes did not return the expected anchored file; check query anchoring, stale index, generated-path noise, or bridge module coverage.",
            )
        )
    if not any_index:
        issues.append(
            Issue("risk", "no_codegraph_index_found", "No readable .codegraph/codegraph.db index was found.")
        )
    if any_index and not current_workspace_index:
        issues.append(
            Issue(
                "advisory",
                "workspace_index_absent",
                "The current mcsmanager workspace has no .codegraph index; only external project indexes were found.",
            )
        )
    return issues


def doctor(indexes: list[Path]) -> dict[str, Any]:
    snap = snapshot(indexes)
    issues = [issue.as_dict() for issue in diagnose(snap)]
    severities = {item["severity"] for item in issues}
    if "blocker" in severities:
        status = "unhealthy"
    elif "risk" in severities:
        status = "degraded"
    else:
        status = "ok"
    return {
        "schema": "codegraph_health.doctor.v1",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "summary": {
            "index_count": sum(1 for item in snap["indexes"] if item.get("db_exists")),
            "cli_available": snap["installed"].get("available", False),
            "read_only": True,
        },
        "snapshot": snap,
    }


def repair_plan(indexes: list[Path]) -> dict[str, Any]:
    doc = doctor(indexes)
    actions: list[dict[str, Any]] = []
    issue_codes = {issue["code"] for issue in doc["issues"]}
    if "codegraph_cli_unavailable" in issue_codes:
        actions.append(
            {
                "id": "install_or_expose_codegraph_cli",
                "mode": "manual_or_approved",
                "mutates": True,
                "default_execution": "dry_run_only",
                "description": "After approval, install or expose @colbymchenry/codegraph so codegraph is on PATH.",
                "guardrails": ["backup_config_before_change", "do_not_remove_existing_plugins"],
            }
        )
    if "codex_mcp_not_configured" in issue_codes:
        actions.append(
            {
                "id": "consider_codex_mcp_registration",
                "mode": "manual_or_approved",
                "mutates": True,
                "default_execution": "dry_run_only",
                "description": "After approval, add CodeGraph MCP to Codex config without changing other MCP entries.",
                "guardrails": ["append_or_merge_only", "preserve_existing_mcp_servers"],
            }
        )
    if "workspace_index_absent" in issue_codes:
        actions.append(
            {
                "id": "consider_current_workspace_index",
                "mode": "manual_or_approved",
                "mutates": True,
                "default_execution": "dry_run_only",
                "description": "After approval, initialize/index the current workspace if CodeGraph should cover mcsmanager.",
                "guardrails": ["do_not_touch_external_3c3u_index", "validate_status_after_index"],
            }
        )
    if {
        "codegraph_reindex_recommended",
        "codegraph_index_metadata_incomplete",
        "codegraph_pending_changes",
        "codegraph_generated_noise_high",
    } & issue_codes:
        actions.append(
            {
                "id": "controlled_current_workspace_reindex",
                "mode": "approved",
                "mutates": True,
                "default_execution": "operator_runs_codegraph_index_after_backup",
                "description": "After approval, run the project-local CodeGraph index command for the current workspace so pending changes, metadata, and ignore-boundary changes converge.",
                "guardrails": [
                    "backup_codegraph_json_before_change",
                    "do_not_touch_external_3c3u_index",
                    "validate_db_integrity_after_index",
                    "run_relevance_smokes_after_index",
                ],
            }
        )
    if "reasonix_knowledge_config_drift" in issue_codes:
        actions.append(
            {
                "id": "reconcile_reasonix_codegraph_knowledge",
                "mode": "manual_or_approved",
                "mutates": True,
                "default_execution": "dry_run_only",
                "description": "After approval, update Reasonix knowledge or config so documented state matches real state.",
                "guardrails": ["prefer_current_config_as_source_of_truth", "backup_before_edit"],
            }
        )
    return {
        "schema": "codegraph_health.repair_plan.v1",
        "generated_at": now_iso(),
        "dry_run": True,
        "status": doc["status"],
        "actions": actions,
        "blocked_actions": sorted(CODEGRAPH_MUTATING_COMMANDS),
        "doctor_issues": doc["issues"],
    }


def validate(indexes: list[Path]) -> dict[str, Any]:
    snap = snapshot(indexes)
    fresh = freshness([])
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "script_read_only_guard",
            "ok": True,
            "detail": "mutating CodeGraph commands are blocked except bounded ensure-fresh",
        }
    )
    checks.append(
        {
            "name": "freshness_entrypoint_available",
            "ok": bool(fresh.get("ok")),
            "detail": {
                "pending_total": fresh.get("pending_total"),
                "sync_needed": fresh.get("sync_needed"),
                "decision": fresh.get("decision"),
            },
        }
    )
    for idx in snap["indexes"]:
        if not idx.get("db_exists"):
            checks.append(
                {
                    "name": f"index_exists:{idx['path']}",
                    "ok": True,
                    "severity": "advisory",
                    "detail": "missing db; reported by doctor, not a read-only validation failure",
                }
            )
            continue
        checks.append(
            {
                "name": f"db_integrity:{idx['path']}",
                "ok": idx.get("integrity_check") == "ok",
                "detail": idx.get("integrity_check") or idx.get("error"),
            }
        )
        checks.append(
            {
                "name": f"db_wal:{idx['path']}",
                "ok": idx.get("journal_mode") == "wal",
                "detail": idx.get("journal_mode"),
            }
        )
    for smoke in snap.get("relevance_smokes") or []:
        if not isinstance(smoke, dict):
            continue
        checks.append(
            {
                "name": f"query_relevance:{smoke.get('id')}",
                "ok": bool(smoke.get("ok")),
                "detail": {
                    "query": smoke.get("query"),
                    "expected_file": smoke.get("expected_file"),
                    "top_file": smoke.get("top_file"),
                    "seen_files": smoke.get("seen_files"),
                },
            }
        )
    failed = [check for check in checks if not check["ok"]]
    return {
        "schema": "codegraph_health.validate.v1",
        "generated_at": now_iso(),
        "ok": not failed,
        "checks": checks,
    }


def metrics(indexes: list[Path]) -> dict[str, Any]:
    snap = snapshot(indexes)
    rows: list[dict[str, Any]] = []
    for idx in snap["indexes"]:
        counts = idx.get("counts") or {}
        rows.append(
            {
                "path": idx["path"],
                "db_exists": idx.get("db_exists", False),
                "files": counts.get("files", 0),
                "nodes": counts.get("nodes", 0),
                "edges": counts.get("edges", 0),
                "unresolved_refs": counts.get("unresolved_refs", 0),
                "unresolved_per_node": idx.get("unresolved_per_node"),
                "generated_noise": idx.get("generated_noise"),
                "file_errors": idx.get("file_errors"),
                "db_mtime": idx.get("db_mtime"),
            }
        )
    return {
        "schema": "codegraph_health.metrics.v1",
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "indexes": rows,
    }


def parse_indexes(values: list[str] | None) -> list[Path]:
    if not values:
        return DEFAULT_INDEXES
    return [Path(value).expanduser() for value in values]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only CodeGraph health tool")
    parser.add_argument(
        "command",
        choices=["snapshot", "doctor", "repair-plan", "validate", "metrics", "freshness", "ensure-fresh"],
        help="maintenance command to run",
    )
    parser.add_argument("--index", action="append", help="extra or replacement .codegraph directory")
    parser.add_argument("--target", action="append", default=[], help="target file or directory to check against index freshness")
    parser.add_argument("--timeout", type=int, default=30, help="bounded sync timeout for ensure-fresh")
    parser.add_argument("--json", action="store_true", help="emit JSON; currently always true")
    args = parser.parse_args(argv)

    indexes = parse_indexes(args.index)
    if args.command == "snapshot":
        payload = snapshot(indexes)
    elif args.command == "doctor":
        payload = doctor(indexes)
    elif args.command == "repair-plan":
        payload = repair_plan(indexes)
    elif args.command == "validate":
        payload = validate(indexes)
    elif args.command == "metrics":
        payload = metrics(indexes)
    elif args.command == "freshness":
        payload = freshness(args.target)
    elif args.command == "ensure-fresh":
        payload = ensure_fresh(args.target, timeout=args.timeout)
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
