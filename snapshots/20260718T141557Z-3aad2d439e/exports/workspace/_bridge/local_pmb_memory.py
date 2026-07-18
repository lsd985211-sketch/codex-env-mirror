from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pmb_compatibility
from local_pmb_memory_process import exclusive_process_lock, hidden_creation_kwargs, run_pmb_command
from mcp_execution_priority import HUB_MANAGED_MCP_NAMES
from shared.backup_router import create_backup as create_routed_backup
from system_membership import retirement_tombstones


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
CODEX_HOME = Path.home() / ".codex"
CODEX_CONFIG = CODEX_HOME / "config.toml"
PMB_VENV = BRIDGE_ROOT / "venvs" / "pmb-memory"
PMB_EXE = PMB_VENV / "Scripts" / "pmb.exe"
PMB_PYTHON = PMB_VENV / "Scripts" / "python.exe"
PMB_PYTHONW = PMB_VENV / "Scripts" / "pythonw.exe"
NO_WINDOW_KW = hidden_creation_kwargs()


def resolve_codex_library_root() -> Path:
    desktop = Path.home() / "Desktop"
    short_root = desktop / "CODEX~1"
    if short_root.exists():
        return short_root
    return desktop / "Codex资源库"


MEMORY_ROOT = resolve_codex_library_root() / "memory"
PMB_HOME = MEMORY_ROOT / "pmb" / "data"
PMB_WORKSPACE = "mcsmanager"
PMB_DAEMON_LOCK = PMB_HOME / ".daemon-lifecycle.lock"
PMB_DAEMON_PROCESS_NAME_REGEX = r"^pythonw?(\.exe)?$"
REPORT_DIR = MEMORY_ROOT / "pmb" / "reports"
IMPORT_DIR = MEMORY_ROOT / "pmb" / "imports"
SCHEMA_DIR = MEMORY_ROOT / "pmb" / "schemas"
LEDGER_PATH = IMPORT_DIR / "migration-ledger.json"
MEMORY_MANIFEST = MEMORY_ROOT / "memory_manifest.json"
MEMORY_README = MEMORY_ROOT / "README.txt"
MEMORY_POLICY = MEMORY_ROOT / "governance" / "memory_policy.json"
USER_PROFILE = MEMORY_ROOT / "profiles" / "user_profile.json"
MEMORY_MANIFEST_SCHEMA = SCHEMA_DIR / "memory_manifest.schema.json"
USER_PROFILE_SCHEMA = SCHEMA_DIR / "user_profile.schema.json"
PROFILE_RULE_SOURCE_PATHS = [
    CODEX_HOME / "AGENTS.md",
    ROOT / "AGENTS.md",
    CODEX_HOME / "skills" / "global-framework" / "SKILL.md",
    CODEX_HOME / "skills" / "memory-systems" / "SKILL.md",
]

SKILL_ROOTS = [
    CODEX_HOME / "skills",
    ROOT / ".codex" / "skills",
]

LOCAL_PMB_MCP_NAME = "local-pmb-memory"


def retired_member_archive_root() -> Path | None:
    roots = {
        str(item.get("archive_root") or "")
        for item in retirement_tombstones()
        if item.get("system") == "mcp" and item.get("replacement") == LOCAL_PMB_MCP_NAME
    } - {""}
    if len(roots) > 1:
        raise RuntimeError("retired PMB memory members must declare one authoritative archive root")
    if not roots:
        return None
    path = Path(roots.pop())
    return path if path.is_absolute() else ROOT / path


RETIRED_MEMORY_ARCHIVE_ROOT = retired_member_archive_root()

def build_legacy_memory_sources(archive_root: Path | None) -> dict[str, Path]:
    sources = {
        "codex_memory_markdown": CODEX_HOME / "memories" / "MEMORY.md",
        "codex_rollout_summaries": CODEX_HOME / "memories" / "rollout_summaries",
        "bridge_codex_knowledge": BRIDGE_ROOT / "shared" / "codex-knowledge.md",
        "bridge_workspace_knowledge": BRIDGE_ROOT / "shared" / "3c3u-workspace-knowledge.md",
        "bridge_checkpoints_memory_system": BRIDGE_ROOT / "shared" / "checkpoints" / "memory-system",
    }
    if archive_root is not None:
        sources.update({
            "project_kb": archive_root / "implementations" / "mcp-project-kb",
            "project_kb_index": archive_root / "data" / ".knowledge" / "index" / "project-kb.sqlite",
            "memory_graph_codex": archive_root / "data" / "_bridge" / "memory_graph" / "codex" / "memory-graph.sqlite",
            "memory_graph_reasonix": archive_root / "data" / "_bridge" / "memory_graph" / "reasonix" / "memory-graph.sqlite",
            "chroma_memory": archive_root / "data" / "_bridge" / "chroma_memory" / "chroma.sqlite3",
            "vector_memory_codex": archive_root / "data" / "_bridge" / "vector_memory" / "codex" / "chroma.sqlite3",
            "vector_memory_shared": archive_root / "data" / "_bridge" / "vector_memory" / "shared" / "chroma.sqlite3",
            "vector_memory_reasonix": archive_root / "data" / "_bridge" / "vector_memory" / "reasonix" / "chroma.sqlite3",
        })
    return sources


LEGACY_MEMORY_SOURCES = build_legacy_memory_sources(RETIRED_MEMORY_ARCHIVE_ROOT)

LOCAL_PMB_HUB_MANAGED = LOCAL_PMB_MCP_NAME in HUB_MANAGED_MCP_NAMES
LOCAL_PMB_MCP_BLOCK = f"""[mcp_servers.local-pmb-memory]
command = 'C:\\Users\\45543\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe'
args = ['C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager\\_bridge\\mcp_profile_launcher.py', 'pmb']
startup_timeout_sec = 120.0

[mcp_servers.local-pmb-memory.env]
PMB_HOME = '{PMB_HOME}'
PMB_WORKSPACE = "mcsmanager"
PYTHONIOENCODING = "utf-8"
PYTHONUTF8 = "1"
"""

SECRET_PATTERNS = [
    "token",
    "authorization",
    "授权码",
    "password",
    "secret",
    "bearer",
    "api_key",
    "smtp",
]

PMB_NOTE_MAX_TEXT_CHARS = 6000


def retired_memory_mcp_names() -> set[str]:
    return {
        str(item.get("member") or "")
        for item in retirement_tombstones()
        if item.get("system") == "mcp" and item.get("replacement") == LOCAL_PMB_MCP_NAME
    } - {""}


def historical_source_manifest() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": str(path),
            "lifecycle": "historical_only",
            "participates_in_current_health": False,
            "participates_in_recall": False,
            "inspection": "not_scanned",
            "allowed_commands": ["import-dry-run", "import-apply"],
        }
        for name, path in LEGACY_MEMORY_SOURCES.items()
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def ensure_dirs() -> None:
    for path in [MEMORY_ROOT, PMB_HOME, REPORT_DIR, IMPORT_DIR, SCHEMA_DIR, MEMORY_POLICY.parent, USER_PROFILE.parent]:
        path.mkdir(parents=True, exist_ok=True)


def read_json_file(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {}, "json_root_not_object"
    return payload, ""


def required_keys_present(payload: dict[str, Any], required: list[str]) -> list[str]:
    return [key for key in required if key not in payload]


def validate_manifest_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    missing = required_keys_present(
        payload,
        ["schema", "version", "updated_at", "authority", "primary_store", "entrypoints", "namespaces", "policies", "maintenance_contract"],
    )
    if missing:
        issues.append({"code": "manifest_missing_required_keys", "missing": missing})
    if payload.get("schema") != "codex-local-memory-manifest/v1":
        issues.append({"code": "manifest_schema_mismatch", "expected": "codex-local-memory-manifest/v1", "actual": payload.get("schema")})
    namespaces = payload.get("namespaces")
    if not isinstance(namespaces, list) or not namespaces:
        issues.append({"code": "manifest_namespaces_missing_or_empty"})
    else:
        seen: set[str] = set()
        for index, row in enumerate(namespaces):
            if not isinstance(row, dict):
                issues.append({"code": "manifest_namespace_not_object", "index": index})
                continue
            missing_row = required_keys_present(row, ["id", "purpose", "store", "memory_type", "write_policy", "privacy_class"])
            if missing_row:
                issues.append({"code": "manifest_namespace_missing_required_keys", "index": index, "missing": missing_row})
            ns_id = str(row.get("id") or "")
            if ns_id in seen:
                issues.append({"code": "manifest_duplicate_namespace", "id": ns_id})
            seen.add(ns_id)
    policies = payload.get("policies") if isinstance(payload.get("policies"), dict) else {}
    if policies.get("secrets_in_normal_memory") is not False:
        issues.append({"code": "manifest_secret_policy_not_false"})
    if policies.get("skills_independent_from_memory") is not True:
        issues.append({"code": "manifest_skill_independence_not_true"})
    return issues


def validate_user_profile_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    missing = required_keys_present(payload, ["schema", "profile_id", "updated_at", "privacy_class", "secret_storage_policy", "facts"])
    if missing:
        issues.append({"code": "profile_missing_required_keys", "missing": missing})
    if payload.get("schema") != "codex-user-profile/v1":
        issues.append({"code": "profile_schema_mismatch", "expected": "codex-user-profile/v1", "actual": payload.get("schema")})
    secret_policy = payload.get("secret_storage_policy") if isinstance(payload.get("secret_storage_policy"), dict) else {}
    if secret_policy.get("normal_memory_may_store_secrets") is not False:
        issues.append({"code": "profile_secret_policy_not_false"})
    facts = payload.get("facts")
    if not isinstance(facts, list):
        issues.append({"code": "profile_facts_not_list"})
        return issues
    seen: set[str] = set()
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            issues.append({"code": "profile_fact_not_object", "index": index})
            continue
        missing_fact = required_keys_present(fact, ["id", "category", "value", "source", "confidence", "valid_from", "review_status"])
        if missing_fact:
            issues.append({"code": "profile_fact_missing_required_keys", "index": index, "missing": missing_fact})
        fact_id = str(fact.get("id") or "")
        if fact_id in seen:
            issues.append({"code": "profile_duplicate_fact", "id": fact_id})
        seen.add(fact_id)
        try:
            confidence = float(fact.get("confidence"))
        except Exception:
            issues.append({"code": "profile_fact_confidence_invalid", "index": index, "value": fact.get("confidence")})
            continue
        if confidence < 0 or confidence > 1:
            issues.append({"code": "profile_fact_confidence_out_of_range", "index": index, "value": confidence})
    return issues


PROFILE_GUIDANCE_CATEGORY_PRIORITY = {
    "communication_style": 0,
    "workflow_expectations": 1,
    "system_governance": 2,
    "memory_preferences": 3,
    "tool_preferences": 4,
    "permission_preferences": 5,
    "risk_controls": 6,
    "long_term_goals": 7,
    "identity_context": 8,
}

PROFILE_OWNED_CATEGORIES = {
    "communication_style",
    "identity_context",
    "long_term_goals",
    "tradeoff_priority",
    "permission_preferences",
}
PROFILE_OWNED_IDS = {
    "performance.keep_function_without_waste",
    "workflow.no_unnecessary_admin_blocks",
}
RULE_OWNED_CATEGORIES = {
    "workflow_expectations",
    "system_governance",
    "memory_preferences",
    "tool_preferences",
    "risk_controls",
}
RULE_OWNED_ID_PREFIXES = (
    "mail.",
    "memory.",
    "risk.",
    "security.",
    "skills.",
    "system.",
    "tools.",
    "workflow.backup_",
    "workflow.restart_",
    "workflow.root_cause_",
    "workflow.verify_",
)


def normalized_rule_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_]+", str(text or "").lower())
        if len(token) >= 4
    }


def profile_rule_corpus() -> tuple[str, set[str], list[str]]:
    parts: list[str] = []
    sources: list[str] = []
    for path in PROFILE_RULE_SOURCE_PATHS:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        parts.append(text)
        sources.append(str(path))
    corpus = "\n".join(parts).lower()
    return corpus, normalized_rule_tokens(corpus), sources


def profile_fact_rule_overlap(fact: dict[str, Any], *, corpus: str, corpus_tokens: set[str]) -> dict[str, Any]:
    fact_id = str(fact.get("id") or "")
    value = str(fact.get("value") or "")
    source = str(fact.get("source") or "")
    if "agents.md" in source.lower():
        return {
            "covered": True,
            "reason": "source_is_rules_file",
            "fact_id": fact_id,
            "overlap_ratio": 1.0,
        }
    if not value.strip() or not corpus:
        return {"covered": False, "reason": "no_rule_corpus_or_empty_value", "fact_id": fact_id, "overlap_ratio": 0.0}
    tokens = normalized_rule_tokens(value)
    if not tokens:
        return {"covered": False, "reason": "no_comparable_tokens", "fact_id": fact_id, "overlap_ratio": 0.0}
    overlap = tokens & corpus_tokens
    overlap_ratio = len(overlap) / max(1, len(tokens))
    compact_value = re.sub(r"\s+", " ", value.lower()).strip()
    exact_substring = len(compact_value) >= 48 and compact_value in re.sub(r"\s+", " ", corpus)
    covered = exact_substring or overlap_ratio >= 0.72
    return {
        "covered": covered,
        "reason": "exact_rule_substring" if exact_substring else ("high_rule_token_overlap" if covered else "not_rule_covered"),
        "fact_id": fact_id,
        "overlap_ratio": round(overlap_ratio, 3),
        "overlap_terms": sorted(overlap)[:12],
    }


def profile_fact_responsibility(fact: dict[str, Any], *, corpus: str, corpus_tokens: set[str]) -> dict[str, Any]:
    fact_id = str(fact.get("id") or "")
    category = str(fact.get("category") or "")
    source = str(fact.get("source") or "")
    overlap = profile_fact_rule_overlap(fact, corpus=corpus, corpus_tokens=corpus_tokens)
    if fact_id in PROFILE_OWNED_IDS or category in PROFILE_OWNED_CATEGORIES:
        owner = "user_profile"
        reason = "profile_owned_category_or_id"
    elif "agents.md" in source.lower() or fact_id.startswith(RULE_OWNED_ID_PREFIXES) or category in RULE_OWNED_CATEGORIES:
        owner = "rules"
        reason = "rule_owned_category_id_or_source"
    elif overlap.get("covered"):
        owner = "needs_review"
        reason = "rule_text_overlap_without_clear_owner"
    else:
        owner = "user_profile"
        reason = "not_rule_owned"
    return {
        "fact_id": fact_id,
        "category": category,
        "owner": owner,
        "reason": reason,
        "rule_overlap": overlap,
    }


def user_profile_responsibility_gate(profile_payload: dict[str, Any]) -> dict[str, Any]:
    facts = profile_payload.get("facts") if isinstance(profile_payload, dict) else []
    if not isinstance(facts, list):
        facts = []
    corpus, corpus_tokens, sources = profile_rule_corpus()
    assignments: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        assignments.append(profile_fact_responsibility(fact, corpus=corpus, corpus_tokens=corpus_tokens))
    rule_owned = [item for item in assignments if item.get("owner") == "rules"]
    profile_owned = [item for item in assignments if item.get("owner") == "user_profile"]
    needs_review = [item for item in assignments if item.get("owner") == "needs_review"]
    return {
        "schema": "codex-user-profile.responsibility_gate.v1",
        "ok": True,
        "responsibility_boundary": {
            "rules": "Global/workspace rules own mandatory behavior, safety constraints, tool policy, repeatable procedures, and maintenance contracts.",
            "user_profile": "User profile owns user-specific stable preferences, identity/context, long-term goals, and tradeoff priorities not already represented as rules.",
            "prewrite_rule": "Before writing user_profile, classify the candidate by responsibility and require explicit user approval; reject rule-owned facts even if they came from user conversation.",
        },
        "rule_source_count": len(sources),
        "rule_sources": sources,
        "profile_owned_fact_count": len(profile_owned),
        "profile_owned_fact_ids": [str(item.get("fact_id") or "") for item in profile_owned],
        "rule_owned_fact_count": len(rule_owned),
        "rule_owned_fact_ids": [str(item.get("fact_id") or "") for item in rule_owned],
        "needs_review_fact_count": len(needs_review),
        "needs_review_fact_ids": [str(item.get("fact_id") or "") for item in needs_review],
        "assignments": assignments,
        "write_gate": {
            "enabled": True,
            "rule": "Do not add rule-owned facts to user_profile. Use AGENTS/workspace rules for mandatory behavior; write only approved inferred user-specific stable preferences and context into user_profile.",
            "approval_rule": "Unapproved inferences must stay outside user_profile as proposals or closeout review items. The profile file itself is the approved result set.",
        },
    }


def profile_fact_is_active(fact: dict[str, Any], *, today: str | None = None) -> bool:
    if not isinstance(fact, dict):
        return False
    if str(fact.get("review_status") or "").lower() != "active":
        return False
    valid_until = fact.get("valid_until")
    if valid_until:
        today_value = today or datetime.now(timezone.utc).date().isoformat()
        return str(valid_until) >= today_value
    return True


def build_user_profile_guidance(profile_payload: dict[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    facts = profile_payload.get("facts") if isinstance(profile_payload, dict) else []
    if not isinstance(facts, list):
        facts = []
    responsibility_gate = user_profile_responsibility_gate(profile_payload)
    rule_owned_ids = set(responsibility_gate.get("rule_owned_fact_ids") or [])
    review_ids = set(responsibility_gate.get("needs_review_fact_ids") or [])
    active_facts = [
        fact
        for fact in facts
        if profile_fact_is_active(fact)
        and str(fact.get("id") or "") not in rule_owned_ids
        and str(fact.get("id") or "") not in review_ids
    ]
    active_facts.sort(
        key=lambda fact: (
            PROFILE_GUIDANCE_CATEGORY_PRIORITY.get(str(fact.get("category") or ""), 99),
            str(fact.get("id") or ""),
        )
    )
    selected = active_facts[: max(0, int(max_items))]
    categories: dict[str, list[str]] = {}
    action_guidance: list[dict[str, str]] = []
    for fact in selected:
        category = str(fact.get("category") or "uncategorized")
        fact_id = str(fact.get("id") or "")
        categories.setdefault(category, []).append(fact_id)
        action_guidance.append(
            {
                "category": category,
                "fact_id": fact_id,
                "instruction": str(fact.get("value") or "").strip(),
            }
        )
    secret_policy = profile_payload.get("secret_storage_policy") if isinstance(profile_payload, dict) else {}
    if not isinstance(secret_policy, dict):
        secret_policy = {}
    return {
        "schema": "codex-user-profile.guidance.v1",
        "ok": True,
        "guidance_available": bool(action_guidance),
        "active_fact_count": len(active_facts),
        "responsibility_gate": responsibility_gate,
        "rule_owned_fact_count": len(rule_owned_ids),
        "rule_owned_fact_ids": sorted(rule_owned_ids),
        "needs_review_fact_count": len(review_ids),
        "needs_review_fact_ids": sorted(review_ids),
        "selected_fact_count": len(action_guidance),
        "selected_fact_ids": [item["fact_id"] for item in action_guidance],
        "categories": categories,
        "action_guidance": action_guidance,
        "privacy_class": profile_payload.get("privacy_class") if isinstance(profile_payload, dict) else None,
        "normal_memory_may_store_secrets": bool(secret_policy.get("normal_memory_may_store_secrets")),
    }


def memory_surface_snapshot() -> dict[str, Any]:
    manifest_payload, manifest_error = read_json_file(MEMORY_MANIFEST) if MEMORY_MANIFEST.exists() else ({}, "missing")
    profile_payload, profile_error = read_json_file(USER_PROFILE) if USER_PROFILE.exists() else ({}, "missing")
    policy_payload, policy_error = read_json_file(MEMORY_POLICY) if MEMORY_POLICY.exists() else ({}, "missing")
    manifest_issues = validate_manifest_payload(manifest_payload) if not manifest_error else [{"code": "manifest_unreadable", "error": manifest_error}]
    profile_issues = validate_user_profile_payload(profile_payload) if not profile_error else [{"code": "profile_unreadable", "error": profile_error}]
    policy_issues: list[dict[str, Any]] = []
    if policy_error:
        policy_issues.append({"code": "policy_unreadable", "error": policy_error})
    elif policy_payload.get("schema") != "codex-memory-policy/v1":
        policy_issues.append({"code": "policy_schema_mismatch", "actual": policy_payload.get("schema")})
    return {
        "root": str(MEMORY_ROOT),
        "readme": file_summary(MEMORY_README),
        "manifest": {
            **file_summary(MEMORY_MANIFEST),
            "schema": manifest_payload.get("schema"),
            "namespace_count": len(manifest_payload.get("namespaces") or []) if isinstance(manifest_payload.get("namespaces"), list) else 0,
            "issues": manifest_issues,
            "ok": not manifest_issues,
        },
        "user_profile": {
            **file_summary(USER_PROFILE),
            "schema": profile_payload.get("schema"),
            "fact_count": len(profile_payload.get("facts") or []) if isinstance(profile_payload.get("facts"), list) else 0,
            "guidance": build_user_profile_guidance(profile_payload),
            "issues": profile_issues,
            "ok": not profile_issues,
        },
        "policy": {
            **file_summary(MEMORY_POLICY),
            "schema": policy_payload.get("schema"),
            "issues": policy_issues,
            "ok": not policy_issues,
        },
        "schemas": {
            "manifest": file_summary(MEMORY_MANIFEST_SCHEMA),
            "user_profile": file_summary(USER_PROFILE_SCHEMA),
        },
    }


def load_migration_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {"schema": "local-pmb-memory.migration_ledger.v1", "imported_source_ids": {}, "updated_at": None}
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": "local-pmb-memory.migration_ledger.v1", "imported_source_ids": {}, "updated_at": None}
    if not isinstance(data.get("imported_source_ids"), dict):
        data["imported_source_ids"] = {}
    return data


def save_migration_ledger(data: dict[str, Any]) -> None:
    ensure_dirs()
    data["updated_at"] = now_iso()
    LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mark_imported(candidates: list[Any], results: list[dict[str, Any]]) -> None:
    ledger = load_migration_ledger()
    imported = ledger.setdefault("imported_source_ids", {})
    by_source_id = {item.source_id: item for item in candidates}
    for row in results:
        if not row.get("ok"):
            continue
        source_id = str(row.get("source_id") or "")
        item = by_source_id.get(source_id)
        imported[source_id] = {
            "source_system": row.get("source_system"),
            "kind": row.get("kind"),
            "imported_at": now_iso(),
            "content_sha256": sha256_text(item.text) if item else "",
        }
    save_migration_ledger(ledger)


def rebuild_ledger_from_reports() -> dict[str, Any]:
    ensure_dirs()
    ledger = load_migration_ledger()
    imported = ledger.setdefault("imported_source_ids", {})
    report_count = 0
    row_count = 0
    for report in sorted(IMPORT_DIR.glob("*-pmb-import-apply.json")):
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except Exception:
            continue
        report_count += 1
        for row in data.get("results", []) if isinstance(data.get("results"), list) else []:
            if not isinstance(row, dict) or not row.get("ok"):
                continue
            source_id = str(row.get("source_id") or "")
            if not source_id:
                continue
            imported[source_id] = {
                "source_system": row.get("source_system"),
                "kind": row.get("kind"),
                "imported_at": data.get("generated_at") or now_iso(),
                "content_sha256": "",
                "source_report": str(report),
            }
            row_count += 1
    save_migration_ledger(ledger)
    return {
        "schema": "local-pmb-memory.ledger_rebuild.v1",
        "ok": True,
        "report_count": report_count,
        "imported_source_id_count": len(imported),
        "rows_seen": row_count,
        "ledger_path": str(LEDGER_PATH),
    }


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


def pmb_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PMB_HOME"] = str(PMB_HOME)
    env["PMB_WORKSPACE"] = PMB_WORKSPACE
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_pmb(args: list[str], timeout: int = 60) -> dict[str, Any]:
    if not PMB_EXE.exists():
        return {"ok": False, "reason": "pmb_exe_missing", "command": str(PMB_EXE)}
    return run_pmb_command(
        pmb_exe=PMB_EXE,
        pmb_pythonw=PMB_PYTHONW,
        args=args,
        cwd=ROOT,
        env=pmb_env(),
        timeout=timeout,
    )


def pmb_recall(query: str, *, top_k: int = 5) -> dict[str, Any]:
    clean_query = str(query or "").strip()
    if not clean_query:
        return {"schema": "local-pmb-memory.recall.v1", "ok": False, "reason": "empty_query"}
    top = max(1, min(20, int(top_k or 5)))
    start = time.time()
    result = run_pmb(["recall", clean_query, "--top", str(top)], timeout=180)
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    no_matches = "No matches." in stdout or "No matches." in stderr
    return {
        "schema": "local-pmb-memory.recall.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "query": clean_query,
        "top_k": top,
        "elapsed_ms": int((time.time() - start) * 1000),
        "has_matches": bool(result.get("ok")) and not no_matches,
        "stdout": stdout,
        "stderr": stderr,
        "preview": (stdout + "\n" + stderr).strip()[:6000],
        "raw": result,
        "contract": {
            "read_only": True,
            "writes_pmb_memory": False,
            "uses_configured_pmb_home_and_workspace": True,
            "fallback_role": "local_cli_read_wrapper_when_mcp_namespace_is_unavailable",
        },
    }


def pmb_prepare(message: str, *, top_k: int = 5) -> dict[str, Any]:
    clean_message = str(message or "").strip()
    if not clean_message:
        return {"schema": "local-pmb-memory.prepare.v1", "ok": False, "reason": "empty_message"}
    start = time.time()
    context = run_pmb(["prepare-context", clean_message], timeout=180)
    recall = pmb_recall(clean_message, top_k=top_k)
    context_text = ((context.get("stdout") or "") + "\n" + (context.get("stderr") or "")).strip()
    return {
        "schema": "local-pmb-memory.prepare.v1",
        "ok": bool(context.get("ok")) and bool(recall.get("ok")),
        "generated_at": now_iso(),
        "message_excerpt": clean_message[:240],
        "elapsed_ms": int((time.time() - start) * 1000),
        "context_available": bool(context.get("ok")) and "no context to inject" not in context_text.lower(),
        "context_preview": context_text[:6000],
        "recall": {
            "ok": recall.get("ok"),
            "has_matches": recall.get("has_matches"),
            "preview": recall.get("preview"),
            "elapsed_ms": recall.get("elapsed_ms"),
        },
        "raw_context": context,
        "contract": {
            "read_only": True,
            "writes_pmb_memory": False,
            "purpose": "Convenient work-start PMB context wrapper with the configured local PMB environment.",
            "native_mcp_preferred": True,
            "hub_pmb_tools_preferred_over_cli_when_current_turn_callable": True,
        },
    }


def run_powershell_json(script: str, timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "timed_out": True, "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "error": "powershell_json_parse_failed",
            "stdout_preview": raw[:2000],
            "stderr_preview": (proc.stderr or "")[:2000],
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "items": parsed if isinstance(parsed, list) else [parsed],
        "stderr_preview": (proc.stderr or "")[:2000],
    }


def pmb_daemon_processes() -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = foreach ($p in Get-CimInstance Win32_Process) {
  $cmd = [string]$p.CommandLine
  if (($p.Name -match '__PMB_DAEMON_PROCESS_NAME_REGEX__') -and ($cmd -match 'pmb\.cli daemon run|pmb daemon run')) {
    $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    [pscustomobject]@{
      pid = [int]$p.ProcessId
      parent_pid = [int]$p.ParentProcessId
      name = [string]$p.Name
      command_line = $cmd
      start_time = if ($gp -and $gp.StartTime) { $gp.StartTime.ToString('o') } else { '' }
      working_set_mb = if ($gp) { [math]::Round($gp.WorkingSet64 / 1MB, 1) } else { 0 }
    }
  }
}
$rows | ConvertTo-Json -Depth 4
""".replace("__PMB_DAEMON_PROCESS_NAME_REGEX__", PMB_DAEMON_PROCESS_NAME_REGEX)
    observed = run_powershell_json(script, timeout=30)
    rows = observed.get("items") if isinstance(observed.get("items"), list) else []
    child_parent_ids = {int(item.get("parent_pid") or 0) for item in rows}
    roots = [item for item in rows if int(item.get("pid") or 0) not in child_parent_ids]
    port_rows: list[dict[str, Any]] = []
    for item in roots:
        match = re.search(r"--port\s+(\d+)", str(item.get("command_line") or ""))
        port_rows.append({**item, "port": int(match.group(1)) if match else None})
    return {
        "ok": bool(observed.get("ok")),
        "count": len(rows),
        "root_count": len(roots),
        "roots": port_rows,
        "ports": sorted({item.get("port") for item in port_rows if item.get("port") is not None}),
        "observer": observed,
    }


def daemon_status() -> dict[str, Any]:
    result = run_pmb(["daemon", "status"], timeout=60)
    text = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
    lower = text.lower()
    running = bool(result.get("ok")) and "no daemon running" not in lower and "not running" not in lower
    warm = bool(running and ("warm=true" in lower or "ready" in lower))
    return {
        "schema": "local-pmb-memory.daemon_status.v1",
        "ok": bool(result.get("ok")),
        "running": running,
        "warm": warm,
        "status_preview": text[:4000],
        "raw": result,
        "policy": {
            "daemon_required_for_pmb_calls": True,
            "persistent_residency_required": False,
            "bind_host": "127.0.0.1",
            "mcp_access": "Hub PMB tools share the warm daemon and recover it on demand after an idle exit.",
            "idle_exit": "workspace config controls the idle timeout; zero means persistent residency",
            "fallback": "disabled in wrapper to avoid hidden heavy in-process PMB servers",
        },
    }


def daemon_start() -> dict[str, Any]:
    try:
        with exclusive_process_lock(PMB_DAEMON_LOCK) as lifecycle_lock:
            before = daemon_status()
            if before.get("running"):
                return {
                    "schema": "local-pmb-memory.daemon_start.v1",
                    "ok": True,
                    "applied": False,
                    "skipped": True,
                    "reason": "daemon_already_running",
                    "lifecycle_lock": lifecycle_lock,
                    "before": before,
                    "after": before,
                }
            result = run_pmb(["daemon", "start"], timeout=180)
            time.sleep(1.0)
            after = daemon_status()
            return {
                "schema": "local-pmb-memory.daemon_start.v1",
                "ok": bool(after.get("running")),
                "applied": True,
                "lifecycle_lock": lifecycle_lock,
                "start_result": result,
                "before": before,
                "after": after,
            }
    except TimeoutError as exc:
        return {
            "schema": "local-pmb-memory.daemon_start.v1",
            "ok": False,
            "applied": False,
            "reason": "daemon_lifecycle_lock_timeout",
            "error": str(exc),
        }


def daemon_restart() -> dict[str, Any]:
    try:
        with exclusive_process_lock(PMB_DAEMON_LOCK) as lifecycle_lock:
            result = run_pmb(["daemon", "restart"], timeout=240)
            time.sleep(1.0)
            after = daemon_status()
            return {
                "schema": "local-pmb-memory.daemon_restart.v1",
                "ok": bool(after.get("running")),
                "applied": True,
                "lifecycle_lock": lifecycle_lock,
                "restart_result": result,
                "after": after,
            }
    except TimeoutError as exc:
        return {
            "schema": "local-pmb-memory.daemon_restart.v1",
            "ok": False,
            "applied": False,
            "reason": "daemon_lifecycle_lock_timeout",
            "error": str(exc),
        }


def daemon_repair_registry(apply: bool = False) -> dict[str, Any]:
    """Rebuild PMB's daemon registry when a foreground run daemon is alive but unregistered."""
    before_status = daemon_status()
    before_processes = pmb_daemon_processes()
    roots = [item for item in before_processes.get("roots", []) if isinstance(item, dict)]
    needs_repair = (not before_status.get("running")) and bool(roots)
    if not needs_repair:
        return {
            "schema": "local-pmb-memory.daemon_registry_repair.v1",
            "ok": True,
            "applied": False,
            "skipped": True,
            "reason": "registry_already_consistent_or_no_daemon_process",
            "before_status": before_status,
            "before_processes": before_processes,
        }
    if not apply:
        return {
            "schema": "local-pmb-memory.daemon_registry_repair.v1",
            "ok": True,
            "applied": False,
            "dry_run": True,
            "would_stop_root_pids": [item.get("pid") for item in roots],
            "would_start": [str(PMB_EXE), "daemon", "start"],
            "before_status": before_status,
            "before_processes": before_processes,
            "guardrails": [
                "only stop pmb daemon root process trees matched by pmb_daemon_processes",
                "restart through official pmb daemon start so servers.json/token registry is populated",
                "do not use pmb daemon kill-all as routine repair",
            ],
        }
    stop_results = [{**item, "stop_result": stop_process_tree(item.get("pid"), apply=True)} for item in roots]
    time.sleep(1.0)
    start = daemon_start()
    time.sleep(1.0)
    after_status = daemon_status()
    after_processes = pmb_daemon_processes()
    return {
        "schema": "local-pmb-memory.daemon_registry_repair.v1",
        "ok": bool(after_status.get("running")) and int(after_processes.get("root_count") or 0) == 1,
        "applied": True,
        "stop_results": stop_results,
        "start": start,
        "before_status": before_status,
        "before_processes": before_processes,
        "after_status": after_status,
        "after_processes": after_processes,
        "dry_run_contract": {
            "kills_processes": True,
            "starts_processes": True,
            "writes_files": True,
            "changed_files_scope": [str(PMB_HOME / "servers.json"), str(PMB_HOME / "daemon.token"), str(PMB_HOME / "daemon.log")],
            "changes_codex_config": False,
        },
    }


def stop_process_tree(pid: Any, *, apply: bool) -> dict[str, Any]:
    try:
        numeric_pid = int(pid)
    except Exception:
        return {"ok": False, "pid": pid, "reason": "invalid_pid"}
    command = ["taskkill.exe", "/PID", str(numeric_pid), "/T", "/F"]
    if not apply:
        return {"ok": True, "dry_run": True, "pid": numeric_pid, "would_run": command}
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    return {
        "ok": proc.returncode == 0,
        "dry_run": False,
        "pid": numeric_pid,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:2000],
        "stderr": (proc.stderr or "").strip()[:2000],
    }


def cleanup_duplicate_daemons(apply: bool = False) -> dict[str, Any]:
    processes = pmb_daemon_processes()
    roots = [item for item in processes.get("roots", []) if isinstance(item, dict)]
    if len(roots) <= 1:
        return {
            "schema": "local-pmb-memory.daemon_cleanup.v1",
            "ok": True,
            "applied": False,
            "skipped": True,
            "reason": "daemon_singleton_already",
            "daemon_processes": processes,
        }
    status = daemon_status()
    status_pid_match = re.search(r"pid\s+(\d+)", str(status.get("status_preview") or ""))
    status_pid = int(status_pid_match.group(1)) if status_pid_match else None
    preferred_port = 8765
    keep = next((item for item in roots if int(item.get("port") or 0) == preferred_port), None)
    if keep is None and status_pid:
        keep = next((item for item in roots if int(item.get("pid") or 0) == status_pid), None)
    if keep is None:
        keep = sorted(roots, key=lambda item: str(item.get("start_time") or ""))[-1]
    keep_pid = int(keep.get("pid") or 0)
    selected = [item for item in roots if int(item.get("pid") or 0) != keep_pid]
    results = []
    for item in selected:
        results.append({**item, "stop_result": stop_process_tree(item.get("pid"), apply=apply)})
    after = pmb_daemon_processes() if apply else None
    return {
        "schema": "local-pmb-memory.daemon_cleanup.v1",
        "ok": all(bool(item.get("stop_result", {}).get("ok")) for item in results),
        "applied": bool(apply),
        "kept_pid": keep_pid,
        "kept": keep,
        "selected_count": len(selected),
        "selected": selected,
        "results": results,
        "before": processes,
        "after": after,
        "dry_run_contract": {
            "kills_processes": bool(apply),
            "writes_files": False,
            "changes_codex_config": False,
            "scope": "only pmb daemon root process trees matched by pmb_daemon_processes",
        },
    }


def daemon_ensure() -> dict[str, Any]:
    status = daemon_status()
    if status.get("running"):
        return {
            "schema": "local-pmb-memory.daemon_ensure.v1",
            "ok": True,
            "applied": False,
            "status": status,
        }
    started = daemon_start()
    return {
        "schema": "local-pmb-memory.daemon_ensure.v1",
        "ok": bool(started.get("ok")),
        "applied": bool(started.get("applied")),
        "status": started.get("after"),
        "start": started,
    }


def config_memory_mcp() -> dict[str, Any]:
    cfg = load_toml(CODEX_CONFIG)
    servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    retired_names = retired_memory_mcp_names()
    return {name: servers[name] for name in sorted(servers) if name in retired_names or "pmb" in name.lower()}


def remove_toml_table_blocks(text: str, names: set[str]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    output: list[str] = []
    removed: list[str] = []
    skip = False
    patterns = []
    for name in names:
        escaped = name.replace('"', '\\"')
        patterns.append((name, f"[mcp_servers.{name}]"))
        patterns.append((name, f'[mcp_servers."{escaped}"]'))
        patterns.append((name, f"[mcp_servers.{name}.env]"))
        patterns.append((name, f'[mcp_servers."{escaped}".env]'))
    for line in lines:
        stripped = line.strip()
        matched = next((name for name, marker in patterns if stripped == marker), None)
        if matched:
            skip = True
            if matched not in removed:
                removed.append(matched)
            continue
        if skip and stripped.startswith("["):
            skip = False
        if not skip:
            output.append(line)
    return "\n".join(output).rstrip() + "\n", removed


def ensure_local_pmb_block(text: str) -> tuple[str, bool]:
    text, removed = remove_toml_table_blocks(text, {LOCAL_PMB_MCP_NAME})
    return text.rstrip() + "\n\n" + LOCAL_PMB_MCP_BLOCK.rstrip() + "\n", True


def file_summary(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        stat = path.stat()
        item.update(
            {
                "is_dir": path.is_dir(),
                "size_bytes": stat.st_size if path.is_file() else sum(
                    p.stat().st_size for p in path.rglob("*") if p.is_file()
                ),
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file()]
            item["file_count"] = len(files)
    return item


def sqlite_table_counts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            tables: dict[str, int | str] = {}
            for (name,) in rows:
                try:
                    tables[str(name)] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                except Exception as exc:
                    tables[str(name)] = f"error:{type(exc).__name__}"
            return {"exists": True, "tables": tables}
    except Exception as exc:
        return {"exists": True, "error": f"{type(exc).__name__}: {exc}"}


def snapshot() -> dict[str, Any]:
    ensure_dirs()
    pmb_stats = run_pmb(["stats"], timeout=120)
    pmb_daemon = daemon_status()
    pmb_processes = pmb_daemon_processes()
    effective_daemon_running = bool(pmb_daemon.get("running")) or int(pmb_processes.get("root_count") or 0) == 1
    effective_daemon_warm = bool(pmb_daemon.get("warm")) or int(pmb_processes.get("root_count") or 0) == 1
    sources = historical_source_manifest()
    skills = []
    for root in SKILL_ROOTS:
        if root.exists():
            skill_files = list(root.glob("*/SKILL.md"))
            skills.append({"root": str(root), "exists": True, "skill_count": len(skill_files)})
        else:
            skills.append({"root": str(root), "exists": False, "skill_count": 0})
    return {
        "schema": "local-pmb-memory.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "root": str(ROOT),
        "pmb": {
            "home": str(PMB_HOME),
            "workspace": PMB_WORKSPACE,
            "venv": str(PMB_VENV),
            "exe": str(PMB_EXE),
            "exe_exists": PMB_EXE.exists(),
            "python_exists": PMB_PYTHON.exists(),
            "stats_ok": pmb_stats.get("ok"),
            "stats_preview": ((pmb_stats.get("stdout") or "") + "\n" + (pmb_stats.get("stderr") or ""))[:4000],
            "daemon": pmb_daemon,
            "daemon_processes": pmb_processes,
            "effective_daemon_running": effective_daemon_running,
            "effective_daemon_warm": effective_daemon_warm,
        },
        "memory_surface": memory_surface_snapshot(),
        "legacy_sources": sources,
        "skill_system": {
            "policy": "skills_are_independent; PMB stores at most skill index summaries, not SKILL.md bodies",
            "roots": skills,
        },
        "configured_memory_mcp": config_memory_mcp(),
        "migration_policy": {
            "mode": "current_pmb_with_explicit_historical_import_only",
            "no_long_term_mapping_layer": True,
            "legacy_sources_are_historical_only": True,
            "legacy_sources_never_participate_in_default_snapshot_health_or_recall": True,
        },
        "dry_run_contract": {
            "writes_files": False,
            "changes_codex_config": False,
            "starts_persistent_services": False,
            "decommissions_legacy": False,
        },
    }


def read_text_limited(path: Path, max_chars: int = 12000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[read_error:{type(exc).__name__}] {path}"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[truncated from {len(text)} chars]"
    return text


def is_sensitive_text(text: str) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in SECRET_PATTERNS)


@dataclass
class Candidate:
    source_system: str
    source_id: str
    kind: str
    text: str
    importance: float = 0.6
    privacy_class: str = "normal"
    action: str = "note"

    def to_record(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "source_id": self.source_id,
            "kind": self.kind,
            "text": self.text,
            "importance": self.importance,
            "privacy_class": self.privacy_class,
            "action": self.action,
            "content_sha256": sha256_text(self.text),
        }


def markdown_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    markdown_sources = [
        ("codex_memory_markdown", LEGACY_MEMORY_SOURCES["codex_memory_markdown"], 0.9, "manual_memory_summary"),
        ("bridge_codex_knowledge", LEGACY_MEMORY_SOURCES["bridge_codex_knowledge"], 0.8, "bridge_knowledge"),
        ("bridge_workspace_knowledge", LEGACY_MEMORY_SOURCES["bridge_workspace_knowledge"], 0.8, "workspace_knowledge"),
    ]
    for source, path, importance, kind in markdown_sources:
        if path.exists() and path.is_file():
            text = read_text_limited(path)
            privacy = "sensitive_review_required" if is_sensitive_text(text) else "normal"
            candidates.append(
                Candidate(
                    source_system=source,
                    source_id=str(path),
                    kind=kind,
                    text=f"[migrated from {source}]\n{ text }",
                    importance=importance,
                    privacy_class=privacy,
                    action="note",
                )
            )
    checkpoint_dir = LEGACY_MEMORY_SOURCES["bridge_checkpoints_memory_system"]
    if checkpoint_dir.exists():
        for path in sorted(checkpoint_dir.glob("*.md"))[-20:]:
            text = read_text_limited(path, max_chars=6000)
            candidates.append(
                Candidate(
                    source_system="bridge_checkpoints_memory_system",
                    source_id=str(path),
                    kind="memory_system_checkpoint",
                    text=f"[migrated memory-system checkpoint]\n{ text }",
                    importance=0.55,
                    privacy_class="sensitive_review_required" if is_sensitive_text(text) else "normal",
                )
            )
    return candidates


def memory_graph_candidates(db_path: Path, source: str) -> list[Candidate]:
    if not db_path.exists():
        return []
    candidates: list[Candidate] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "entities" in tables:
                for row in conn.execute("SELECT * FROM entities LIMIT 500"):
                    data = dict(row)
                    label = data.get("name") or data.get("id") or json.dumps(data, ensure_ascii=False)
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"entity:{data.get('id') or label}",
                            kind="entity",
                            text=f"[migrated memory graph entity]\n{json.dumps(data, ensure_ascii=False, sort_keys=True)}",
                            importance=0.55,
                        )
                    )
            if "observations" in tables:
                for row in conn.execute("SELECT * FROM observations LIMIT 1000"):
                    data = dict(row)
                    text = str(data.get("content") or data.get("text") or json.dumps(data, ensure_ascii=False))
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"observation:{data.get('id') or sha256_text(text)[:16]}",
                            kind="observation",
                            text=f"[migrated memory graph observation]\n{text}\n\nmetadata={json.dumps(data, ensure_ascii=False, sort_keys=True)}",
                            importance=0.6,
                            privacy_class="sensitive_review_required" if is_sensitive_text(text) else "normal",
                        )
                    )
            if "relations" in tables:
                for row in conn.execute("SELECT * FROM relations LIMIT 1000"):
                    data = dict(row)
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"relation:{data.get('id') or sha256_text(json.dumps(data, ensure_ascii=False))[:16]}",
                            kind="relation",
                            text=f"[migrated memory graph relation]\n{json.dumps(data, ensure_ascii=False, sort_keys=True)}",
                            importance=0.55,
                        )
                    )
    except Exception as exc:
        candidates.append(
            Candidate(
                source_system=source,
                source_id=str(db_path),
                kind="migration_error",
                text=f"Could not read memory graph {db_path}: {type(exc).__name__}: {exc}",
                importance=0.2,
                privacy_class="system_error",
            )
        )
    return candidates


def project_kb_candidates(db_path: Path, source: str) -> list[Candidate]:
    if not db_path.exists():
        return []
    candidates: list[Candidate] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "documents" in tables:
                for row in conn.execute("SELECT id, path, title, content, updated_at, source_type FROM documents LIMIT 1000"):
                    data = dict(row)
                    content = str(data.get("content") or "").strip()
                    if not content:
                        continue
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"document:{data.get('id') or sha256_text(content)[:16]}",
                            kind="legacy_project_document",
                            text=f"[migrated project knowledge document]\n{json.dumps(data, ensure_ascii=False, sort_keys=True)}",
                            importance=0.65,
                            privacy_class="sensitive_review_required" if is_sensitive_text(content) else "normal",
                        )
                    )
            if "conversation_checkpoints" in tables:
                for row in conn.execute("SELECT * FROM conversation_checkpoints LIMIT 1000"):
                    data = dict(row)
                    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"checkpoint:{data.get('id') or sha256_text(text)[:16]}",
                            kind="legacy_project_checkpoint",
                            text=f"[migrated project checkpoint]\n{text}",
                            importance=0.6,
                            privacy_class="sensitive_review_required" if is_sensitive_text(text) else "normal",
                        )
                    )
            if "project_configs" in tables:
                for row in conn.execute("SELECT * FROM project_configs LIMIT 1000"):
                    data = dict(row)
                    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
                    candidates.append(
                        Candidate(
                            source_system=source,
                            source_id=f"config:{data.get('id') or sha256_text(text)[:16]}",
                            kind="legacy_project_config",
                            text=f"[migrated project configuration evidence]\n{text}",
                            importance=0.55,
                            privacy_class="sensitive_review_required" if is_sensitive_text(text) else "normal",
                        )
                    )
    except Exception as exc:
        candidates.append(
            Candidate(
                source_system=source,
                source_id=str(db_path),
                kind="migration_error",
                text=f"Could not read legacy project knowledge {db_path}: {type(exc).__name__}: {exc}",
                importance=0.2,
                privacy_class="system_error",
            )
        )
    return candidates


def chroma_candidates(db_path: Path, source: str) -> list[Candidate]:
    if not db_path.exists():
        return []
    candidates: list[Candidate] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "embedding_metadata" not in tables or "embeddings" not in tables:
                return []
            rows = conn.execute(
                """
                SELECT e.embedding_id, e.created_at, m.string_value AS document
                FROM embeddings e
                JOIN embedding_metadata m ON m.id = e.id
                WHERE m.key = 'chroma:document'
                  AND m.string_value IS NOT NULL
                ORDER BY e.id
                LIMIT 2000
                """
            ).fetchall()
            for row in rows:
                document = str(row["document"] or "").strip()
                if not document:
                    continue
                metadata_rows = conn.execute(
                    """
                    SELECT key, string_value, int_value, float_value, bool_value
                    FROM embedding_metadata
                    WHERE id = (SELECT id FROM embeddings WHERE embedding_id = ? LIMIT 1)
                      AND key != 'chroma:document'
                    ORDER BY key
                    LIMIT 80
                    """,
                    (row["embedding_id"],),
                ).fetchall()
                metadata: dict[str, Any] = {}
                for meta in metadata_rows:
                    value = meta["string_value"]
                    if value is None:
                        value = meta["int_value"]
                    if value is None:
                        value = meta["float_value"]
                    if value is None:
                        value = meta["bool_value"]
                    metadata[str(meta["key"])] = value
                body = {
                    "source": source,
                    "embedding_id": row["embedding_id"],
                    "created_at": row["created_at"],
                    "metadata": metadata,
                    "document": document,
                }
                candidates.append(
                    Candidate(
                        source_system=source,
                        source_id=f"chroma:{row['embedding_id'] or sha256_text(document)[:16]}",
                        kind="legacy_vector_memory_document",
                        text=f"[migrated legacy vector/chroma memory]\n{json.dumps(body, ensure_ascii=False, sort_keys=True)}",
                        importance=0.6,
                        privacy_class="sensitive_review_required" if is_sensitive_text(document) else "normal",
                    )
                )
    except Exception as exc:
        candidates.append(
            Candidate(
                source_system=source,
                source_id=str(db_path),
                kind="migration_error",
                text=f"Could not read legacy chroma memory {db_path}: {type(exc).__name__}: {exc}",
                importance=0.2,
                privacy_class="system_error",
            )
        )
    return candidates


def skill_index_candidates() -> list[Candidate]:
    rows: list[dict[str, str]] = []
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            text = read_text_limited(skill_file, max_chars=2000)
            name = skill_file.parent.name
            desc = ""
            for line in text.splitlines():
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"')
                    break
            rows.append({"name": name, "path": str(skill_file), "description": desc[:300]})
    if not rows:
        return []
    summary = {
        "policy": "Skill bodies are not migrated into PMB. This is only an index so memory recall can remind the agent which independent skill system to inspect.",
        "count": len(rows),
        "skills": rows[:400],
    }
    return [
        Candidate(
            source_system="skill_system_index",
            source_id="skill-index-summary",
            kind="skill_index_summary",
            text=f"[skill system index summary - not skill body]\n{json.dumps(summary, ensure_ascii=False, indent=2)}",
            importance=0.5,
            privacy_class="index_only",
        )
    ]


def build_candidates(include_sensitive: bool = False) -> list[Candidate]:
    candidates = markdown_candidates()
    if path := LEGACY_MEMORY_SOURCES.get("project_kb_index"):
        candidates.extend(project_kb_candidates(path, "project_kb_index"))
    for source in ["memory_graph_codex", "memory_graph_reasonix"]:
        if path := LEGACY_MEMORY_SOURCES.get(source):
            candidates.extend(memory_graph_candidates(path, source))
    for source in [
        "chroma_memory",
        "vector_memory_codex",
        "vector_memory_shared",
        "vector_memory_reasonix",
    ]:
        if path := LEGACY_MEMORY_SOURCES.get(source):
            candidates.extend(chroma_candidates(path, source))
    candidates.extend(skill_index_candidates())
    seen: set[str] = set()
    deduped: list[Candidate] = []
    for item in candidates:
        key = sha256_text(item.text)
        if key in seen:
            continue
        seen.add(key)
        if item.privacy_class == "sensitive_review_required" and not include_sensitive:
            continue
        deduped.append(item)
    return deduped


def import_dry_run(include_sensitive: bool = False) -> dict[str, Any]:
    ensure_dirs()
    ledger = load_migration_ledger()
    imported = set((ledger.get("imported_source_ids") or {}).keys())
    candidates = build_candidates(include_sensitive=include_sensitive)
    records = [item.to_record() for item in candidates if item.source_id not in imported]
    report = {
        "schema": "local-pmb-memory.import_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "target": {"pmb_home": str(PMB_HOME), "workspace": PMB_WORKSPACE},
        "include_sensitive": include_sensitive,
        "record_count": len(records),
        "already_imported_count": len([item for item in candidates if item.source_id in imported]),
        "by_source": {},
        "by_kind": {},
        "records": records,
        "dry_run_contract": {
            "writes_pmb_memory": False,
            "changes_codex_config": False,
            "decommissions_legacy": False,
        },
    }
    for record in records:
        report["by_source"][record["source_system"]] = report["by_source"].get(record["source_system"], 0) + 1
        report["by_kind"][record["kind"]] = report["by_kind"].get(record["kind"], 0) + 1
    out = IMPORT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-pmb-import-plan.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["plan_path"] = str(out)
    return report


def import_apply(include_sensitive: bool = False, limit: int | None = None) -> dict[str, Any]:
    ensure_dirs()
    ledger = load_migration_ledger()
    imported = set((ledger.get("imported_source_ids") or {}).keys())
    candidates = build_candidates(include_sensitive=include_sensitive)
    skipped_already_imported = [item for item in candidates if item.source_id in imported]
    candidates = [item for item in candidates if item.source_id not in imported]
    if limit is not None:
        candidates = candidates[:limit]
    results: list[dict[str, Any]] = []
    for item in candidates:
        chunks = split_text_for_pmb_note(item.text, max_chars=PMB_NOTE_MAX_TEXT_CHARS)
        chunk_results: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_text = chunk
            if len(chunks) > 1:
                chunk_text = (
                    f"[chunk {index}/{len(chunks)} from {item.source_system} source_id={item.source_id}]\n"
                    f"{chunk}"
                )
            command = ["note", chunk_text, "--importance", str(item.importance)]
            chunk_result = run_pmb(command, timeout=180)
            chunk_results.append(
                {
                    "ok": chunk_result.get("ok"),
                    "returncode": chunk_result.get("returncode"),
                    "stdout_preview": (chunk_result.get("stdout") or "")[:1000],
                    "stderr_preview": (chunk_result.get("stderr") or "")[:1000],
                }
            )
            if not chunk_result.get("ok"):
                break
        result = {
            "ok": bool(chunk_results) and all(row.get("ok") for row in chunk_results),
            "returncode": 0 if chunk_results and all(row.get("ok") for row in chunk_results) else 1,
            "stdout": json.dumps({"chunk_count": len(chunks), "chunk_results": chunk_results}, ensure_ascii=False),
            "stderr": "",
        }
        results.append(
            {
                "source_system": item.source_system,
                "source_id": item.source_id,
                "kind": item.kind,
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
                "chunk_count": len(chunks),
                "stdout_preview": (result.get("stdout") or "")[:1000],
                "stderr_preview": (result.get("stderr") or "")[:1000],
            }
        )
        if not result.get("ok"):
            break
    mark_imported(candidates, results)
    report = {
        "schema": "local-pmb-memory.import_apply.v1",
        "ok": all(row.get("ok") for row in results),
        "generated_at": now_iso(),
        "applied_count": sum(1 for row in results if row.get("ok")),
        "attempted_count": len(results),
        "skipped_already_imported_count": len(skipped_already_imported),
        "results": results,
    }
    out = IMPORT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-pmb-import-apply.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(out)
    return report


def split_text_for_pmb_note(text: str, max_chars: int = PMB_NOTE_MAX_TEXT_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.splitlines(keepends=True):
        if len(paragraph) > max_chars:
            if current:
                chunks.append("".join(current).strip())
                current = []
                current_len = 0
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start:start + max_chars].strip())
            continue
        if current_len + len(paragraph) > max_chars and current:
            chunks.append("".join(current).strip())
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += len(paragraph)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def cutover_plan() -> dict[str, Any]:
    cfg = load_toml(CODEX_CONFIG)
    servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    retired_names = retired_memory_mcp_names()
    legacy_present = sorted(name for name in retired_names if name in servers)
    pmb_present = LOCAL_PMB_MCP_NAME in servers
    validation = validate()
    return {
        "schema": "local-pmb-memory.cutover_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(CODEX_CONFIG),
        "would_remove_legacy_memory_mcp": legacy_present,
        "would_add_or_replace": "" if LOCAL_PMB_HUB_MANAGED else LOCAL_PMB_MCP_NAME,
        "would_remove_desktop_local_pmb": LOCAL_PMB_HUB_MANAGED and pmb_present,
        "local_pmb_present_now": pmb_present,
        "local_pmb_registration_mode": "hub_managed" if LOCAL_PMB_HUB_MANAGED else "desktop_native",
        "validation_ok": validation.get("ok"),
        "validation_summary": {
            "failure_count": validation.get("failure_count"),
            "recall_checks": [
                {"query": row.get("query"), "ok": row.get("ok"), "elapsed_ms": row.get("elapsed_ms")}
                for row in validation.get("recall_checks", [])
            ],
        },
        "preconditions": [
            "full import reviewed/applied",
            "sensitive records policy accepted",
            "validate ok",
            "config backup created during apply",
            "Codex Desktop restart after cutover",
        ],
        "dry_run_contract": {
            "writes_files": False,
            "changes_codex_config": False,
            "decommissions_legacy": False,
        },
    }


def cutover_apply(confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {
            "schema": "local-pmb-memory.cutover_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "confirmation_required",
            "required_flag": "--confirm-cutover",
        }
    validation = validate()
    if not validation.get("ok"):
        return {
            "schema": "local-pmb-memory.cutover_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "validation_failed",
            "validation": validation,
        }
    if not CODEX_CONFIG.exists():
        return {"schema": "local-pmb-memory.cutover_apply.v1", "ok": False, "applied": False, "reason": "config_missing"}
    backup_result = create_routed_backup(
        [str(CODEX_CONFIG)],
        remark="local-pmb-cutover",
        purpose="backup Codex config before local PMB cutover",
        category="codex-config",
        trigger="local-pmb-memory.cutover_apply",
    )
    if not backup_result.get("ok"):
        return {
            "schema": "local-pmb-memory.cutover_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "backup_failed",
            "backup_result": backup_result,
        }
    backup_items = backup_result.get("items") if isinstance(backup_result.get("items"), list) else []
    backup = Path(str(backup_items[0].get("backup_path") or "")) if backup_items else Path()
    if not backup.exists():
        return {
            "schema": "local-pmb-memory.cutover_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "backup_missing_after_create",
            "backup_result": backup_result,
        }
    original = CODEX_CONFIG.read_text(encoding="utf-8-sig")
    rewritten, removed = remove_toml_table_blocks(original, retired_memory_mcp_names() | {LOCAL_PMB_MCP_NAME})
    added_or_replaced = ""
    if not LOCAL_PMB_HUB_MANAGED:
        rewritten, _ = ensure_local_pmb_block(rewritten)
        added_or_replaced = LOCAL_PMB_MCP_NAME
    CODEX_CONFIG.write_text(rewritten, encoding="utf-8", newline="\n")
    try:
        tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        shutil.copy2(backup, CODEX_CONFIG)
        return {
            "schema": "local-pmb-memory.cutover_apply.v1",
            "ok": False,
            "applied": False,
            "reason": f"toml_validation_failed_restored_backup: {type(exc).__name__}: {exc}",
            "backup": str(backup),
        }
    return {
        "schema": "local-pmb-memory.cutover_apply.v1",
        "ok": True,
        "applied": True,
        "backup": str(backup),
        "removed": removed,
        "added_or_replaced": added_or_replaced,
        "local_pmb_registration_mode": "hub_managed" if LOCAL_PMB_HUB_MANAGED else "desktop_native",
        "restart_required": True,
        "note": "Restart Codex Desktop before expecting the current session tool surface to change.",
    }


def pmb_compat_doctor(*, full_lance: bool = True) -> dict[str, Any]:
    return pmb_compatibility.doctor(
        PMB_PYTHON,
        PMB_HOME,
        PMB_WORKSPACE,
        full_lance=full_lance,
    )


def pmb_compat_repair_plan() -> dict[str, Any]:
    return pmb_compatibility.repair_plan(PMB_PYTHON, PMB_HOME, PMB_WORKSPACE)


def pmb_compat_apply(*, apply: bool) -> dict[str, Any]:
    if not apply:
        plan = pmb_compat_repair_plan()
        return {
            "schema": "local-pmb-memory.compat_apply.v1",
            "ok": True,
            "applied": False,
            "reason": "explicit_apply_required",
            "plan": plan,
        }
    before = pmb_compat_doctor(full_lance=True)
    if before.get("ok"):
        return {
            "schema": "local-pmb-memory.compat_apply.v1",
            "ok": True,
            "applied": False,
            "reason": "compatibility_already_healthy",
            "before": before,
        }
    metadata = pmb_compatibility.package_metadata(PMB_PYTHON)
    source_paths = [
        str(metadata.get(key))
        for key in ("search_path", "workspace_path", "daemon_path")
        if metadata.get(key)
    ]
    if not metadata.get("ok") or len(source_paths) != 3:
        return {
            "schema": "local-pmb-memory.compat_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "package_metadata_failed",
            "metadata": metadata,
        }
    backup_result = create_routed_backup(
        source_paths,
        remark="pmb-compatibility-pre-apply",
        purpose="backup PMB package sources before bounded compatibility repair",
        category="memory-system",
        trigger="local-pmb-memory.pmb-compat-apply",
    )
    if not backup_result.get("ok"):
        return {
            "schema": "local-pmb-memory.compat_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "backup_failed",
            "backup": backup_result,
        }
    package_apply = pmb_compatibility.apply_package_fixes(PMB_PYTHON, apply=True)
    if not package_apply.get("ok"):
        return {
            "schema": "local-pmb-memory.compat_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "package_patch_failed",
            "backup": backup_result,
            "before": before,
            "package_apply": package_apply,
        }
    issue_codes = {
        str(item.get("code"))
        for item in before.get("issues", [])
        if isinstance(item, dict)
    }
    search_changed = any(
        item.get("target") == "search" and item.get("changed")
        for item in package_apply.get("changes", [])
        if isinstance(item, dict)
    )
    daemon_changed = any(
        item.get("target") == "daemon" and item.get("changed")
        for item in package_apply.get("changes", [])
        if isinstance(item, dict)
    )
    reindex_required = search_changed or bool(
        {"pmb_quick_index_mismatch", "pmb_lance_index_mismatch"} & issue_codes
    )
    reindex_result = {"ok": True, "skipped": True, "reason": "indexes_already_consistent"}
    if reindex_required:
        reindex_result = run_pmb(["reindex"], timeout=1800)
        if not reindex_result.get("ok"):
            return {
                "schema": "local-pmb-memory.compat_apply.v1",
                "ok": False,
                "applied": bool(package_apply.get("applied")),
                "reason": "reindex_failed",
                "backup": backup_result,
                "before": before,
                "package_apply": package_apply,
                "reindex": reindex_result,
            }
    daemon = daemon_restart() if reindex_required or daemon_changed else daemon_status()
    after = pmb_compat_doctor(full_lance=True)
    return {
        "schema": "local-pmb-memory.compat_apply.v1",
        "ok": bool(package_apply.get("ok")) and bool(reindex_result.get("ok")) and bool(daemon.get("ok")) and bool(after.get("ok")),
        "applied": bool(package_apply.get("applied")) or reindex_required,
        "generated_at": now_iso(),
        "backup": backup_result,
        "before": before,
        "package_apply": package_apply,
        "reindex": reindex_result,
        "daemon_restart": daemon,
        "after": after,
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    compatibility = pmb_compat_doctor(full_lance=False)
    if not compatibility.get("ok"):
        issues.append(
            {
                "severity": "risk",
                "code": "pmb_compatibility_drift",
                "detail": {
                    "issues": compatibility.get("issues", []),
                    "policy": "PMB upgrades and reindex operations must preserve package compatibility and SQLite/BM25 count consistency.",
                    "repair_plan": "python _bridge\\local_pmb_memory.py pmb-compat-repair-plan",
                },
            }
        )
    if not snap["pmb"]["exe_exists"]:
        issues.append({"severity": "blocker", "code": "pmb_exe_missing", "detail": snap["pmb"]})
    if not snap["pmb"]["stats_ok"]:
        issues.append({"severity": "risk", "code": "pmb_stats_failed", "detail": snap["pmb"]["stats_preview"]})
    daemon = snap["pmb"].get("daemon") if isinstance(snap["pmb"].get("daemon"), dict) else {}
    processes = snap["pmb"].get("daemon_processes") if isinstance(snap["pmb"].get("daemon_processes"), dict) else {}
    effective_running = bool(snap["pmb"].get("effective_daemon_running"))
    if not effective_running:
        issues.append(
            {
                "severity": "risk",
                "code": "pmb_daemon_not_running",
                "detail": {
                    "status_preview": daemon.get("status_preview", ""),
                    "policy": "PMB memory body should stay warm; Codex uses lightweight stdio proxy to this daemon.",
                },
            }
        )
    if not daemon.get("running") and int(processes.get("root_count") or 0) == 1:
        issues.append(
            {
                "severity": "advisory",
                "code": "pmb_daemon_registry_stale",
                "detail": {
                    "status_preview": daemon.get("status_preview", ""),
                    "daemon_processes": processes,
                    "policy": "do not auto-start another daemon when one valid local daemon process is already running",
                },
            }
        )
    if int(processes.get("root_count") or 0) > 1:
        issues.append(
            {
                "severity": "risk",
                "code": "pmb_duplicate_daemons",
                "detail": {
                    "root_count": processes.get("root_count"),
                    "ports": processes.get("ports"),
                    "roots": processes.get("roots"),
                    "policy": "PMB memory body must be a singleton warm daemon; proxy processes may multiply, daemon processes must not.",
                },
            }
        )
    surface = snap.get("memory_surface") if isinstance(snap.get("memory_surface"), dict) else {}
    manifest = surface.get("manifest") if isinstance(surface.get("manifest"), dict) else {}
    profile = surface.get("user_profile") if isinstance(surface.get("user_profile"), dict) else {}
    policy = surface.get("policy") if isinstance(surface.get("policy"), dict) else {}
    if not manifest.get("ok"):
        issues.append(
            {
                "severity": "risk",
                "code": "memory_manifest_invalid",
                "detail": {
                    "path": manifest.get("path"),
                    "issues": manifest.get("issues"),
                    "policy": "memory_manifest.json is the unified machine entrypoint for memory routing",
                },
            }
        )
    if not profile.get("ok"):
        issues.append(
            {
                "severity": "risk",
                "code": "user_profile_invalid",
                "detail": {
                    "path": profile.get("path"),
                    "issues": profile.get("issues"),
                    "policy": "user_profile.json stores non-secret user preferences with source, confidence, and temporal validity",
                },
            }
        )
    if not policy.get("ok"):
        issues.append(
            {
                "severity": "risk",
                "code": "memory_policy_invalid",
                "detail": {
                    "path": policy.get("path"),
                    "issues": policy.get("issues"),
                    "policy": "memory_policy.json defines write and privacy controls for normal memory",
                },
            }
        )
    retired_names = retired_memory_mcp_names()
    legacy_active = [name for name in snap["configured_memory_mcp"] if name in retired_names]
    if legacy_active:
        issues.append(
            {
                "severity": "risk",
                "code": "legacy_memory_mcp_still_configured",
                "detail": {
                    "configured": legacy_active,
                    "policy": "after migration validation, default memory MCP should be local-pmb-memory only",
                },
            }
        )
    return {
        "schema": "local-pmb-memory.doctor.v1",
        "ok": not any(item["severity"] in {"blocker", "risk"} for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "pmb_compatibility": compatibility,
        "snapshot": snap,
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap)
    actions: list[dict[str, Any]] = []
    for issue in doc["issues"]:
        code = issue["code"]
        if code == "pmb_exe_missing":
            actions.append(
                {
                    "id": "install_pmb_isolated_venv",
                    "dry_run_only": True,
                    "would_run": [
                        "python -m venv _bridge\\venvs\\pmb-memory",
                        "_bridge\\venvs\\pmb-memory\\Scripts\\python.exe -m pip install pmb-ai==1.2.2",
                    ],
                }
            )
        elif code == "pmb_stats_failed":
            actions.append(
                {
                    "id": "inspect_pmb_runtime",
                    "dry_run_only": True,
                    "would_run": ["pmb doctor", "pmb init", "pmb stats"],
                }
            )
        elif code == "pmb_daemon_not_running":
            actions.append(
                {
                    "id": "start_local_pmb_daemon",
                    "dry_run_only": False,
                    "safe_apply_command": "python _bridge\\local_pmb_memory.py daemon-ensure",
                    "would_change": "start one local PMB daemon bound to 127.0.0.1 for warm memory recall",
                    "guardrails": [
                        "do not use pmb daemon kill-all as routine repair",
                        "do not expose PMB daemon on 0.0.0.0",
                        "Codex MCP wrapper uses lightweight pmb mcp proxy with --no-fallback",
                    ],
                }
            )
        elif code == "pmb_duplicate_daemons":
            actions.append(
                {
                    "id": "restart_local_pmb_daemon_singleton",
                    "dry_run_only": False,
                    "safe_apply_command": "python _bridge\\local_pmb_memory.py daemon-restart",
                    "safe_cleanup_command": "python _bridge\\local_pmb_memory.py daemon-cleanup-duplicates --apply",
                    "would_change": "ask PMB to restart its daemon registry and converge to one warm local daemon",
                    "guardrails": [
                        "prefer pmb daemon restart over manual taskkill",
                        "verify daemon_processes.root_count == 1 afterward",
                        "do not kill unrelated Python processes by name",
                    ],
                }
            )
        elif code == "pmb_compatibility_drift":
            actions.append(
                {
                    "id": "repair_pmb_package_and_index_compatibility",
                    "dry_run_only": False,
                    "safe_plan_command": "python _bridge\\local_pmb_memory.py pmb-compat-repair-plan",
                    "safe_apply_command": "python _bridge\\local_pmb_memory.py pmb-compat-apply --apply",
                    "would_change": "apply exact-signature PMB compatibility fixes, rebuild indexes only when required, and restart the governed daemon",
                    "guardrails": [
                        "backup package sources before apply",
                        "stop when package source signatures changed after an upgrade",
                        "verify SQLite, LanceDB, and BM25 counts after repair",
                    ],
                }
            )
        elif code == "pmb_daemon_registry_stale":
            actions.append(
                {
                    "id": "repair_local_pmb_daemon_registry",
                    "dry_run_only": False,
                    "safe_apply_command": "python _bridge\\local_pmb_memory.py daemon-repair-registry --apply",
                    "would_change": "replace an unregistered foreground PMB daemon with an official registered pmb daemon start instance",
                    "guardrails": [
                        "only stop PMB daemon root process trees matched by pmb_daemon_processes",
                        "do not kill unrelated Python processes by name",
                        "do not use pmb daemon kill-all as routine repair",
                    ],
                }
            )
        elif code == "legacy_memory_mcp_still_configured":
            actions.append(
                {
                    "id": "cutover_decommission_legacy_memory_mcps",
                    "dry_run_only": True,
                    "preconditions": [
                        "import-apply completed",
                        "validate passes recall smoke checks",
                        "config backup exists",
                    ],
                    "would_change": "remove legacy memory MCP entries from active config and add one local-pmb-memory entry",
                }
            )
        elif code in {"memory_manifest_invalid", "user_profile_invalid", "memory_policy_invalid"}:
            actions.append(
                {
                    "id": "repair_memory_surface_entrypoints",
                    "dry_run_only": True,
                    "would_change": "restore or edit memory_manifest/profile/policy from backup or approved schema",
                    "guardrails": [
                        "backup_before_edit",
                        "do_not_store_secrets_in_normal_memory",
                        "keep skills independent from PMB memory body",
                    ],
                }
            )
    return {
        "schema": "local-pmb-memory.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "doctor_ok": doc["ok"],
        "actions": actions,
        "dry_run_contract": {
            "writes_files": False,
            "changes_codex_config": False,
            "decommissions_legacy": False,
        },
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    compatibility = pmb_compat_doctor(full_lance=True)
    recall_checks: list[dict[str, Any]] = []
    for query in ["桥接系统", "Codex 配置漂移", "ClientModLoader", "技能系统独立于记忆", "memory_manifest 用户画像"]:
        start = time.time()
        result = pmb_recall(query, top_k=5)
        recall_checks.append(
            {
                "query": query,
                "ok": result.get("ok"),
                "elapsed_ms": int((time.time() - start) * 1000),
                "has_matches": result.get("has_matches"),
                "preview": str(result.get("preview") or "")[:2000],
            }
        )
    failures = [row for row in recall_checks if not row.get("ok")]
    surface = snap.get("memory_surface") if isinstance(snap.get("memory_surface"), dict) else {}
    surface_checks = [
        {"name": "memory_manifest_valid", "ok": bool((surface.get("manifest") or {}).get("ok")), "detail": (surface.get("manifest") or {}).get("path", "")},
        {"name": "user_profile_valid", "ok": bool((surface.get("user_profile") or {}).get("ok")), "detail": (surface.get("user_profile") or {}).get("path", "")},
        {"name": "user_profile_guidance_available", "ok": bool(((surface.get("user_profile") or {}).get("guidance") or {}).get("ok")), "detail": (surface.get("user_profile") or {}).get("path", "")},
        {"name": "memory_policy_valid", "ok": bool((surface.get("policy") or {}).get("ok")), "detail": (surface.get("policy") or {}).get("path", "")},
        {"name": "manifest_schema_file_exists", "ok": bool(((surface.get("schemas") or {}).get("manifest") or {}).get("exists")), "detail": str(MEMORY_MANIFEST_SCHEMA)},
        {"name": "user_profile_schema_file_exists", "ok": bool(((surface.get("schemas") or {}).get("user_profile") or {}).get("exists")), "detail": str(USER_PROFILE_SCHEMA)},
    ]
    prepare_smoke = pmb_prepare("PMB memory preflight smoke for local read wrapper", top_k=3)
    surface_checks.append(
        {
            "name": "pmb_prepare_wrapper_available",
            "ok": bool(prepare_smoke.get("ok")),
            "detail": "local_pmb_memory.py pmb-prepare",
        }
    )
    surface_checks.append(
        {
            "name": "pmb_package_and_index_compatibility",
            "ok": bool(compatibility.get("ok")),
            "detail": compatibility.get("issues", []),
        }
    )
    surface_failures = [row for row in surface_checks if not row.get("ok")]
    return {
        "schema": "local-pmb-memory.validate.v1",
        "ok": bool(snap["pmb"]["stats_ok"]) and not failures and not surface_failures,
        "generated_at": now_iso(),
        "stats_ok": snap["pmb"]["stats_ok"],
        "pmb_compatibility": compatibility,
        "surface_checks": surface_checks,
        "recall_checks": recall_checks,
        "failure_count": len(failures) + len(surface_failures),
    }


def metrics() -> dict[str, Any]:
    snap = snapshot()
    source_count = len(snap["legacy_sources"])
    retired_names = retired_memory_mcp_names()
    configured_legacy_count = sum(1 for name in snap["configured_memory_mcp"] if name in retired_names)
    pmb_size = file_summary(PMB_HOME)
    return {
        "schema": "local-pmb-memory.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "pmb_home_size_bytes": pmb_size.get("size_bytes", 0),
        "pmb_home_file_count": pmb_size.get("file_count", 0),
        "legacy_source_count": source_count,
        "configured_legacy_mcp_count": configured_legacy_count,
        "pmb_stats_ok": snap["pmb"]["stats_ok"],
        "pmb_daemon_running": bool(snap["pmb"].get("effective_daemon_running")),
        "pmb_daemon_warm": bool(snap["pmb"].get("effective_daemon_warm")),
        "pmb_daemon_root_count": int((snap["pmb"].get("daemon_processes") or {}).get("root_count") or 0),
        "memory_manifest_ok": bool(((snap.get("memory_surface") or {}).get("manifest") or {}).get("ok")),
        "memory_namespace_count": int(((snap.get("memory_surface") or {}).get("manifest") or {}).get("namespace_count") or 0),
        "user_profile_ok": bool(((snap.get("memory_surface") or {}).get("user_profile") or {}).get("ok")),
        "user_profile_fact_count": int(((snap.get("memory_surface") or {}).get("user_profile") or {}).get("fact_count") or 0),
        "user_profile_guidance_count": int(((((snap.get("memory_surface") or {}).get("user_profile") or {}).get("guidance") or {}).get("selected_fact_count")) or 0),
        "memory_policy_ok": bool(((snap.get("memory_surface") or {}).get("policy") or {}).get("ok")),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Local PMB memory migration and governance")
    parser.add_argument(
        "command",
        choices=[
            "snapshot",
            "doctor",
            "repair-plan",
            "validate",
            "metrics",
            "pmb-compat-doctor",
            "pmb-compat-repair-plan",
            "pmb-compat-apply",
            "pmb-recall",
            "pmb-prepare",
            "import-dry-run",
            "import-apply",
            "cutover-plan",
            "cutover-apply",
            "ledger-rebuild",
            "daemon-status",
            "daemon-start",
            "daemon-ensure",
            "daemon-restart",
            "daemon-repair-registry",
            "daemon-cleanup-duplicates",
        ],
    )
    parser.add_argument("--include-sensitive", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--query", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--confirm-cutover", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "repair-plan":
        payload = repair_plan()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "metrics":
        payload = metrics()
    elif args.command == "pmb-compat-doctor":
        payload = pmb_compat_doctor(full_lance=True)
    elif args.command == "pmb-compat-repair-plan":
        payload = pmb_compat_repair_plan()
    elif args.command == "pmb-compat-apply":
        payload = pmb_compat_apply(apply=bool(args.apply))
    elif args.command == "pmb-recall":
        payload = pmb_recall(args.query or args.message, top_k=args.top_k)
    elif args.command == "pmb-prepare":
        payload = pmb_prepare(args.message or args.query, top_k=args.top_k)
    elif args.command == "import-dry-run":
        payload = import_dry_run(include_sensitive=args.include_sensitive)
    elif args.command == "import-apply":
        payload = import_apply(include_sensitive=args.include_sensitive, limit=args.limit)
    elif args.command == "cutover-plan":
        payload = cutover_plan()
    elif args.command == "cutover-apply":
        payload = cutover_apply(confirm=args.confirm_cutover)
    elif args.command == "daemon-status":
        payload = daemon_status()
    elif args.command == "daemon-start":
        payload = daemon_start()
    elif args.command == "daemon-ensure":
        payload = daemon_ensure()
    elif args.command == "daemon-restart":
        payload = daemon_restart()
    elif args.command == "daemon-repair-registry":
        payload = daemon_repair_registry(apply=bool(args.apply))
    elif args.command == "daemon-cleanup-duplicates":
        payload = cleanup_duplicate_daemons(apply=bool(args.apply))
    else:
        payload = rebuild_ledger_from_reports()
    printable = payload
    if args.command == "import-dry-run" and isinstance(payload, dict) and "records" in payload:
        printable = dict(payload)
        records = printable.pop("records", [])
        printable["records_omitted_from_stdout"] = len(records)
        printable["note"] = "Full records were written to plan_path."
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
