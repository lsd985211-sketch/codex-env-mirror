#!/usr/bin/env python3
"""Lifecycle governance for Codex skills.

Ownership: audit active skill assets and produce bounded lifecycle repair plans.
Non-goals: task routing, editing skill bodies, installing catalog skills, or
writing the MySkills database directly.
State behavior: source-read-only by default; refreshes update only the derived
SQLite lifecycle index. Approved filesystem actions require ``apply-approved
--confirm-apply`` and move assets into the resource backup library instead of
deleting them.
Caller context: thin facade calls from ``skill_orchestrator.py`` and direct
maintenance use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from _bridge import skill_active_catalog, skill_lifecycle_state
except ImportError:  # Direct script execution keeps only _bridge on sys.path.
    import skill_active_catalog  # type: ignore[no-redef]
    import skill_lifecycle_state  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
GLOBAL_SKILLS = Path.home() / ".codex" / "skills"
SYSTEM_SKILLS = GLOBAL_SKILLS / ".system"
PLUGIN_CACHE = Path.home() / ".codex" / "plugins" / "cache"
WORKSPACE_SKILLS = ROOT / ".codex" / "skills"
RESOURCE_BACKUP_ROOT = Path.home() / "Desktop" / "Codex资源库" / "_backup" / "skills"
MYSKILLS_DB = Path.home() / "AppData" / "Roaming" / "com.kanbenzhi.myskills" / "myskills.db"
USAGE_LOG = BRIDGE / "runtime" / "skill_orchestrator" / "skill_usage.jsonl"
ADMISSION_REGISTRY = BRIDGE / "shared" / "skill-system" / "registry.json"
ARCHIVE_NAMES = {"_backups", ".backups", "backups", "backup", "archive", "archived"}
IGNORED_TOP_LEVEL = {".system", ".disabled", *ARCHIVE_NAMES}
SCRIPT_REFERENCE_RE = re.compile(
    r"(?<![\w.-])((?:(?:\$\{?[A-Z_]+\}?|\.?\.?|[A-Za-z0-9_.-]+)/)*scripts/[A-Za-z0-9_./-]+)"
)
NAME_RE = re.compile(r"(?m)^name:\s*[\"']?([^\n\"']+)")
VALID_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BACKUP_FILE_RE = re.compile(r"(?:\.bak(?:-|$)|\.backup(?:-|$)|~$)", re.IGNORECASE)
RECOMMENDED_SKILL_LINE_LIMIT = 500
RECORD_SCHEMA_VERSION = 2
TRANSIENT_TREE_PARTS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
DEFAULT_METADATA_BUDGET_CHARS = 7_500
DEFAULT_IMPLICIT_SKILL_NAMES = (
    "global-framework",
    "diagnose",
    "windows-codex-ops",
    "codegraph-ops",
    "mcp-builder",
    "mobile-weixin-bridge-ops",
    "memory-systems",
    "codex-cli",
    "github-ops",
    "gui-automation",
    "email-ops",
    "mcsmanager-fabric-mc",
    "workspace-knowledge",
    "find-docs",
    "office-craft",
    "pdf",
    "playwright",
    "security-best-practices",
    "openai-docs",
    "skill-creator",
)
SOURCE_PRIORITY = {"user": 0, "system": 1, "plugin": 2}

SCENARIO_ALIASES: dict[str, str] = {
    "内容创作": "写作、编辑与内容策划",
    "写作与内容生产": "写作、编辑与内容策划",
    "媒体视觉": "视觉设计与图像表达",
    "视觉与图像输出": "视觉设计与图像表达",
    "开发工程": "软件开发与工程协作",
    "开发实现与故障诊断": "软件开发与工程协作",
    "维护生产力": "记忆、复盘与长期规划",
    "记忆、计划与协作治理": "记忆、复盘与长期规划",
    "网页研究": "信息检索与知识采集",
    "知识整理与笔记工作流": "文档、表格与出版物处理",
    "文档办公": "文档、表格与出版物处理",
    "windows-图形": "桌面、浏览器与设备自动化",
    "浏览器、桌面与平台操作": "桌面、浏览器与设备自动化",
    "minecraft": "游戏、模组与世界构建",
    "minecraft-专项生态": "游戏、模组与世界构建",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_name(path: Path, text: str = "") -> str:
    body = text or path.read_text(encoding="utf-8", errors="replace")
    match = NAME_RE.search(body[:4000])
    return (match.group(1).strip() if match else path.parent.name).strip()


def discovered_skill_files() -> list[tuple[Path, str]]:
    return skill_active_catalog.discover_active_skill_files(
        global_skills=GLOBAL_SKILLS,
        system_skills=SYSTEM_SKILLS,
        plugin_cache=PLUGIN_CACHE,
    )


def records_for_source(source: str) -> list[dict[str, Any]]:
    refresh = refresh_incremental()
    return [row for row in refresh["records"] if row.get("source") == source]


def active_local_skills() -> list[dict[str, Any]]:
    return records_for_source("user")


def system_skills() -> list[dict[str, Any]]:
    return records_for_source("system")


def plugin_skills() -> list[dict[str, Any]]:
    return records_for_source("plugin")


def skill_record(path: Path, source: str, text: str = "") -> dict[str, Any]:
    body = text or path.read_text(encoding="utf-8", errors="replace")
    line_count = len(body.splitlines())
    full_guide = path.parent / "references" / "full-guide.md"
    has_full_guide = full_guide.is_file()
    full_guide_referenced = "references/full-guide.md" in body.replace("\\", "/")
    name = skill_name(path, body)
    frontmatter = body.split("---", 2)[1] if body.startswith("---") and body.count("---") >= 2 else ""
    description_match = re.search(r"(?ms)^description:\s*(?:[>|][-+]?\s*\n(?P<block>(?:[ \t]+.*\n?)+)|(?P<inline>[^\n]+))", frontmatter)
    description = ""
    if description_match:
        description = description_match.group("inline") or description_match.group("block") or ""
        description = " ".join(line.strip() for line in description.splitlines() if line.strip()).strip(" '\"")
    contract_errors: list[str] = []
    if not frontmatter:
        contract_errors.append("missing_frontmatter")
    if not description:
        contract_errors.append("missing_description")
    if not VALID_NAME_RE.fullmatch(name):
        contract_errors.append("invalid_skill_name")
    metadata_match = re.search(r"(?m)^metadata:\s*(\{.+\})\s*$", frontmatter)
    metadata: dict[str, Any] = {}
    if metadata_match:
        try:
            metadata = json.loads(metadata_match.group(1))
        except json.JSONDecodeError:
            metadata = {}
    compatibility_declared = bool(
        re.search(r"(?m)^compatibility:\s*\S", frontmatter)
        or ((metadata.get("codex") or {}).get("compatibility"))
    )
    required_env = [
        str(value)
        for value in (((metadata.get("codex") or {}).get("required_env")) or [])
        if str(value).strip()
    ]
    missing_required_env = [name for name in required_env if not os.environ.get(name)]
    superseded_by = str(((metadata.get("codex") or {}).get("superseded_by")) or "").strip()
    missing_refs: list[str] = []
    for value in sorted(set(SCRIPT_REFERENCE_RE.findall(body))):
        clean = value.rstrip("`.,);]")
        if not script_reference_resolves(path, clean):
            missing_refs.append(clean)
    flags: list[str] = []
    if re.search(r"/Users/[^/\s]+", body):
        flags.append("foreign_macos_user_path")
    if re.search(r"(?m)^\s*(python3|pip3)\s+", body):
        flags.append("unix_only_command")
    if ("~/.claude" in body or ".claude/" in body) and not compatibility_declared:
        flags.append("claude_specific_path")
    if path.parent.name != name:
        flags.append("directory_name_mismatch")
    flags.extend(contract_errors)
    if missing_refs:
        flags.append("missing_relative_script")
    if line_count > RECOMMENDED_SKILL_LINE_LIMIT:
        flags.append("oversized_default_entry")
    if has_full_guide and not full_guide_referenced:
        flags.append("unreferenced_full_guide")
    missing_implementation = bool(missing_refs and not (path.parent / "scripts").is_dir())
    routing_eligible = not missing_implementation and not missing_required_env and not superseded_by and not contract_errors
    block_reasons = []
    if missing_implementation:
        block_reasons.append("missing_local_implementation")
    if missing_required_env:
        block_reasons.append("missing_required_environment")
    if superseded_by:
        block_reasons.append("superseded_skill")
    if contract_errors:
        block_reasons.append("invalid_skill_contract")
    return {
        "_record_schema_version": RECORD_SCHEMA_VERSION,
        "name": name,
        "description": description,
        "source": source,
        "path": str(path),
        "directory": path.parent.name,
        "sha256": file_hash(path),
        "size_bytes": path.stat().st_size,
        "line_count": line_count,
        "has_full_guide": has_full_guide,
        "full_guide_referenced": full_guide_referenced,
        "flags": flags,
        "missing_refs": missing_refs,
        "routing_eligible": routing_eligible,
        "routing_block_reason": ",".join(block_reasons),
        "required_env": required_env,
        "missing_required_env": missing_required_env,
        "superseded_by": superseded_by,
        "contract_errors": contract_errors,
    }


def skill_tree_stat_fingerprint(skill_file: Path) -> str:
    digest = hashlib.sha256()
    root = skill_file.parent
    for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in TRANSIENT_TREE_PARTS for part in relative.parts):
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def apply_runtime_eligibility(record: dict[str, Any]) -> dict[str, Any]:
    row = dict(record)
    required_env = [str(value) for value in row.get("required_env") or [] if str(value).strip()]
    missing_required_env = [name for name in required_env if not os.environ.get(name)]
    block_reasons: list[str] = []
    if row.get("missing_refs") and not (Path(str(row.get("path") or "")).parent / "scripts").is_dir():
        block_reasons.append("missing_local_implementation")
    if missing_required_env:
        block_reasons.append("missing_required_environment")
    if row.get("superseded_by"):
        block_reasons.append("superseded_skill")
    if row.get("contract_errors"):
        block_reasons.append("invalid_skill_contract")
    row["missing_required_env"] = missing_required_env
    row["routing_eligible"] = not block_reasons
    row["routing_block_reason"] = ",".join(block_reasons)
    return row


def load_admission_by_path() -> dict[str, dict[str, Any]]:
    if not ADMISSION_REGISTRY.exists():
        return {}
    try:
        payload = json.loads(ADMISSION_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(Path(str(item.get("path") or "")).resolve()).lower(): item
        for item in payload.get("skills", [])
        if isinstance(item, dict) and item.get("path")
    }


def trust_state_for(admission: dict[str, Any] | None, source: str) -> tuple[str, str]:
    state = str((admission or {}).get("state") or "unregistered")
    if state == "rejected":
        return state, "blocked"
    if state == "approved":
        return state, "trusted"
    if source in {"system", "plugin"}:
        return state, "managed"
    if state == "deferred":
        return state, "deferred"
    return state, "provisional"


def apply_admission(record: dict[str, Any], admission: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(record)
    admission_state, trust_state = trust_state_for(admission, str(row.get("source") or ""))
    row["skill_id"] = str((admission or {}).get("skill_id") or "")
    row["admission_state"] = admission_state
    row["trust_state"] = trust_state
    if trust_state == "blocked":
        reasons = [item for item in str(row.get("routing_block_reason") or "").split(",") if item]
        if "admission_rejected" not in reasons:
            reasons.append("admission_rejected")
        row["routing_eligible"] = False
        row["routing_block_reason"] = ",".join(reasons)
    return row


def refresh_incremental(*, state_db: Path = skill_lifecycle_state.STATE_DB) -> dict[str, Any]:
    cached = skill_lifecycle_state.active_rows(state_db)
    admission_by_path = load_admission_by_path()
    entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    parsed_count = 0
    reused_count = 0
    for path, source in discovered_skill_files():
        path_key = str(path)
        stat_fingerprint = skill_tree_stat_fingerprint(path)
        cached_row = cached.get(path_key)
        if (
            cached_row
            and cached_row.get("stat_fingerprint") == stat_fingerprint
            and cached_row.get("record", {}).get("_record_schema_version") == RECORD_SCHEMA_VERSION
        ):
            record = apply_runtime_eligibility(dict(cached_row["record"]))
            reused_count += 1
        else:
            record = apply_runtime_eligibility(skill_record(path, source))
            parsed_count += 1
        admission = admission_by_path.get(str(path.parent.resolve()).lower())
        record = apply_admission(record, admission)
        entries.append(
            {
                "path": path_key,
                "source": source,
                "name": record["name"],
                "stat_fingerprint": stat_fingerprint,
                "content_sha256": record["sha256"],
                "record": record,
            }
        )
        records.append(record)
    sync = skill_lifecycle_state.sync_records(entries, path=state_db)
    return {
        "schema": "skill_lifecycle.refresh.v1",
        "ok": True,
        "generated_at": now_iso(),
        "state_db": str(state_db),
        "parsed_count": parsed_count,
        "reused_count": reused_count,
        "records": records,
        "summary": {
            "run_id": sync["run_id"],
            "recorded_run": sync["recorded_run"],
            "bootstrap": sync["bootstrap"],
            "discovered_count": sync["discovered_count"],
            "change_count": sync["change_count"],
            "counts": sync["counts"],
            "parsed_count": parsed_count,
            "reused_count": reused_count,
        },
        "changes": sync["changes"],
    }


def script_reference_resolves(skill_file: Path, reference: str) -> bool:
    """Resolve local script references without treating owner variables as local files."""
    value = reference.replace("\\", "/")
    variable = re.match(r"^\$\{?([A-Z_]+)\}?/(.+)$", value)
    if variable:
        variable_name, suffix = variable.groups()
        if variable_name != "SKILL_DIR":
            return True
        value = suffix
    candidates = [
        skill_file.parent / value,
        GLOBAL_SKILLS / value,
        GLOBAL_SKILLS.parent / value,
        SYSTEM_SKILLS / value,
    ]
    return any(candidate.exists() for candidate in candidates)


def scattered_backup_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not GLOBAL_SKILLS.exists():
        return rows
    for path in sorted(GLOBAL_SKILLS.rglob("*"), key=str):
        if path.is_file() and BACKUP_FILE_RE.search(path.name):
            rows.append({
                "path": str(path),
                "relative_path": str(path.relative_to(GLOBAL_SKILLS)),
                "size_bytes": path.stat().st_size,
            })
    return rows


def canonical_platform_snapshot() -> dict[str, Any]:
    if not MYSKILLS_DB.exists():
        return {"available": False, "aligned": False, "reason": "myskills_db_missing"}
    connection = sqlite3.connect(f"file:{MYSKILLS_DB}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key='canonical_platform'").fetchone()
        canonical = str(row[0]) if row else ""
        platform = connection.execute("SELECT skills_dir FROM platforms WHERE id=?", (canonical,)).fetchone()
        global_platform = connection.execute(
            "SELECT id FROM platforms WHERE lower(skills_dir)=lower(?)", (str(GLOBAL_SKILLS),)
        ).fetchone()
        orphaned_skills = [
            {"id": str(row[0]), "name": str(row[1])}
            for row in connection.execute(
                "SELECT sk.id, sk.name FROM skills sk LEFT JOIN skill_locations sl ON sl.skill_id=sk.id "
                "WHERE sl.id IS NULL ORDER BY sk.name"
            )
        ]
    finally:
        connection.close()
    expected = str(global_platform[0]) if global_platform else ""
    return {
        "available": True,
        "canonical_platform": canonical,
        "canonical_path": str(platform[0]) if platform else "",
        "expected_platform": expected,
        "expected_path": str(GLOBAL_SKILLS),
        "aligned": bool(expected and canonical == expected),
        "orphaned_skills": orphaned_skills,
    }


def backup_pollution() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not GLOBAL_SKILLS.exists():
        return rows
    candidates: set[Path] = set()
    for path in GLOBAL_SKILLS.rglob("SKILL.md"):
        try:
            rel = path.relative_to(GLOBAL_SKILLS)
        except ValueError:
            continue
        parts = [part.lower() for part in rel.parts[:-1]]
        if any(part in ARCHIVE_NAMES for part in parts):
            archive_index = next(index for index, part in enumerate(parts) if part in ARCHIVE_NAMES)
            candidates.add(GLOBAL_SKILLS.joinpath(*rel.parts[: archive_index + 1]))
    for root in sorted(candidates, key=str):
        files = [item for item in root.rglob("*") if item.is_file()]
        rows.append({
            "path": str(root),
            "file_count": len(files),
            "skill_file_count": sum(1 for item in files if item.name == "SKILL.md"),
            "size_bytes": sum(item.stat().st_size for item in files),
        })
    return rows


def collisions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["name"]].append(row)
    return [
        {
            "name": name,
            "copies": len(rows),
            "sources": sorted({row["source"] for row in rows}),
            "paths": [row["path"] for row in rows],
            "unique_hashes": len({row["sha256"] for row in rows}),
        }
        for name, rows in sorted(grouped.items())
        if len(rows) > 1
    ]


def exact_content_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["sha256"]].append(row)
    return [
        {
            "sha256": digest,
            "copies": len(rows),
            "names": sorted({row["name"] for row in rows}),
            "paths": [row["path"] for row in rows],
        }
        for digest, rows in sorted(grouped.items())
        if len(rows) > 1 and len({row["name"] for row in rows}) > 1
    ]


def resolve_cross_source_collision(row: dict[str, Any]) -> dict[str, Any]:
    sources = set(row.get("sources") or [])
    if "user" in sources and "plugin" in sources:
        return {
            **row,
            "resolved": True,
            "resolution": "user_primary_plugin_namespaced",
            "rule": "The user skill keeps the unqualified name; plugin skills are exposed through a plugin-prefixed name.",
        }
    return {**row, "resolved": False, "resolution": "", "rule": ""}


def usage_window() -> dict[str, Any]:
    quality = skill_lifecycle_state.quality_summary()
    skills = quality.get("skills", {})
    return {
        "record_count": int(quality.get("record_count") or 0),
        "first_recorded_at": str(quality.get("first_recorded_at") or ""),
        "last_recorded_at": str(quality.get("last_recorded_at") or ""),
        "unique_selected_count": sum(1 for item in skills.values() if int(item.get("selected") or 0) > 0),
        "unique_used_count": sum(1 for item in skills.values() if int(item.get("applied") or 0) > 0),
        "evidence_source": str(skill_lifecycle_state.STATE_DB),
        "legacy_log_present": USAGE_LOG.exists(),
        "retirement_evidence_sufficient": False,
        "rule": "Indexed quality evidence is advisory only; short-window non-use never authorizes retirement by itself.",
    }


def scenario_snapshot() -> dict[str, Any]:
    if not MYSKILLS_DB.exists():
        return {"available": False, "scenario_count": 0, "aliases": [], "unscenarized": []}
    connection = sqlite3.connect(f"file:{MYSKILLS_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        scenarios = [dict(row) for row in connection.execute(
            "SELECT s.key, s.name, s.description, COUNT(ss.skill_id) AS skill_count "
            "FROM scenarios s LEFT JOIN skill_scenarios ss ON ss.scenario_id=s.id "
            "GROUP BY s.id ORDER BY s.sort_order, s.name"
        )]
        unscenarized = [row[0] for row in connection.execute(
            "SELECT sk.name FROM skills sk LEFT JOIN skill_scenarios ss ON ss.skill_id=sk.id "
            "GROUP BY sk.id HAVING COUNT(ss.scenario_id)=0 ORDER BY sk.name"
        )]
    finally:
        connection.close()
    aliases = [
        {"source": source, "destination": destination, "skill_count": next(
            (int(row["skill_count"]) for row in scenarios if row["name"] == source), 0
        )}
        for source, destination in SCENARIO_ALIASES.items()
        if any(row["name"] == source and int(row["skill_count"]) > 0 for row in scenarios)
    ]
    return {
        "available": True,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "alias_count": len(aliases),
        "aliases": aliases,
        "unscenarized": unscenarized,
    }


def pdf_capability_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in records if row["name"] == "pdf"]
    keywords = {
        "read_extract": ("extract", "read", "pdfplumber"),
        "create": ("create", "generate", "reportlab"),
        "forms": ("form", "fill"),
        "render_visual_verify": ("render", "visual", "poppler"),
        "merge_split": ("merge", "split", "pypdf"),
        "tables": ("table", "pdfplumber"),
    }
    results: list[dict[str, Any]] = []
    for row in candidates:
        text = Path(row["path"]).read_text(encoding="utf-8", errors="replace").lower()
        coverage = {key: any(term in text for term in terms) for key, terms in keywords.items()}
        results.append({**row, "coverage": coverage, "coverage_count": sum(coverage.values())})
    combined = {key: any(item["coverage"][key] for item in results) for key in keywords}
    replacement_candidates = [item for item in results if item["source"] != "user"]
    replacement_portable = any(
        not ({"foreign_macos_user_path", "unix_only_command", "claude_specific_path"} & set(item["flags"]))
        for item in replacement_candidates
    )
    safe_to_disable_user = bool(results) and replacement_portable and all(
        any(item["source"] != "user" and item["coverage"][key] for item in results)
        for key in combined
    )
    return {
        "schema": "skill_lifecycle.pdf_capability.v1",
        "candidates": results,
        "combined_coverage": combined,
        "safe_to_disable_user_skill": safe_to_disable_user,
        "decision": "retain_both" if not safe_to_disable_user else "plugin_covers_user_candidate",
        "rule": "Do not disable a colliding implementation until replacement coverage is complete and verified.",
    }


def default_metadata_budget(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the bounded default discovery manifest without disabling skills."""
    by_name: dict[str, dict[str, Any]] = {}
    for row in records:
        name = str(row.get("name") or "")
        if name not in DEFAULT_IMPLICIT_SKILL_NAMES:
            continue
        existing = by_name.get(name)
        if existing is None or SOURCE_PRIORITY.get(str(row.get("source")), 99) < SOURCE_PRIORITY.get(str(existing.get("source")), 99):
            by_name[name] = row
    manifest = [by_name[name] for name in DEFAULT_IMPLICIT_SKILL_NAMES if name in by_name]
    manifest_chars = sum(len(str(row.get("name") or "")) + len(str(row.get("description") or "")) for row in manifest)
    all_chars = sum(len(str(row.get("name") or "")) + len(str(row.get("description") or "")) for row in records)
    return {
        "schema": "skill_lifecycle.default_metadata_budget.v1",
        "budget_chars": DEFAULT_METADATA_BUDGET_CHARS,
        "all_active_chars": all_chars,
        "default_manifest_chars": manifest_chars,
        "within_budget": manifest_chars <= DEFAULT_METADATA_BUDGET_CHARS,
        "default_skill_count": len(manifest),
        "deferred_skill_count": max(0, len(records) - len(manifest)),
        "default_skills": [
            {"name": row["name"], "source": row["source"], "path": row["path"]}
            for row in manifest
        ],
        "rule": "The default manifest is a routing overlay; non-default skills remain installed and are selected only after domain routing or explicit mention.",
    }


def audit() -> dict[str, Any]:
    refresh = refresh_incremental()
    local = [row for row in refresh["records"] if row.get("source") == "user"]
    system = [row for row in refresh["records"] if row.get("source") == "system"]
    plugins = [row for row in refresh["records"] if row.get("source") == "plugin"]
    all_records = local + system + plugins
    stale = [
        row
        for row in local
        if set(row["flags"]) - {"oversized_default_entry", "unreferenced_full_guide"}
    ]
    invalid_contracts = [row for row in local if row.get("contract_errors")]
    oversized = [row for row in local if "oversized_default_entry" in row["flags"]]
    unreferenced_guides = [row for row in local if "unreferenced_full_guide" in row["flags"]]
    exact_duplicates = exact_content_duplicates(local)
    local_collisions = collisions(local)
    cross_collisions = [
        resolve_cross_source_collision(row)
        for row in collisions(all_records)
        if len(row["sources"]) > 1
    ]
    unresolved_cross_collisions = [row for row in cross_collisions if not row.get("resolved")]
    pollution = backup_pollution()
    backup_files = scattered_backup_files()
    canonical = canonical_platform_snapshot()
    scenarios = scenario_snapshot()
    plugin_catalog = skill_active_catalog.catalog_snapshot(plugin_cache=PLUGIN_CACHE)
    metadata_budget = default_metadata_budget(all_records)
    quality = skill_lifecycle_state.quality_summary()
    trust_counts: dict[str, int] = defaultdict(int)
    for row in all_records:
        trust_counts[str(row.get("trust_state") or "unknown")] += 1
    workspace_entries = []
    if WORKSPACE_SKILLS.exists():
        workspace_entries = [str(item) for item in sorted(WORKSPACE_SKILLS.iterdir(), key=str)]
    return {
        "schema": "skill_lifecycle.audit.v1",
        "ok": not pollution and not local_collisions,
        "generated_at": now_iso(),
        "read_only": True,
        "incremental_governance": refresh["summary"],
        "state": skill_lifecycle_state.snapshot(recent_limit=0),
        "counts": {
            "active_local": len(local),
            "system": len(system),
            "plugin": len(plugins),
            "backup_pollution_roots": len(pollution),
            "scattered_backup_files": len(backup_files),
            "scattered_backup_bytes": sum(row["size_bytes"] for row in backup_files),
            "active_local_collisions": len(local_collisions),
            "cross_source_collisions": len(cross_collisions),
            "resolved_cross_source_collisions": len(cross_collisions) - len(unresolved_cross_collisions),
            "unresolved_cross_source_collisions": len(unresolved_cross_collisions),
            "stale_candidate_count": len(stale),
            "invalid_contract_count": len(invalid_contracts),
            "oversized_default_entry_count": len(oversized),
            "progressive_entry_count": sum(1 for row in local if row["has_full_guide"] and row["full_guide_referenced"]),
            "unreferenced_full_guide_count": len(unreferenced_guides),
            "exact_content_duplicate_groups": len(exact_duplicates),
            "workspace_skill_entry_count": len(workspace_entries),
            "quality_event_count": quality.get("record_count", 0),
            "active_plugin_catalog_skill_count": plugin_catalog["active_skill_count"],
            "default_metadata_manifest_skill_count": metadata_budget["default_skill_count"],
        },
        "trust_counts": dict(sorted(trust_counts.items())),
        "quality": quality,
        "workspace_skill_entries": workspace_entries,
        "backup_pollution": pollution,
        "scattered_backup_files": backup_files,
        "canonical_platform": canonical,
        "active_local_collisions": local_collisions,
        "cross_source_collisions": cross_collisions,
        "unresolved_cross_source_collisions": unresolved_cross_collisions,
        "stale_candidates": stale,
        "invalid_contracts": invalid_contracts,
        "oversized_candidates": oversized,
        "unreferenced_full_guides": unreferenced_guides,
        "exact_content_duplicates": exact_duplicates,
        "usage": usage_window(),
        "scenarios": scenarios,
        "plugin_catalog": plugin_catalog,
        "metadata_budget": metadata_budget,
        "pdf_capability": pdf_capability_report(all_records),
    }


def review_cards(report: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for row in report.get("backup_pollution", []):
        cards.append({
            "id": f"archive-backup-root:{row['path']}",
            "kind": "backup_discovery_pollution",
            "title": Path(row["path"]).name,
            "source": row["path"],
            "digest": f"{row['skill_file_count']} backup SKILL.md files remain under the active discovery root.",
            "risk": "medium",
            "approval_action": "move_to_resource_backup_root",
        })
    for row in report.get("scattered_backup_files", []):
        cards.append({
            "id": f"archive-backup-file:{row['relative_path']}",
            "kind": "scattered_backup_file",
            "title": Path(row["path"]).name,
            "source": row["path"],
            "digest": "Backup file remains inside the active skill discovery root.",
            "risk": "low",
            "approval_action": "move_to_resource_backup_root",
        })
    canonical = report.get("canonical_platform", {})
    if canonical.get("available") and not canonical.get("aligned"):
        cards.append({
            "id": "align-canonical-platform",
            "kind": "canonical_platform_mismatch",
            "title": "MySkills canonical platform",
            "source": canonical.get("canonical_path", ""),
            "digest": f"Configured {canonical.get('canonical_platform')} but active global root belongs to {canonical.get('expected_platform')}.",
            "risk": "medium",
            "approval_action": "update_myskills_canonical_platform",
        })
    for row in report.get("active_local_collisions", []):
        cards.append({
            "id": f"duplicate-name:{row['name']}",
            "kind": "active_duplicate",
            "title": row["name"],
            "source": " | ".join(row["paths"]),
            "digest": f"{row['copies']} active local implementations share one skill name.",
            "risk": "high",
            "approval_action": "choose_canonical_then_archive_duplicate",
        })
    for row in report.get("cross_source_collisions", []):
        if row.get("resolved"):
            continue
        cards.append({
            "id": f"cross-source:{row['name']}",
            "kind": "cross_source_collision",
            "title": row["name"],
            "source": " | ".join(row["sources"]),
            "digest": "Multiple source classes expose the same unqualified skill name.",
            "risk": "medium",
            "approval_action": "verify_capability_and_set_precedence",
        })
    for row in report.get("stale_candidates", []):
        cards.append({
            "id": f"review-stale:{row['name']}",
            "kind": "stale_or_nonportable_candidate",
            "title": row["name"],
            "source": row["path"],
            "digest": ", ".join(row["flags"]),
            "risk": "low" if "missing_relative_script" not in row["flags"] else "medium",
            "approval_action": "review_before_revise_or_disable",
        })
    for row in report.get("oversized_candidates", []):
        cards.append({
            "id": f"review-oversized:{row['name']}",
            "kind": "oversized_default_entry",
            "title": row["name"],
            "source": row["path"],
            "digest": f"Default SKILL.md has {row['line_count']} lines; move detailed material to focused references.",
            "risk": "low",
            "approval_action": "progressive_disclosure_review",
        })
    for row in report.get("unreferenced_full_guides", []):
        cards.append({
            "id": f"review-unreferenced-guide:{row['name']}",
            "kind": "unreferenced_full_guide",
            "title": row["name"],
            "source": row["path"],
            "digest": "references/full-guide.md exists but the default entry does not point to it.",
            "risk": "low",
            "approval_action": "add_progressive_reference_handoff",
        })
    for row in report.get("exact_content_duplicates", []):
        cards.append({
            "id": f"review-exact-duplicate:{row['sha256'][:12]}",
            "kind": "exact_content_duplicate",
            "title": " | ".join(row["names"]),
            "source": " | ".join(row["paths"]),
            "digest": "Different skill names have byte-identical SKILL.md content.",
            "risk": "medium",
            "approval_action": "choose_canonical_or_explain_alias",
        })
    return cards


def repair_plan() -> dict[str, Any]:
    report = audit()
    cards = review_cards(report)
    scenario_actions = [
        {
            "id": f"scenario-alias:{row['source']}",
            "source": row["source"],
            "destination": row["destination"],
            "skill_count": row["skill_count"],
            "owner": "myskills.skills_set_scenarios",
        }
        for row in report.get("scenarios", {}).get("aliases", [])
    ]
    return {
        "schema": "skill_lifecycle.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "requires_confirm_apply": True,
        "review_cards": cards,
        "scenario_actions": scenario_actions,
        "pdf_capability": report.get("pdf_capability"),
        "apply_boundary": "Filesystem archive actions may use apply-approved. Scenario and enable/disable changes remain owned by MySkills.",
    }


def archive_root(source: Path, batch_root: Path) -> dict[str, Any]:
    resolved = source.resolve()
    skills_root = GLOBAL_SKILLS.resolve()
    try:
        relative = resolved.relative_to(skills_root)
    except ValueError as exc:
        raise ValueError(f"source_outside_active_skill_root:{resolved}") from exc
    destination = (batch_root / relative).resolve()
    if not str(destination).lower().startswith(str(batch_root.resolve()).lower() + "\\"):
        raise ValueError(f"destination_outside_archive_root:{destination}")
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved), str(destination))
    return {"source": str(resolved), "destination": str(destination), "deleted": False}


def archive_file(source: Path, batch_root: Path) -> dict[str, Any]:
    resolved = source.resolve()
    relative = resolved.relative_to(GLOBAL_SKILLS.resolve())
    destination = (batch_root / relative).resolve()
    if not str(destination).lower().startswith(str(batch_root.resolve()).lower() + "\\"):
        raise ValueError(f"destination_outside_archive_root:{destination}")
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved), str(destination))
    return {"source": str(resolved), "destination": str(destination), "deleted": False}


def apply_approved(ids: list[str], *, confirm_apply: bool) -> dict[str, Any]:
    if not confirm_apply:
        return {
            "schema": "skill_lifecycle.apply_approved.v1",
            "ok": False,
            "error": "confirm_apply_required",
            "requested_ids": ids,
        }
    report = audit()
    allowed = {
        card["id"]: card
        for card in review_cards(report)
        if card["kind"] in {"backup_discovery_pollution", "scattered_backup_file"}
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    batch_root = RESOURCE_BACKUP_ROOT / "lifecycle-governance" / stamp
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item_id in ids:
        card = allowed.get(item_id)
        if not card:
            skipped.append({"id": item_id, "reason": "unsupported_or_not_current"})
            continue
        batch_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root if card["kind"] == "backup_discovery_pollution" else archive_file
        applied.append({"id": item_id, **archive(Path(card["source"]), batch_root)})
    if applied:
        manifest = {
            "schema": "skill_lifecycle.archive_manifest.v1",
            "created_at": now_iso(),
            "deletes_files": False,
            "applied": applied,
        }
        (batch_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema": "skill_lifecycle.apply_approved.v1",
        "ok": bool(applied) and not skipped,
        "generated_at": now_iso(),
        "batch_root": str(batch_root) if applied else "",
        "applied": applied,
        "skipped": skipped,
    }


def doctor() -> dict[str, Any]:
    report = audit()
    issues: list[dict[str, Any]] = []
    if report["backup_pollution"]:
        issues.append({"severity": "risk", "code": "skill_backup_discovery_pollution", "count": len(report["backup_pollution"])})
    if report["scattered_backup_files"]:
        issues.append({"severity": "risk", "code": "skill_scattered_backup_pollution", "count": len(report["scattered_backup_files"])})
    canonical = report.get("canonical_platform", {})
    if canonical.get("available") and not canonical.get("aligned"):
        issues.append({"severity": "risk", "code": "skill_canonical_platform_mismatch", "count": 1})
    if canonical.get("orphaned_skills"):
        issues.append({"severity": "advisory", "code": "myskills_orphaned_records", "count": len(canonical["orphaned_skills"])})
    if report["active_local_collisions"]:
        issues.append({"severity": "risk", "code": "active_skill_name_collision", "count": len(report["active_local_collisions"])})
    if report["unresolved_cross_source_collisions"]:
        issues.append({"severity": "advisory", "code": "cross_source_skill_collision", "count": len(report["unresolved_cross_source_collisions"])})
    if report["workspace_skill_entries"]:
        issues.append({"severity": "risk", "code": "workspace_skill_root_not_empty", "count": len(report["workspace_skill_entries"])})
    if report["stale_candidates"]:
        issues.append({"severity": "advisory", "code": "stale_skill_review_candidates", "count": len(report["stale_candidates"])})
    if report["invalid_contracts"]:
        issues.append({"severity": "risk", "code": "invalid_skill_contracts", "count": len(report["invalid_contracts"])})
    if report["oversized_candidates"]:
        issues.append({"severity": "advisory", "code": "oversized_skill_entries", "count": len(report["oversized_candidates"])})
    if report["unreferenced_full_guides"]:
        issues.append({"severity": "advisory", "code": "unreferenced_skill_full_guides", "count": len(report["unreferenced_full_guides"])})
    if report["exact_content_duplicates"]:
        issues.append({"severity": "risk", "code": "exact_duplicate_skill_content", "count": len(report["exact_content_duplicates"])})
    if not report.get("plugin_catalog", {}).get("ok"):
        issues.append({"severity": "risk", "code": "active_plugin_catalog_unresolved", "count": 1})
    if not report.get("metadata_budget", {}).get("within_budget"):
        issues.append({"severity": "risk", "code": "default_skill_metadata_budget_exceeded", "count": 1})
    aliases = report.get("scenarios", {}).get("alias_count", 0)
    if aliases:
        issues.append({"severity": "advisory", "code": "scenario_aliases_pending", "count": aliases})
    state = report.get("state", {})
    if not state.get("available"):
        issues.append({"severity": "risk", "code": "skill_lifecycle_state_unavailable", "count": 1})
    return {
        "schema": "skill_lifecycle.doctor.v1",
        "ok": not any(item["severity"] == "risk" for item in issues),
        "generated_at": now_iso(),
        "status": "degraded" if any(item["severity"] == "risk" for item in issues) else ("advisory" if issues else "ok"),
        "issues": issues,
        "counts": report["counts"],
        "incremental_governance": report.get("incremental_governance", {}),
        "state": state,
    }


def validate() -> dict[str, Any]:
    report = audit()
    checks = [
        {"name": "active_skill_root_has_no_backup_skill_trees", "ok": not report["backup_pollution"], "detail": report["backup_pollution"]},
        {"name": "active_skill_root_has_no_scattered_backups", "ok": not report["scattered_backup_files"], "detail": len(report["scattered_backup_files"])},
        {"name": "active_local_names_unique", "ok": not report["active_local_collisions"], "detail": report["active_local_collisions"]},
        {"name": "active_local_contracts_valid", "ok": not report["invalid_contracts"], "detail": [row["name"] for row in report["invalid_contracts"]]},
        {"name": "global_root_is_only_user_skill_source", "ok": not report["workspace_skill_entries"], "detail": report["workspace_skill_entries"]},
        {"name": "myskills_canonical_matches_global_root", "ok": report.get("canonical_platform", {}).get("aligned", False), "detail": report.get("canonical_platform", {})},
        {"name": "myskills_has_no_orphaned_skill_records", "ok": not report.get("canonical_platform", {}).get("orphaned_skills", []), "detail": report.get("canonical_platform", {}).get("orphaned_skills", [])},
        {"name": "progressive_full_guides_are_referenced", "ok": not report["unreferenced_full_guides"], "detail": [row["name"] for row in report["unreferenced_full_guides"]]},
        {"name": "usage_nonuse_not_retirement_authority", "ok": report["usage"]["retirement_evidence_sufficient"] is False, "detail": report["usage"]},
        {"name": "resource_skill_backup_root_available", "ok": RESOURCE_BACKUP_ROOT.parent.exists(), "detail": str(RESOURCE_BACKUP_ROOT)},
        {"name": "repair_plan_available", "ok": True, "detail": "skill_lifecycle_governance.py repair-plan"},
        {"name": "incremental_state_available", "ok": report.get("state", {}).get("available") is True, "detail": report.get("state", {})},
        {"name": "incremental_state_matches_discovery", "ok": report.get("state", {}).get("active_count") == sum(report["counts"].get(key, 0) for key in ("active_local", "system", "plugin")), "detail": report.get("incremental_governance", {})},
        {"name": "admission_trust_is_indexed", "ok": sum(report.get("trust_counts", {}).values()) == sum(report["counts"].get(key, 0) for key in ("active_local", "system", "plugin")), "detail": report.get("trust_counts", {})},
        {"name": "quality_evidence_is_sqlite_backed", "ok": report.get("quality", {}).get("ok") is True, "detail": report.get("quality", {})},
        {"name": "active_plugins_resolve_from_configured_catalog", "ok": report.get("plugin_catalog", {}).get("ok") is True, "detail": report.get("plugin_catalog", {})},
        {"name": "default_skill_metadata_manifest_within_budget", "ok": report.get("metadata_budget", {}).get("within_budget") is True, "detail": report.get("metadata_budget", {})},
    ]
    return {
        "schema": "skill_lifecycle.validate.v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
    }


def record_lineage(
    skill: str,
    evolution_kind: str,
    parent_version: str,
    child_version: str,
    source: str,
    reason: str,
    validation_evidence: str = "",
) -> dict[str, Any]:
    material = "\n".join(
        [skill, evolution_kind.upper(), parent_version, child_version, source, reason, validation_evidence]
    )
    lineage_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return skill_lifecycle_state.record_lineage(
        {
            "lineage_key": lineage_key,
            "evolution_kind": evolution_kind,
            "skill_name": skill,
            "parent_version": parent_version,
            "child_version": child_version,
            "source": source,
            "reason": reason,
            "validation_evidence": validation_evidence,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Skill lifecycle audit and approved repair owner")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("audit", "doctor", "repair-plan", "pdf-capability", "refresh", "state", "validate"):
        sub.add_parser(command)
    lineage_parser = sub.add_parser("record-lineage")
    lineage_parser.add_argument("--skill", required=True)
    lineage_parser.add_argument("--kind", required=True, choices=sorted(skill_lifecycle_state.LINEAGE_KINDS))
    lineage_parser.add_argument("--parent-version", default="")
    lineage_parser.add_argument("--child-version", required=True)
    lineage_parser.add_argument("--source", required=True)
    lineage_parser.add_argument("--reason", required=True)
    lineage_parser.add_argument("--validation-evidence", default="")
    apply_parser = sub.add_parser("apply-approved")
    apply_parser.add_argument("--ids", required=True, help="Comma-separated review card ids")
    apply_parser.add_argument("--confirm-apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "audit":
        payload = audit()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "repair-plan":
        payload = repair_plan()
    elif args.command == "pdf-capability":
        payload = audit()["pdf_capability"]
        payload["ok"] = True
        payload["generated_at"] = now_iso()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "record-lineage":
        payload = record_lineage(
            args.skill,
            args.kind,
            args.parent_version,
            args.child_version,
            args.source,
            args.reason,
            args.validation_evidence,
        )
    elif args.command == "refresh":
        refresh = refresh_incremental()
        payload = {
            key: value for key, value in refresh.items() if key not in {"records"}
        }
    elif args.command == "state":
        payload = skill_lifecycle_state.snapshot()
    else:
        payload = apply_approved([item.strip() for item in args.ids.split(",") if item.strip()], confirm_apply=args.confirm_apply)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
