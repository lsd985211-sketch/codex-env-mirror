#!/usr/bin/env python3
"""Governance checks and explicitly confirmed owner actions for Codex memory.

This module keeps memory from becoming a passive archive. It does not promote
or rewrite memories by itself; it reports whether long-lived system work has a
usable memory surface, pending candidate notes, and validation hooks. Mutating
commands remain opt-in and require their exact confirmation flags.
"""

# ruff: noqa: E402 - imports intentionally follow the workspace path bootstrap and fallback definitions.

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

try:
    from _bridge.local_pmb_memory import build_user_profile_guidance, user_profile_responsibility_gate
except Exception:
    def build_user_profile_guidance(profile_payload: dict[str, Any], *, max_items: int = 12) -> dict[str, Any]:
        return {
            "schema": "codex-user-profile.guidance.v1",
            "ok": False,
            "active_fact_count": 0,
            "selected_fact_count": 0,
            "selected_fact_ids": [],
            "categories": {},
            "action_guidance": [],
            "error": "guidance_builder_unavailable",
        }

    def user_profile_responsibility_gate(profile_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "codex-user-profile.responsibility_gate.v1",
            "ok": False,
            "profile_owned_fact_ids": [],
            "rule_owned_fact_ids": [],
            "needs_review_fact_ids": [],
            "error": "responsibility_gate_unavailable",
        }

from _bridge import memory_work_notes
from _bridge import memory_surface_snapshot as memory_surface
from _bridge import memory_recall_verification
from _bridge import memory_review_summary
from _bridge import memory_candidate_notes
from _bridge.memory_iteration_owner import (
    DEFAULT_MEMORY_INDEX as ITERATION_MEMORY_INDEX,
    apply_iteration_candidate as _apply_iteration_candidate,
    apply_iteration_candidates as _apply_iteration_candidates,
    plan_iteration_candidate as _plan_iteration_candidate,
    recall_iteration_candidate as _recall_iteration_candidate,
    validate_iteration_candidate as _validate_iteration_candidate,
)
from _bridge.memory_note_analysis import (
    drift_hits,
    highest_severity,
    normalize_memory_text,
    recommend_consolidation_theme,
    recommend_note_destination,
    sensitive_hits,
    stable_point_candidates,
)
from _bridge import memory_pmb_organize
from _bridge import memory_pmb_workspaces
from _bridge.platform_paths import codex_home, resource_library_root
from _bridge.shared.backup_router import create_backup as create_routed_backup
from _bridge.user_profile_candidates import profile_signal_sources
from _bridge.shared.json_cli import now_iso, read_text


ROOT = MODULE_ROOT
CODEX_HOME = codex_home()
MEMORY_ROOT = CODEX_HOME / "memories"
MEMORY_INDEX = MEMORY_ROOT / "MEMORY.md"
AD_HOC_NOTES = MEMORY_ROOT / "extensions" / "ad_hoc" / "notes"
EPHEMERAL_WORK_NOTES_DIR = ROOT / "_bridge" / "tmp" / "work_notes"
EPHEMERAL_WORK_NOTES_FILE = EPHEMERAL_WORK_NOTES_DIR / "current.jsonl"
RESOURCE_MEMORY_ROOT = resource_library_root() / "memory"
MEMORY_MANIFEST = RESOURCE_MEMORY_ROOT / "memory_manifest.json"
USER_PROFILE = RESOURCE_MEMORY_ROOT / "profiles" / "user_profile.json"
MEMORY_POLICY = RESOURCE_MEMORY_ROOT / "governance" / "memory_policy.json"
MEMORY_ABSORPTION_INDEX = RESOURCE_MEMORY_ROOT / "governance" / "memory_absorption_index.json"
PMB_FACT_REVIEW_MARKS = RESOURCE_MEMORY_ROOT / "governance" / "pmb_fact_review_marks.json"
MEMORY_MANIFEST_SCHEMA = RESOURCE_MEMORY_ROOT / "pmb" / "schemas" / "memory_manifest.schema.json"
USER_PROFILE_SCHEMA = RESOURCE_MEMORY_ROOT / "pmb" / "schemas" / "user_profile.schema.json"
PMB_WORKSPACE_DB = RESOURCE_MEMORY_ROOT / "pmb" / "data" / "workspaces" / "mcsmanager" / "events.sqlite"
PMB_WORKSPACES_ROOT = RESOURCE_MEMORY_ROOT / "pmb" / "data" / "workspaces"
PMB_RETIRED_WORKSPACES_ROOT = RESOURCE_MEMORY_ROOT / "pmb" / "data" / "_retired_workspaces"
PMB_RETIRED_WORKSPACES_TOMBSTONES = RESOURCE_MEMORY_ROOT / "governance" / "retired_pmb_workspaces.jsonl"
GLOBAL_FRAMEWORK = CODEX_HOME / "skills" / "global-framework" / "SKILL.md"
MEMORY_SYSTEMS = CODEX_HOME / "skills" / "memory-systems" / "SKILL.md"
LOCAL_PMB = ROOT / "_bridge" / "local_pmb_memory.py"
ITERATION_REVIEW = ROOT / "_bridge" / "iteration_layer_review.py"
EXTERNAL_KNOWLEDGE = ROOT / "_bridge" / "external_knowledge.py"

LONG_LIVED_SYSTEM_KEYWORDS = (
    "bridge",
    "weixin",
    "openclaw",
    "mcp",
    "email",
    "scheduler",
    "maintenance",
    "performance",
    "defender",
    "pmb",
    "memory",
    "skill",
    "baseline",
    "seed",
    "sqlite",
    "automation",
)

REQUIRED_GLOBAL_RULE_SNIPPETS = (
    "Memory is a continuous work layer",
    "Long-lived system work must keep memory in the loop",
)

REQUIRED_MEMORY_RULE_SNIPPETS = (
    "Operational Memory Loop",
    "memory is a work-control layer",
)

DESTINATION_MATRIX = (
    {
        "input": "user_preference_or_profile_fact",
        "destination": "user_profile",
        "examples": ["stable communication preferences", "identity/context", "long-term goals", "tradeoff priorities"],
        "requires": [
            "Codex inference from repeated user choices or explicit requirements",
            "explicit user approval before writing the fact into user_profile",
            "non-secret",
            "not a one-off mood or task state",
            "not mandatory behavior already owned by AGENTS/workspace rules/skills",
            "passes user_profile responsibility gate before write",
        ],
        "verification": [
            "user_profile_guidance_available",
            "user_profile_responsibility_gate_ok",
            "profile contains approved facts only; pending candidates remain outside user_profile",
            "future memory-preflight selects the fact when relevant",
        ],
    },
    {
        "input": "operational_lesson_or_root_cause",
        "destination": "local-pmb-memory or memory_absorption_index",
        "examples": ["MCP layer diagnosis rule", "bridge delivery root-cause pattern"],
        "requires": ["current evidence", "reusable future behavior", "incident details compressed"],
        "verification": ["recall-checks contains a query for the theme", "memory_governance validate"],
    },
    {
        "input": "codex_behavior_constraint",
        "destination": "AGENTS.md or workspace guide",
        "examples": ["closeout rule", "tool use boundary", "approval gate"],
        "requires": ["fits global/workspace scope", "does not duplicate existing rule", "validated mirror sync"],
        "verification": ["agents_rule_mirror validate", "targeted rule read-back"],
    },
    {
        "input": "reusable_procedure",
        "destination": "skill or custom-slash-command proposal",
        "examples": ["repeated repair flow", "review gate cleanup"],
        "requires": ["repeatable steps", "clear inputs/outputs/boundaries", "not just one incident"],
        "verification": ["template renders or skill gate passes", "small dry-run on representative case"],
    },
    {
        "input": "tool_capability_or_fallback_fact",
        "destination": "mcp_capability_matrix or maintenance surface",
        "examples": ["owning MCP route", "Hub fallback boundary", "current-turn callability rule"],
        "requires": ["current tool evidence", "permission boundary preserved", "native vs fallback separated"],
        "verification": ["mcp_session_doctor validate", "tool matrix read-back"],
    },
    {
        "input": "external_source_knowledge",
        "destination": "external_knowledge evidence layer first",
        "examples": ["official docs", "versioned upstream behavior"],
        "requires": ["official or primary source", "clear scope", "reusable", "non-sensitive"],
        "verification": ["external_knowledge doctor", "later distill-plan before long-term memory"],
    },
    {
        "input": "current_task_side_issue",
        "destination": "one-shot work note",
        "examples": ["non-blocking risk", "follow-up optimization", "possible stale rule"],
        "requires": ["valuable but not blocking", "safe to defer", "not an authorization grant", "cleared after closeout"],
        "verification": ["work-note-read at closeout", "read-only follow-up is handled after the main task", "derived writes are explicitly proposed for separate approval"],
    },
)


def file_info(path: Path) -> dict[str, Any]:
    return memory_surface.file_info(path)


def read_json(path: Path) -> tuple[dict[str, Any], str]:
    return memory_surface.read_json(path)


def json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return "".join(ch if ch in "\n\r\t" or ord(ch) >= 32 else " " for ch in value)
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def redact_work_note_text(text: str) -> tuple[str, list[dict[str, str]]]:
    return memory_work_notes.redact_work_note_text(text, sensitive_hits=sensitive_hits)


def work_note_read(limit: int = 100) -> dict[str, Any]:
    return memory_work_notes.work_note_read(EPHEMERAL_WORK_NOTES_FILE, limit=limit)


def work_note_add(text: str, *, source: str = "codex", scope: str = "general", reason: str = "") -> dict[str, Any]:
    return memory_work_notes.work_note_add(
        EPHEMERAL_WORK_NOTES_FILE,
        text,
        sensitive_hits=sensitive_hits,
        source=source,
        scope=scope,
        reason=reason,
    )


def work_note_clear() -> dict[str, Any]:
    return memory_work_notes.work_note_clear(EPHEMERAL_WORK_NOTES_FILE)


def work_note_dispose(ids: str, disposition: str) -> dict[str, Any]:
    selected = [item.strip() for item in str(ids or "").split(",") if item.strip()]
    return memory_work_notes.work_note_dispose(EPHEMERAL_WORK_NOTES_FILE, ids=selected, disposition=disposition)


def missing_keys(payload: dict[str, Any], required: list[str]) -> list[str]:
    return memory_surface.missing_keys(payload, required)


def manifest_issues(manifest: dict[str, Any], error: str) -> list[dict[str, Any]]:
    return memory_surface.manifest_issues(manifest, error, absorption_index_path=MEMORY_ABSORPTION_INDEX)


def user_profile_issues(profile: dict[str, Any], error: str) -> list[dict[str, Any]]:
    return memory_surface.user_profile_issues(profile, error)


def memory_policy_issues(policy: dict[str, Any], error: str) -> list[dict[str, Any]]:
    return memory_surface.memory_policy_issues(policy, error)


def absorption_index_issues(absorption: dict[str, Any], error: str) -> list[dict[str, Any]]:
    return memory_surface.absorption_index_issues(absorption, error)


def memory_surface_snapshot() -> dict[str, Any]:
    return memory_surface.build_snapshot(
        resource_memory_root=RESOURCE_MEMORY_ROOT,
        memory_manifest=MEMORY_MANIFEST,
        user_profile=USER_PROFILE,
        memory_policy=MEMORY_POLICY,
        memory_absorption_index=MEMORY_ABSORPTION_INDEX,
        memory_manifest_schema=MEMORY_MANIFEST_SCHEMA,
        user_profile_schema=USER_PROFILE_SCHEMA,
        build_user_profile_guidance=build_user_profile_guidance,
        external_knowledge_snapshot=external_knowledge_snapshot,
        external_knowledge_doctor=external_knowledge_doctor,
    )


def run_json(command: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "timeout",
            "command": command,
            "stdout_preview": str(exc.stdout or "")[:1200],
            "stderr_preview": str(exc.stderr or "")[:1200],
        }
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "command_not_found", "command": command, "error": str(exc)}
    raw = (proc.stdout or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "reason": "json_parse_failed",
            "stdout_preview": raw[:1200],
            "stderr_preview": (proc.stderr or "")[:1200],
        }
    if isinstance(payload, dict):
        payload.setdefault("returncode", proc.returncode)
        payload.setdefault("ok", proc.returncode == 0)
        return payload
    return {"ok": False, "reason": "unexpected_json_type", "command": command, "returncode": proc.returncode}


def candidate_notes(limit: int = 20) -> list[dict[str, Any]]:
    return memory_candidate_notes.candidate_notes(
        AD_HOC_NOTES,
        keywords=LONG_LIVED_SYSTEM_KEYWORDS,
        file_info_fn=file_info,
        read_text_fn=lambda path: read_text(path, limit=6000),
        limit=limit,
    )


def absorb_plan(limit: int = 20) -> dict[str, Any]:
    return memory_candidate_notes.absorb_plan(
        AD_HOC_NOTES,
        keywords=LONG_LIVED_SYSTEM_KEYWORDS,
        file_info_fn=file_info,
        read_preview_fn=lambda path: read_text(path, limit=6000),
        read_full_fn=lambda path: read_text(path, limit=20_000),
        limit=limit,
    )


PROFILE_CANDIDATE_CATEGORIES = (
    "communication_style",
    "identity_context",
    "long_term_goals",
    "tradeoff_priority",
    "permission_preferences",
)


def _profile_candidate_id(text: str, index: int) -> str:
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", str(text or "").strip().lower())
    compact = compact.strip("_")[:48] or f"candidate_{index}"
    return f"profile:{compact}"


def _profile_candidate_category(text: str) -> str:
    lower = str(text or "").lower()
    if any(word in lower for word in ("沟通", "解释", "回复", "简洁", "详细", "challenge", "direct", "compact")):
        return "communication_style"
    if any(word in lower for word in ("长期", "目标", "建设", "goal", "长期目标")):
        return "long_term_goals"
    if any(word in lower for word in ("身份", "owner", "admin", "操作者", "画像")):
        return "identity_context"
    if any(word in lower for word in ("取舍", "优先", "不要牺牲", "tradeoff", "成本", "效率")):
        return "tradeoff_priority"
    if any(word in lower for word in ("权限", "授权", "阻止", "friction")):
        return "permission_preferences"
    return "tradeoff_priority"


def _profile_candidate_conflict(candidate_text: str, existing_facts: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", str(candidate_text or "").lower()))
    best: dict[str, Any] = {"mode": "new", "fact_id": "", "overlap_ratio": 0.0}
    if not candidate_tokens:
        return best
    for fact in existing_facts:
        if not isinstance(fact, dict):
            continue
        value = str(fact.get("value") or "")
        fact_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", value.lower()))
        if not fact_tokens:
            continue
        overlap_ratio = len(candidate_tokens & fact_tokens) / max(1, min(len(candidate_tokens), len(fact_tokens)))
        if overlap_ratio > float(best.get("overlap_ratio") or 0):
            mode = "merge_or_update" if overlap_ratio >= 0.45 else "new"
            best = {
                "mode": mode,
                "fact_id": str(fact.get("id") or ""),
                "overlap_ratio": round(overlap_ratio, 3),
            }
    return best


def _profile_candidate_from_text(text: str, *, source: str, index: int, existing_facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if len(value) < 18:
        return None
    sensitive = sensitive_hits(value)
    if highest_severity(sensitive) in {"high", "medium"}:
        return None
    fact = {
        "id": _profile_candidate_id(value, index),
        "category": _profile_candidate_category(value),
        "value": value[:360],
        "source": source,
        "confidence": 0.6,
        "valid_from": datetime.now(timezone.utc).date().isoformat(),
        "valid_until": None,
        "review_status": "active",
    }
    responsibility = user_profile_responsibility_gate({"facts": [fact]})
    if responsibility.get("rule_owned_fact_count") or responsibility.get("needs_review_fact_count"):
        return None
    conflict = _profile_candidate_conflict(value, existing_facts)
    return {
        "id": fact["id"],
        "kind": "user_profile_candidate",
        "destination": "user_profile",
        "proposed_fact": fact,
        "source": source,
        "inference_basis": "Candidate extracted from current work notes or ad hoc notes; Codex must present it for user approval before writing.",
        "old_profile_action": conflict.get("mode", "new"),
        "related_existing_fact_id": conflict.get("fact_id", ""),
        "overlap_ratio": conflict.get("overlap_ratio", 0.0),
        "sensitive_severity": highest_severity(sensitive),
        "approval_request": "Approve writing this inferred profile fact, modify it, or reject it.",
    }


def profile_plan(limit: int = 20, signals: list[str] | None = None) -> dict[str, Any]:
    profile_payload, profile_error = read_json(USER_PROFILE) if USER_PROFILE.exists() else ({}, "missing")
    existing_facts = profile_payload.get("facts") if isinstance(profile_payload.get("facts"), list) else []
    candidates: list[dict[str, Any]] = []
    seen_values: set[str] = set()
    signal_sources = profile_signal_sources(signals or [])

    for signal in signal_sources:
        if not signal.get("profile_worthy"):
            continue
        candidate = _profile_candidate_from_text(
            str(signal.get("text") or ""),
            source=str(signal.get("source") or "closeout_profile_signal"),
            index=len(candidates) + 1,
            existing_facts=existing_facts,
        )
        if candidate and candidate["proposed_fact"]["value"] not in seen_values:
            seen_values.add(candidate["proposed_fact"]["value"])
            candidates.append(candidate)

    work_notes = work_note_read(limit=max(1, int(limit)))
    for entry in work_notes.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or entry.get("redacted_text") or entry.get("raw") or "")
        if not re.search(r"(画像|偏好|用户|要求|选择|prefer|preference|profile)", text, flags=re.IGNORECASE):
            continue
        candidate = _profile_candidate_from_text(
            text,
            source=f"work_note:{entry.get('id') or entry.get('created_at') or 'unknown'}",
            index=len(candidates) + 1,
            existing_facts=existing_facts,
        )
        if candidate and candidate["proposed_fact"]["value"] not in seen_values:
            seen_values.add(candidate["proposed_fact"]["value"])
            candidates.append(candidate)

    for item in candidate_notes(limit=max(1, int(limit))):
        path = Path(str(item.get("path") or ""))
        text = read_text(path, limit=12_000)
        destination = recommend_note_destination(path, text)
        if destination.get("destination") != "user_profile":
            continue
        points = stable_point_candidates(text, limit=5) or [item.get("preview", "")]
        for point in points:
            candidate = _profile_candidate_from_text(
                str(point),
                source=f"ad_hoc_note:{item.get('name') or path.name}",
                index=len(candidates) + 1,
                existing_facts=existing_facts,
            )
            if candidate and candidate["proposed_fact"]["value"] not in seen_values:
                seen_values.add(candidate["proposed_fact"]["value"])
                candidates.append(candidate)
            if len(candidates) >= int(limit):
                break
        if len(candidates) >= int(limit):
            break

    return {
        "schema": "memory_governance.profile_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "writes_profile": False,
        "source_policy": {
            "allowed_sources": ["explicit current-turn profile signals", "current work notes", "ad hoc notes selected for user_profile", "existing profile comparison"],
            "disallowed_sources": ["AGENTS rules", "workspace rules", "skills", "tool matrix", "one-off task state", "temporary authorization"],
            "approval_boundary": "Candidates are not written to user_profile until the user approves the proposed fact.",
        },
        "signal_intake": {
            "input_count": len(signals or []),
            "accepted_count": sum(1 for item in signal_sources if item.get("profile_worthy")),
            "skipped": [item for item in signal_sources if not item.get("profile_worthy")],
            "writes_profile": False,
        },
        "existing_profile_error": profile_error,
        "existing_fact_count": len(existing_facts),
        "candidate_count": len(candidates),
        "candidates": candidates[: max(0, int(limit))],
        "apply_policy": {
            "default_action": "no_write",
            "requires_user_approval": True,
            "before_apply": ["backup user_profile", "run responsibility gate", "merge/update/remove stale profile facts when needed"],
            "verify_after_apply": ["memory_governance validate", "memory_governance snapshot"],
        },
    }


def read_pmb_events(limit: int = 2000) -> tuple[list[dict[str, Any]], str]:
    return memory_pmb_organize.read_pmb_events(PMB_WORKSPACE_DB, limit=limit)


def consolidation_plan(limit: int = 200) -> dict[str, Any]:
    notes = candidate_notes(limit=limit)
    groups: dict[str, dict[str, Any]] = {}
    duplicate_notes: dict[str, list[str]] = {}
    review_required: list[dict[str, Any]] = []
    for item in notes:
        path = Path(str(item.get("path") or ""))
        text = read_text(path, limit=30_000)
        theme = recommend_consolidation_theme(path, text)
        theme_id = str(theme.get("theme_id") or "unclassified")
        group = groups.setdefault(
            theme_id,
            {
                "theme_id": theme_id,
                "destination": theme.get("destination", "workspace.mcsmanager.operational"),
                "confidence": theme.get("confidence", "low"),
                "source_notes": [],
                "stable_points": [],
                "sensitive_hits": [],
                "drift_hits": [],
                "duplicate_note_names": [],
                "recommended_action": "summarize_then_review_for_absorption",
            },
        )
        group["source_notes"].append(str(item.get("name") or path.name))
        for point in stable_point_candidates(text, limit=6):
            norm = normalize_memory_text(point)
            if norm and norm not in {normalize_memory_text(existing) for existing in group["stable_points"]}:
                group["stable_points"].append(point)
        sensitive = sensitive_hits(text)
        drift = drift_hits(text)
        if sensitive:
            group["sensitive_hits"].append({"note": path.name, "hits": sensitive})
            if highest_severity(sensitive) in {"high", "medium"}:
                review_required.append({"note": path.name, "reason": "sensitive_candidate", "hits": sensitive})
        if drift:
            group["drift_hits"].append({"note": path.name, "hits": drift})
        norm_text = normalize_memory_text(text)
        if len(norm_text) >= 80:
            duplicate_notes.setdefault(norm_text, []).append(path.name)
    duplicate_groups = [names for names in duplicate_notes.values() if len(names) > 1]
    for names in duplicate_groups:
        for group in groups.values():
            overlap = sorted(set(names) & set(group["source_notes"]))
            if overlap:
                group["duplicate_note_names"].extend(overlap)
    group_list = sorted(groups.values(), key=lambda item: (-len(item["source_notes"]), str(item["theme_id"])))
    for group in group_list:
        group["stable_points"] = group["stable_points"][:8]
        group["source_count"] = len(group["source_notes"])
        group["sensitive_severity"] = highest_severity([hit for item in group["sensitive_hits"] for hit in item.get("hits", [])])
        group["drift_severity"] = highest_severity([hit for item in group["drift_hits"] for hit in item.get("hits", [])])
        if group["sensitive_severity"] in {"high", "medium"}:
            group["recommended_action"] = "manual_sensitive_review_before_absorption"
        elif group["source_count"] >= 2:
            group["recommended_action"] = "merge_theme_summary_then_review_for_pmb"
        else:
            group["recommended_action"] = "single_note_review_for_pmb_or_skill"
    return {
        "schema": "memory_governance.consolidation_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "source": str(AD_HOC_NOTES),
        "candidate_count": len(notes),
        "theme_count": len(group_list),
        "groups": group_list,
        "duplicate_note_group_count": len(duplicate_groups),
        "review_required": review_required[:25],
        "external_design_basis": [
            "separate semantic facts, episodic incidents, and procedural rules",
            "keep changing facts temporally scoped instead of overwriting history",
            "keep compact active profile separate from archival long-term memory",
        ],
        "apply_policy": {
            "default_action": "no_write",
            "notes_are_source_evidence_until_archived_by_approval": True,
            "requires_backup_before_apply": True,
            "never_store_secrets": True,
            "verify_after_apply": [
                "memory_governance validate",
                "memory_governance metrics",
                "PMB recall or local fallback query for promoted theme",
            ],
        },
    }


def pmb_organize_plan(limit: int = 2000) -> dict[str, Any]:
    return memory_pmb_organize.organize_plan(PMB_WORKSPACE_DB, limit=limit, review_marks_path=PMB_FACT_REVIEW_MARKS)


def pmb_workspace_retire_plan(workspace_id: str) -> dict[str, Any]:
    return memory_pmb_workspaces.workspace_retire_plan(
        PMB_WORKSPACES_ROOT,
        workspace_id,
        active_workspace_id="mcsmanager",
        quarantine_root=PMB_RETIRED_WORKSPACES_ROOT,
        tombstone_path=PMB_RETIRED_WORKSPACES_TOMBSTONES,
    )


def pmb_workspace_retire_apply(workspace_id: str, *, reason: str, confirm: bool) -> dict[str, Any]:
    return memory_pmb_workspaces.workspace_retire_apply(
        PMB_WORKSPACES_ROOT,
        workspace_id,
        active_workspace_id="mcsmanager",
        quarantine_root=PMB_RETIRED_WORKSPACES_ROOT,
        tombstone_path=PMB_RETIRED_WORKSPACES_TOMBSTONES,
        reason=reason,
        confirm=confirm,
    )


def pmb_workspace_rebind_plan(workspace_id: str, *, target_name: str, target_root: str) -> dict[str, Any]:
    return memory_pmb_workspaces.workspace_rebind_plan(
        PMB_WORKSPACES_ROOT,
        workspace_id,
        target_name=target_name,
        target_root=target_root,
    )


def pmb_workspace_rebind_apply(
    workspace_id: str,
    *,
    target_name: str,
    target_root: str,
    confirm: bool,
) -> dict[str, Any]:
    plan = pmb_workspace_rebind_plan(workspace_id, target_name=target_name, target_root=target_root)
    if not confirm:
        return {
            "schema": "memory_governance.pmb_workspace_rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "confirmation_required",
            "plan": plan,
        }
    if not plan.get("eligible"):
        return {
            "schema": "memory_governance.pmb_workspace_rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "workspace_not_eligible",
            "plan": plan,
        }
    if not plan.get("would_change"):
        return {
            "schema": "memory_governance.pmb_workspace_rebind_apply.v1",
            "ok": True,
            "applied": False,
            "reason": "already_current",
            "plan": plan,
            "backup": {"ok": True, "skipped": "no_change"},
        }
    backup = create_routed_backup(
        [str(plan["meta_path"])],
        remark=f"pmb-workspace-rebind-{workspace_id}",
        purpose="preserve PMB workspace metadata before identity rebind",
        category="memory",
        trigger="memory_governance.pmb_workspace_rebind_apply",
    )
    if not backup.get("ok"):
        return {
            "schema": "memory_governance.pmb_workspace_rebind_apply.v1",
            "ok": False,
            "applied": False,
            "reason": "backup_failed",
            "plan": plan,
            "backup": backup,
        }
    result = memory_pmb_workspaces.workspace_rebind_apply(
        PMB_WORKSPACES_ROOT,
        workspace_id,
        target_name=target_name,
        target_root=target_root,
        confirm=True,
        expected_meta_sha256=str(plan.get("meta_sha256") or ""),
    )
    return {**result, "owner": "memory_governance", "backup": backup}


def pmb_fact_repair_plan(ids: str = "", limit: int = 2000) -> dict[str, Any]:
    return memory_pmb_organize.fact_repair_plan(
        PMB_WORKSPACE_DB,
        PMB_FACT_REVIEW_MARKS,
        ids=ids,
        limit=limit,
    )


def _backup_pmb_fact_review_marks(batch_id: str) -> dict[str, Any]:
    backup_root = RESOURCE_MEMORY_ROOT / "_backups" / f"{batch_id}-pmb-fact-review-marks"
    backup_root.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    if PMB_FACT_REVIEW_MARKS.exists():
        target = backup_root / "pmb_fact_review_marks.json.bak"
        shutil.copy2(PMB_FACT_REVIEW_MARKS, target)
        copied.append({"source": str(PMB_FACT_REVIEW_MARKS), "backup": str(target)})
    else:
        copied.append({"source": str(PMB_FACT_REVIEW_MARKS), "backup": "", "skipped": "source_missing_before_first_apply"})
    manifest = {
        "schema": "memory_governance.pmb_fact_review_marks_backup.v1",
        "created_at": now_iso(),
        "batch_id": batch_id,
        "copied": copied,
        "restore": "Copy pmb_fact_review_marks.json.bak back to the governance path, or remove the marks file if this was the first apply.",
    }
    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup_root": str(backup_root), "manifest_path": str(manifest_path), "copied_count": len(copied)}


def pmb_fact_apply_approved(ids: str, *, limit: int = 2000, confirm: bool = False) -> dict[str, Any]:
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = _backup_pmb_fact_review_marks(batch_id) if confirm else {}
    payload = memory_pmb_organize.apply_fact_review_marks(
        PMB_WORKSPACE_DB,
        PMB_FACT_REVIEW_MARKS,
        ids=ids,
        limit=limit,
        confirm=confirm,
    )
    if confirm:
        payload["backup"] = backup
        payload["post_apply_plan"] = pmb_organize_plan(limit=limit)
        payload["validation"] = validate()
    return payload


def review_summary(limit: int = 20) -> dict[str, Any]:
    return memory_review_summary.build_review_summary(
        limit=limit,
        consolidation_plan=lambda plan_limit: consolidation_plan(limit=plan_limit),
        absorb_plan=lambda plan_limit: absorb_plan(limit=plan_limit),
        pmb_organize_plan=lambda plan_limit: pmb_organize_plan(limit=plan_limit),
        profile_plan=lambda plan_limit: profile_plan(limit=plan_limit),
    )


def _recall_queries_for_theme(theme: dict[str, Any], *, include_points: bool = True) -> list[str]:
    return memory_recall_verification.recall_queries_for_theme(theme, include_points=include_points)


def _build_post_apply_recall_checks(item: dict[str, Any], theme_id: str, *, batch_id: str) -> list[dict[str, Any]]:
    return memory_recall_verification.build_post_apply_recall_checks(item, theme_id, batch_id=batch_id)


def recall_checks(limit: int = 50, *, include_verified: bool = False) -> dict[str, Any]:
    return memory_recall_verification.recall_checks(
        MEMORY_ABSORPTION_INDEX,
        limit=limit,
        include_verified=include_verified,
    )


def _pmb_contains_query(events: list[dict[str, Any]], query: str) -> dict[str, Any]:
    return memory_recall_verification.pmb_contains_query(events, query)


def recall_verify(limit: int = 50) -> dict[str, Any]:
    return memory_recall_verification.recall_verify(
        MEMORY_ABSORPTION_INDEX,
        read_pmb_events,
        limit=limit,
    )


def _safe_slug(value: str, default: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", str(value or "").strip())
    text = text.strip(".-_")
    return text[:120] or default


def _parse_apply_ids(value: str) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    if raw.lower() in {"all", "*", "全部"}:
        return {"all"}
    return {item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}")
    tmp.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate unique path for {path}")


def _backup_apply_targets(selected_items: list[dict[str, Any]], batch_id: str) -> dict[str, Any]:
    backup_root = RESOURCE_MEMORY_ROOT / "_backups" / f"{batch_id}-apply-approved"
    copied: list[dict[str, Any]] = []
    if MEMORY_ABSORPTION_INDEX.exists():
        target = backup_root / "memory_absorption_index.json.bak"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MEMORY_ABSORPTION_INDEX, target)
        copied.append({"source": str(MEMORY_ABSORPTION_INDEX), "backup": str(target)})
    note_root = backup_root / "source_notes"
    seen_sources: set[str] = set()
    for item in selected_items:
        for name in item.get("sources") or []:
            source_name = Path(str(name)).name
            if not source_name or source_name in seen_sources:
                continue
            seen_sources.add(source_name)
            source_path = AD_HOC_NOTES / source_name
            if not source_path.exists():
                copied.append({"source": str(source_path), "backup": "", "skipped": "missing"})
                continue
            target = note_root / source_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
            copied.append({"source": str(source_path), "backup": str(target)})
    manifest = {
        "schema": "memory_governance.apply_approved_backup.v1",
        "created_at": now_iso(),
        "batch_id": batch_id,
        "copied": copied,
        "restore": "Copy backup files back to their source paths after reviewing this manifest.",
    }
    backup_root.mkdir(parents=True, exist_ok=True)
    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup_root": str(backup_root), "manifest_path": str(manifest_path), "copied_count": len(copied)}


def _archive_selected_source_notes(selected_items: list[dict[str, Any]], batch_id: str) -> dict[str, Any]:
    archive_dir = AD_HOC_NOTES.parent / "archived" / batch_id
    moved: list[dict[str, str]] = []
    missing: list[str] = []
    seen_sources: set[str] = set()
    for item in selected_items:
        for name in item.get("sources") or []:
            source_name = Path(str(name)).name
            if not source_name or source_name in seen_sources:
                continue
            seen_sources.add(source_name)
            source_path = AD_HOC_NOTES / source_name
            if not source_path.exists():
                missing.append(str(source_path))
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_path(archive_dir / source_name)
            shutil.move(str(source_path), str(target))
            moved.append({"source": str(source_path), "archived": str(target)})
    manifest = {
        "schema": "memory_governance.apply_approved_archive.v1",
        "created_at": now_iso(),
        "batch_id": batch_id,
        "moved": moved,
        "missing": missing,
        "restore": "Move archived files back to the active ad_hoc notes directory if rollback is needed.",
    }
    if moved or missing:
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "manifest.json").write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"archive_dir": str(archive_dir), "moved_count": len(moved), "missing_count": len(missing), "moved": moved, "missing": missing}


def _external_knowledge_source_ids(selected_items: list[dict[str, Any]]) -> list[str]:
    source_ids: list[str] = []
    seen: set[str] = set()
    for item in selected_items:
        for name in item.get("sources") or []:
            source_path = AD_HOC_NOTES / Path(str(name)).name
            try:
                text = source_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            match = re.search(r"(?m)^\s*-\s*source_item_id:\s*(ek_[A-Za-z0-9_]+)\s*$", text)
            if not match:
                continue
            source_id = match.group(1)
            if source_id not in seen:
                seen.add(source_id)
                source_ids.append(source_id)
    return source_ids


def _mark_external_knowledge_absorbed(source_ids: list[str], batch_id: str) -> dict[str, Any]:
    if not source_ids:
        return {"ok": True, "skipped": "no_external_knowledge_source_ids"}
    if not EXTERNAL_KNOWLEDGE.exists():
        return {"ok": False, "reason": "external_knowledge_tool_missing", "path": str(EXTERNAL_KNOWLEDGE)}
    command = [
        sys.executable,
        str(EXTERNAL_KNOWLEDGE),
        "mark-absorbed",
        "--batch-id",
        batch_id,
        "--apply",
    ]
    for source_id in source_ids:
        command.extend(["--source-item-id", source_id])
    return run_json(command, timeout=60)


def apply_approved(ids: str, *, limit: int = 200, confirm: bool = False, archive_notes: bool = True) -> dict[str, Any]:
    requested = _parse_apply_ids(ids)
    review = review_summary(limit=max(1, int(limit)))
    items = [item for item in review.get("approval_items", []) if isinstance(item, dict)]
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if "all" not in requested and item_id not in requested:
            continue
        if item.get("kind") not in {"note_consolidation", "single_note_absorption"}:
            skipped.append({"id": item_id, "reason": "requires_finer_plan_or_non_note_kind", "kind": item.get("kind")})
            continue
        if item.get("sensitive_severity") in {"high", "medium"}:
            skipped.append({"id": item_id, "reason": "sensitive_review_required", "sensitive_severity": item.get("sensitive_severity")})
            continue
        selected.append(item)

    dry_payload = {
        "schema": "memory_governance.apply_approved.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": not confirm,
        "requested_ids": sorted(requested),
        "selected_count": len(selected),
        "selected_ids": [item.get("id") for item in selected],
        "skipped": skipped,
        "writes_absorption_index": bool(confirm and selected),
        "archives_source_notes": bool(confirm and selected and archive_notes),
        "requires_confirm_apply": True,
    }
    if not confirm:
        return {
            **dry_payload,
            "required_next_command": (
                "python _bridge\\memory_governance.py apply-approved --ids <id|all> "
                "--confirm-apply --limit 200"
            ),
        }
    if not selected:
        return {**dry_payload, "ok": False, "reason": "no_selected_items_to_apply"}

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    external_source_ids = _external_knowledge_source_ids(selected)
    backup = _backup_apply_targets(selected, batch_id)
    index_payload, index_error = read_json(MEMORY_ABSORPTION_INDEX) if MEMORY_ABSORPTION_INDEX.exists() else ({}, "missing")
    if index_error:
        return {**dry_payload, "ok": False, "reason": "absorption_index_unreadable", "error": index_error, "backup": backup}
    themes = index_payload.setdefault("merged_themes", [])
    if not isinstance(themes, list):
        return {**dry_payload, "ok": False, "reason": "absorption_index_merged_themes_not_list", "backup": backup}
    by_theme: dict[str, dict[str, Any]] = {
        str(item.get("theme_id")): item for item in themes if isinstance(item, dict) and item.get("theme_id")
    }
    applied: list[dict[str, Any]] = []
    for item in selected:
        item_id = str(item.get("id") or "")
        if item_id.startswith("consolidation:"):
            theme_id = item_id.split(":", 1)[1]
        else:
            theme_id = _safe_slug(item_id.replace("absorb:", "single_note_"), default="single_note")
        target = by_theme.get(theme_id)
        if target is None:
            target = {"theme_id": theme_id}
            themes.append(target)
            by_theme[theme_id] = target
        target.update(
            {
                "theme_id": theme_id,
                "destination": item.get("destination"),
                "status": "approved_absorbed",
                "approved_at": now_iso(),
                "approved_batch_id": batch_id,
                "approval_item_id": item_id,
                "source_notes": item.get("sources") or [],
                "stable_points": item.get("keep") or [],
                "sensitive_severity": item.get("sensitive_severity") or "",
                "drift_severity": item.get("drift_severity") or "",
                "excluded": item.get("exclude") or [],
                "validation": item.get("validation") or [],
                "post_apply_recall_checks": _build_post_apply_recall_checks(item, theme_id, batch_id=batch_id),
                "notes_after_merge": "source notes archived by apply-approved after backup" if archive_notes else "source notes kept active",
            }
        )
        applied.append({"id": item_id, "theme_id": theme_id, "destination": item.get("destination")})
    index_payload["generated_at"] = now_iso()
    batches = index_payload.setdefault("approved_apply_batches", [])
    if isinstance(batches, list):
        batches.append(
            {
                "batch_id": batch_id,
                "applied_at": now_iso(),
                "applied_ids": [item.get("id") for item in selected],
                "backup": backup,
                "archive_notes": archive_notes,
            }
        )
    _write_json_atomic(MEMORY_ABSORPTION_INDEX, index_payload)
    external_knowledge_absorbed = _mark_external_knowledge_absorbed(external_source_ids, batch_id)
    archive = _archive_selected_source_notes(selected, batch_id) if archive_notes else {"moved_count": 0, "missing_count": 0}
    validation = validate()
    return {
        **dry_payload,
        "dry_run": False,
        "batch_id": batch_id,
        "applied": applied,
        "backup": backup,
        "archive": archive,
        "external_knowledge_absorbed": external_knowledge_absorbed,
        "validation_ok": bool(validation.get("ok")),
        "validation_doctor_status": validation.get("doctor_status"),
        "validation_advisory_count": validation.get("advisory_count"),
    }


def iteration_candidate_plan(candidate: dict[str, Any], *, index_path: Path = ITERATION_MEMORY_INDEX) -> dict[str, Any]:
    """Thin owner facade; does not approve or read the workflow queue."""

    return _plan_iteration_candidate(candidate, index_path=index_path)


def iteration_candidate_apply(
    candidate: dict[str, Any],
    *,
    confirm: bool = False,
    index_path: Path = ITERATION_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    """Apply one already-approved candidate through the memory owner."""

    return _apply_iteration_candidate(candidate, confirm=confirm, index_path=index_path, backup=backup)


def iteration_candidates_apply(
    candidates: list[dict[str, Any]],
    *,
    confirm: bool = False,
    index_path: Path = ITERATION_MEMORY_INDEX,
    backup: bool = True,
) -> dict[str, Any]:
    """Apply a reviewed batch through the memory owner with one backup."""

    return _apply_iteration_candidates(candidates, confirm=confirm, index_path=index_path, backup=backup)


def iteration_candidate_validate(candidate: dict[str, Any], *, index_path: Path = ITERATION_MEMORY_INDEX) -> dict[str, Any]:
    return _validate_iteration_candidate(candidate, index_path=index_path)


def iteration_candidate_recall(candidate_id: str, *, index_path: Path = ITERATION_MEMORY_INDEX) -> dict[str, Any]:
    return _recall_iteration_candidate(candidate_id, index_path=index_path)


def _iteration_candidate_queue_item(candidate_id: str, *, required_status: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from _bridge.workflow_review_queue import get_review_item

    record = get_review_item(candidate_id)
    if not record.get("ok"):
        return {}, record
    if record.get("kind") != "iteration_candidates":
        return {}, {"ok": False, "reason": "review_item_kind_not_iteration_candidate", "record": record}
    if record.get("status") != required_status:
        return {}, {
            "ok": False,
            "reason": "review_item_status_not_allowed",
            "required_status": required_status,
            "actual_status": record.get("status"),
        }
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    return item, record


def rule_presence() -> dict[str, Any]:
    global_text = read_text(GLOBAL_FRAMEWORK)
    memory_text = read_text(MEMORY_SYSTEMS)
    return {
        "global_framework": {
            "path": str(GLOBAL_FRAMEWORK),
            "exists": bool(global_text),
            "required_snippets": {
                snippet: snippet in global_text for snippet in REQUIRED_GLOBAL_RULE_SNIPPETS
            },
        },
        "memory_systems": {
            "path": str(MEMORY_SYSTEMS),
            "exists": bool(memory_text),
            "required_snippets": {
                snippet: snippet in memory_text for snippet in REQUIRED_MEMORY_RULE_SNIPPETS
            },
        },
    }


def pmb_metrics() -> dict[str, Any]:
    if not LOCAL_PMB.exists():
        return {"ok": False, "reason": "local_pmb_memory_missing", "path": str(LOCAL_PMB)}
    return run_json([sys.executable, str(LOCAL_PMB), "metrics"], timeout=45)


def external_knowledge_command(command: str, timeout: int = 30) -> dict[str, Any]:
    if not EXTERNAL_KNOWLEDGE.exists():
        return {"ok": False, "reason": "external_knowledge_tool_missing", "path": str(EXTERNAL_KNOWLEDGE)}
    return run_json([sys.executable, str(EXTERNAL_KNOWLEDGE), command], timeout=timeout)


def external_knowledge_snapshot() -> dict[str, Any]:
    return external_knowledge_command("snapshot", timeout=30)


def external_knowledge_doctor() -> dict[str, Any]:
    return external_knowledge_command("doctor", timeout=30)


def snapshot() -> dict[str, Any]:
    candidates = candidate_notes()
    rules = rule_presence()
    pmb = pmb_metrics()
    surface = memory_surface_snapshot()
    work_notes = work_note_read(limit=100)
    recall = recall_checks(limit=50)
    return {
        "schema": "memory_governance.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "memory_index": file_info(MEMORY_INDEX),
        "ad_hoc_notes_dir": file_info(AD_HOC_NOTES),
        "candidate_notes": candidates,
        "candidate_note_count": len(candidates),
        "operational_candidate_note_count": sum(1 for item in candidates if item.get("likely_operational")),
        "ephemeral_work_notes": work_notes,
        "ephemeral_work_note_count": int(work_notes.get("active_count") or 0),
        "rules": rules,
        "pmb_metrics": pmb,
        "pmb_fact_review_marks": file_info(PMB_FACT_REVIEW_MARKS),
        "memory_surface": surface,
        "post_apply_recall_checks": recall,
        "post_apply_recall_pending_count": int(recall.get("pending_count") or 0),
        "policy": {
            "memory_role": "continuous_work_layer",
            "long_lived_systems": list(LONG_LIVED_SYSTEM_KEYWORDS),
            "core_loop": ["recall", "verify_current_state", "act", "record_reusable_lesson", "iterate"],
            "no_secret_storage": True,
            "candidate_notes_are_not_final_memory": True,
            "ephemeral_work_notes_are_one_shot": True,
            "codex_reads_work_notes_raw_at_closeout": True,
            "approved_memory_requires_recall_verification": True,
            "destination_matrix": list(DESTINATION_MATRIX),
        },
    }


def memory_index_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not snap["memory_index"].get("exists"):
        issues.append({"severity": "risk", "code": "memory_index_missing", "message": "MEMORY.md is missing."})
    if not snap["ad_hoc_notes_dir"].get("exists"):
        issues.append({"severity": "advisory", "code": "ad_hoc_notes_dir_missing", "message": "Memory candidate note directory is missing."})
    return issues


def memory_rule_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rules = snap.get("rules", {})
    for layer_name in ("global_framework", "memory_systems"):
        layer = rules.get(layer_name, {}) if isinstance(rules, dict) else {}
        missing = [key for key, present in (layer.get("required_snippets") or {}).items() if not present]
        if missing:
            issues.append(
                {
                    "severity": "risk",
                    "code": f"{layer_name}_memory_work_rule_missing",
                    "message": f"{layer_name} is missing active memory work-loop rules.",
                    "missing": missing,
                }
            )
    return issues


def memory_pending_work_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if int(snap.get("operational_candidate_note_count") or 0) > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "memory_candidates_pending_review",
                "message": "Operational memory candidates exist and should be reviewed by the iteration layer before becoming durable rules.",
                "count": int(snap.get("operational_candidate_note_count") or 0),
            }
        )
    if int(snap.get("ephemeral_work_note_count") or 0) > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "ephemeral_work_notes_pending_closeout",
                "message": "One-shot work notes exist. After the main task, Codex must read the raw entries and process read-only follow-up itself. Main-task approval does not carry over: derived writes or external actions need separate explicit approval. Clear notes only after handling/proposing/deferring/discarding each item.",
                "count": int(snap.get("ephemeral_work_note_count") or 0),
            }
        )
    return issues


def memory_recall_issues(snap: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    issues: list[dict[str, Any]] = []
    recall_verify_payload: dict[str, Any] = {}
    recall_unverified_count = 0
    if int(snap.get("post_apply_recall_pending_count") or 0) > 0:
        recall_verify_payload = recall_verify(limit=1000)
        recall_unverified_count = int((recall_verify_payload.get("status_counts") or {}).get("not_verified") or 0)
    if recall_unverified_count > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "memory_post_apply_recall_checks_unverified",
                "message": "Approved absorbed memory has recall checks that did not pass read-only local index or PMB verification.",
                "count": recall_unverified_count,
            }
        )
    return issues, recall_verify_payload, recall_unverified_count


def memory_surface_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    surface = snap.get("memory_surface") if isinstance(snap.get("memory_surface"), dict) else {}
    for key, code, message in [
        ("manifest", "memory_manifest_invalid", "Unified memory manifest is missing or invalid."),
        ("user_profile", "user_profile_invalid", "User profile partition is missing or invalid."),
        ("policy", "memory_policy_invalid", "Memory policy file is missing or invalid."),
        ("absorption_index", "memory_absorption_index_invalid", "Memory absorption index is missing or invalid."),
    ]:
        item = surface.get(key) if isinstance(surface.get(key), dict) else {}
        if not item.get("ok"):
            issues.append(
                {
                    "severity": "risk",
                    "code": code,
                    "message": message,
                    "path": item.get("path"),
                    "detail": item.get("issues"),
                }
            )
    schemas = surface.get("schemas") if isinstance(surface.get("schemas"), dict) else {}
    if not ((schemas.get("manifest") or {}).get("exists")) or not ((schemas.get("user_profile") or {}).get("exists")):
        issues.append(
            {
                "severity": "risk",
                "code": "memory_schema_files_missing",
                "message": "Memory manifest/profile schema files should exist for local validation.",
                "detail": schemas,
            }
        )
    return issues


def external_knowledge_surface_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    surface = snap.get("memory_surface") if isinstance(snap.get("memory_surface"), dict) else {}
    external_surface = surface.get("external_knowledge") if isinstance(surface.get("external_knowledge"), dict) else {}
    external = external_surface.get("doctor") if isinstance(external_surface.get("doctor"), dict) else {}
    external_snapshot = external_surface.get("snapshot") if isinstance(external_surface.get("snapshot"), dict) else {}
    if not external.get("ok"):
        issue_codes = {str(item.get("code")) for item in external.get("issues", []) if isinstance(item, dict)}
        severity = "advisory" if issue_codes == {"external_knowledge_store_missing"} else "risk"
        issues.append(
            {
                "severity": severity,
                "code": "external_knowledge_surface_unhealthy",
                "message": "External knowledge capture surface is unavailable or has invalid evidence items.",
                "detail": external,
            }
        )
    elif int(external_snapshot.get("json_error_count") or 0) > 0:
        issues.append(
            {
                "severity": "risk",
                "code": "external_knowledge_json_errors",
                "message": "External knowledge evidence store has unreadable JSON items.",
                "detail": external_snapshot.get("errors"),
            }
        )
    return issues


def pmb_metric_issues(snap: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pmb = snap.get("pmb_metrics", {}) if isinstance(snap.get("pmb_metrics"), dict) else {}
    if not pmb.get("ok"):
        issues.append({"severity": "risk", "code": "pmb_metrics_unavailable", "message": "PMB memory metrics are unavailable.", "detail": pmb})
    elif not pmb.get("pmb_daemon_running"):
        issues.append({"severity": "risk", "code": "pmb_daemon_not_running", "message": "PMB should be warm for daily memory recall."})
    return issues


def pmb_fact_review_mark_issues() -> list[dict[str, Any]]:
    _payload, error = memory_pmb_organize.read_review_marks(PMB_FACT_REVIEW_MARKS)
    if not error:
        return []
    return [
        {
            "severity": "risk",
            "code": "pmb_fact_review_marks_invalid",
            "message": "PMB fact review marker file is unreadable or invalid.",
            "detail": {"path": str(PMB_FACT_REVIEW_MARKS), "error": error},
        }
    ]


def memory_doctor_issues(snap: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    recall_issues, recall_verify_payload, recall_unverified_count = memory_recall_issues(snap)
    issues = [
        *memory_index_issues(snap),
        *memory_rule_issues(snap),
        *memory_pending_work_issues(snap),
        *recall_issues,
        *memory_surface_issues(snap),
        *external_knowledge_surface_issues(snap),
        *pmb_metric_issues(snap),
        *pmb_fact_review_mark_issues(),
    ]
    return issues, recall_verify_payload, recall_unverified_count


def memory_doctor_status(issues: list[dict[str, Any]]) -> str:
    severities = {item["severity"] for item in issues}
    return "risk" if "risk" in severities else "advisory" if issues else "ok"


def memory_doctor_summary(snap: dict[str, Any], pmb: dict[str, Any], recall_verify_payload: dict[str, Any], recall_unverified_count: int) -> dict[str, Any]:
    return {
        "candidate_note_count": snap.get("candidate_note_count", 0),
        "operational_candidate_note_count": snap.get("operational_candidate_note_count", 0),
        "ephemeral_work_note_count": snap.get("ephemeral_work_note_count", 0),
        "post_apply_recall_pending_count": snap.get("post_apply_recall_pending_count", 0),
        "post_apply_recall_unverified_count": recall_unverified_count,
        "post_apply_recall_verified_counts": recall_verify_payload.get("status_counts", {}),
        "pmb_daemon_running": bool(pmb.get("pmb_daemon_running")),
        "pmb_daemon_root_count": pmb.get("pmb_daemon_root_count"),
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues, recall_verify_payload, recall_unverified_count = memory_doctor_issues(snap)
    pmb = snap.get("pmb_metrics", {}) if isinstance(snap.get("pmb_metrics"), dict) else {}
    status = memory_doctor_status(issues)
    return {
        "schema": "memory_governance.doctor.v1",
        "ok": not any(item["severity"] == "risk" for item in issues),
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "summary": memory_doctor_summary(snap, pmb, recall_verify_payload, recall_unverified_count),
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    doc = doctor(snap)
    actions: list[dict[str, Any]] = []
    for issue in doc["issues"]:
        code = issue.get("code")
        if code in {"global_framework_memory_work_rule_missing", "memory_systems_memory_work_rule_missing"}:
            actions.append(
                {
                    "id": "update_memory_work_rules",
                    "mode": "manual_approval_required",
                    "target": code.replace("_memory_work_rule_missing", ""),
                    "guardrails": ["backup_before_edit", "keep_rules_short", "do_not_store_secrets"],
                }
            )
        elif code == "memory_candidates_pending_review":
            actions.append(
                {
                    "id": "review_memory_candidates_with_iteration_layer",
                    "mode": "read_only_first",
                    "command": f"{sys.executable} _bridge\\iteration_layer_review.py --json --run-validation --validation-profile quick",
                    "guardrails": ["candidate_notes_are_not_final_memory", "promote_only_verified_durable_lessons"],
                }
            )
        elif code == "pmb_daemon_not_running":
            actions.append(
                {
                    "id": "ensure_pmb_daemon",
                    "mode": "safe_apply_available",
                    "command": f"{sys.executable} _bridge\\local_pmb_memory.py daemon-ensure",
                    "guardrails": ["local_daemon_only", "no_config_change"],
                }
            )
        elif code == "memory_post_apply_recall_checks_unverified":
            actions.append(
                {
                    "id": "run_memory_recall_checks",
                    "mode": "read_only_first",
                    "command": f"{sys.executable} _bridge\\memory_governance.py recall-checks --limit 50",
                    "guardrails": [
                        "read_only_plan",
                        "do_not_write_or_delete_memory",
                        "use_pmb_recall_or_local_readback_only_when_current_turn_callable",
                    ],
                }
            )
        elif code in {"memory_manifest_invalid", "user_profile_invalid", "memory_policy_invalid", "memory_schema_files_missing"}:
            actions.append(
                {
                    "id": "repair_memory_entrypoint_surface",
                    "mode": "manual_approval_required",
                    "guardrails": [
                        "backup_before_edit",
                        "do_not_store_secrets_in_normal_memory",
                        "preserve_pmb_as_primary_memory_body",
                        "keep_skills_independent",
                    ],
                }
            )
    return {
        "schema": "memory_governance.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "actions": actions,
        "doctor_issues": doc["issues"],
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    doc = doctor(snap)
    consolidation = consolidation_plan(limit=200)
    review = review_summary(limit=20)
    recall = recall_checks(limit=50)
    recall_verified = recall_verify(limit=50)
    profile_signal_plan = profile_plan(
        limit=5,
        signals=["我希望以后做稳定性和功能性取舍时，优先保持功能完整，再通过隐藏执行、验证和回滚控制稳定性风险。"],
    )
    profile_payload, profile_error = read_json(USER_PROFILE) if USER_PROFILE.exists() else ({}, "missing")
    responsibility = user_profile_responsibility_gate(profile_payload if not profile_error else {})
    responsibility_ok = (
        bool(responsibility.get("ok"))
        and int(responsibility.get("rule_owned_fact_count") or 0) == 0
        and int(responsibility.get("needs_review_fact_count") or 0) == 0
    )
    checks = [
        {"name": "memory_index_exists", "ok": bool(snap["memory_index"].get("exists")), "detail": snap["memory_index"].get("path")},
        {"name": "global_framework_rule_present", "ok": not any(item.get("code") == "global_framework_memory_work_rule_missing" for item in doc["issues"]), "detail": str(GLOBAL_FRAMEWORK)},
        {"name": "memory_systems_rule_present", "ok": not any(item.get("code") == "memory_systems_memory_work_rule_missing" for item in doc["issues"]), "detail": str(MEMORY_SYSTEMS)},
        {"name": "pmb_metrics_available", "ok": bool((snap.get("pmb_metrics") or {}).get("ok")), "detail": (snap.get("pmb_metrics") or {}).get("schema", "")},
        {"name": "pmb_fact_review_marks_valid", "ok": not any(item.get("code") == "pmb_fact_review_marks_invalid" for item in doc["issues"]), "detail": str(PMB_FACT_REVIEW_MARKS)},
        {"name": "memory_manifest_valid", "ok": not any(item.get("code") == "memory_manifest_invalid" for item in doc["issues"]), "detail": str(MEMORY_MANIFEST)},
        {"name": "user_profile_valid", "ok": not any(item.get("code") == "user_profile_invalid" for item in doc["issues"]), "detail": str(USER_PROFILE)},
        {"name": "user_profile_guidance_available", "ok": bool(((snap.get("memory_surface") or {}).get("user_profile") or {}).get("guidance", {}).get("ok")), "detail": str(USER_PROFILE)},
        {
            "name": "user_profile_signal_intake_available",
            "ok": bool(profile_signal_plan.get("ok"))
            and profile_signal_plan.get("writes_profile") is False
            and int(profile_signal_plan.get("candidate_count") or 0) >= 1,
            "detail": {
                "candidate_count": profile_signal_plan.get("candidate_count"),
                "writes_profile": profile_signal_plan.get("writes_profile"),
            },
        },
        {
            "name": "user_profile_responsibility_gate_ok",
            "ok": responsibility_ok,
            "detail": {
                "profile_owned_fact_count": responsibility.get("profile_owned_fact_count"),
                "rule_owned_fact_count": responsibility.get("rule_owned_fact_count"),
                "needs_review_fact_count": responsibility.get("needs_review_fact_count"),
            },
        },
        {"name": "memory_policy_valid", "ok": not any(item.get("code") == "memory_policy_invalid" for item in doc["issues"]), "detail": str(MEMORY_POLICY)},
        {"name": "memory_absorption_index_valid", "ok": not any(item.get("code") == "memory_absorption_index_invalid" for item in doc["issues"]), "detail": str(MEMORY_ABSORPTION_INDEX)},
        {
            "name": "memory_consolidation_plan_available",
            "ok": bool(consolidation.get("ok")) and (
                int(consolidation.get("theme_count") or 0) > 0
                or int(consolidation.get("candidate_count") or 0) == 0
            ),
            "detail": "memory_governance consolidation-plan",
        },
        {"name": "memory_review_summary_available", "ok": bool(review.get("ok")) and bool(review.get("review_text")) and review.get("writes_memory") is False, "detail": "memory_governance review-summary"},
        {"name": "memory_recall_checks_available", "ok": bool(recall.get("ok")) and recall.get("dry_run") is True, "detail": "memory_governance recall-checks"},
        {"name": "memory_recall_verify_available", "ok": bool(recall_verified.get("ok")) and recall_verified.get("dry_run") is True and recall_verified.get("writes_memory") is False, "detail": "memory_governance recall-verify"},
        {"name": "ephemeral_work_note_surface_available", "ok": bool(work_note_read(limit=1).get("ok")), "detail": "memory_governance work-note-add|work-note-read|work-note-clear"},
        {"name": "memory_schema_files_exist", "ok": not any(item.get("code") == "memory_schema_files_missing" for item in doc["issues"]), "detail": str(MEMORY_MANIFEST_SCHEMA)},
        {"name": "external_knowledge_surface_available", "ok": not any(item.get("code") == "external_knowledge_surface_unhealthy" and item.get("severity") == "risk" for item in doc["issues"]), "detail": str(EXTERNAL_KNOWLEDGE)},
    ]
    failed = [item for item in checks if not item["ok"]]
    return {
        "schema": "memory_governance.validate.v1",
        "ok": not failed,
        "generated_at": now_iso(),
        "checks": checks,
        "doctor_status": doc["status"],
        "advisory_count": sum(1 for item in doc["issues"] if item.get("severity") == "advisory"),
    }


def metrics() -> dict[str, Any]:
    snap = snapshot()
    pmb = snap.get("pmb_metrics", {}) if isinstance(snap.get("pmb_metrics"), dict) else {}
    surface = snap.get("memory_surface", {}) if isinstance(snap.get("memory_surface"), dict) else {}
    organize = pmb_organize_plan(limit=1000)
    consolidation = consolidation_plan(limit=200)
    return {
        "schema": "memory_governance.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "candidate_note_count": snap.get("candidate_note_count", 0),
        "operational_candidate_note_count": snap.get("operational_candidate_note_count", 0),
        "ephemeral_work_note_count": snap.get("ephemeral_work_note_count", 0),
        "pmb_daemon_running": bool(pmb.get("pmb_daemon_running")),
        "pmb_daemon_root_count": pmb.get("pmb_daemon_root_count"),
        "pmb_home_file_count": pmb.get("pmb_home_file_count"),
        "pmb_home_size_bytes": pmb.get("pmb_home_size_bytes"),
        "memory_manifest_ok": bool((surface.get("manifest") or {}).get("ok")),
        "memory_namespace_count": int((surface.get("manifest") or {}).get("namespace_count") or 0),
        "user_profile_ok": bool((surface.get("user_profile") or {}).get("ok")),
        "user_profile_fact_count": int((surface.get("user_profile") or {}).get("fact_count") or 0),
        "user_profile_guidance_count": int((((surface.get("user_profile") or {}).get("guidance") or {}).get("selected_fact_count")) or 0),
        "memory_policy_ok": bool((surface.get("policy") or {}).get("ok")),
        "memory_absorption_index_ok": bool((surface.get("absorption_index") or {}).get("ok")),
        "memory_absorption_theme_count": int((surface.get("absorption_index") or {}).get("theme_count") or 0),
        "memory_consolidation_theme_count": int(consolidation.get("theme_count") or 0),
        "memory_consolidation_duplicate_note_group_count": int(consolidation.get("duplicate_note_group_count") or 0),
        "memory_consolidation_review_required_count": len(consolidation.get("review_required") or []),
        "memory_review_summary_item_count": int(review_summary(limit=20).get("approval_item_count") or 0),
        "memory_post_apply_recall_pending_count": int(recall_checks(limit=1000).get("pending_count") or 0),
        "external_knowledge_item_count": int(((((surface.get("external_knowledge") or {}).get("snapshot") or {}).get("item_count")) or 0)),
        "external_knowledge_json_error_count": int(((((surface.get("external_knowledge") or {}).get("snapshot") or {}).get("json_error_count")) or 0)),
        "pmb_duplicate_group_count": len(organize.get("duplicate_groups") or []),
        "pmb_drift_prone_candidate_count": len(organize.get("stale_or_drift_prone_candidates") or []),
        "pmb_sensitive_candidate_count": len(organize.get("sensitive_candidates") or []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only memory governance checks")
    parser.add_argument(
        "command",
        choices=[
            "snapshot",
            "doctor",
            "repair-plan",
            "validate",
            "metrics",
            "absorb-plan",
            "profile-plan",
            "pmb-organize-plan",
            "pmb-workspace-retire-plan",
            "pmb-workspace-retire-apply",
            "pmb-workspace-rebind-plan",
            "pmb-workspace-rebind-apply",
            "pmb-fact-repair-plan",
            "pmb-fact-apply-approved",
            "consolidation-plan",
            "review-summary",
            "apply-approved",
            "iteration-candidate-plan",
            "iteration-candidate-apply",
            "iteration-candidate-validate",
            "iteration-candidate-recall",
            "recall-checks",
            "recall-verify",
            "work-note-add",
            "work-note-read",
            "work-note-dispose",
            "work-note-clear",
        ],
    )
    parser.add_argument("--limit", type=int, default=20, help="Candidate/sample limit for plan commands")
    parser.add_argument("--ids", default="", help="Approval item ids for apply-approved, comma-separated or all")
    parser.add_argument("--confirm-apply", action="store_true", help="Actually apply approved memory items")
    parser.add_argument("--keep-source-notes", action="store_true", help="Do not archive source notes after apply-approved")
    parser.add_argument("--text", default="", help="Raw one-shot work note text for work-note-add")
    parser.add_argument("--signal", action="append", default=[], help="Explicit current-turn user-profile candidate signal for profile-plan")
    parser.add_argument("--source", default="codex", help="Source label for work-note-add")
    parser.add_argument("--scope", default="general", help="Scope label for work-note-add")
    parser.add_argument("--reason", default="", help="Reason label for work-note-add")
    parser.add_argument("--workspace-id", default="", help="Exact PMB workspace id for workspace retirement commands")
    parser.add_argument("--workspace-name", default="", help="PMB workspace display name for rebind commands")
    parser.add_argument("--workspace-root", default="", help="Absolute project root for PMB workspace rebind commands")
    parser.add_argument("--disposition", default="", help="Work-note disposition: handled_read_only|proposal|deferred|discarded")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
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
    elif args.command == "absorb-plan":
        payload = absorb_plan(limit=args.limit)
    elif args.command == "profile-plan":
        payload = profile_plan(limit=max(1, int(args.limit)), signals=args.signal)
    elif args.command == "pmb-workspace-retire-plan":
        payload = pmb_workspace_retire_plan(args.workspace_id)
    elif args.command == "pmb-workspace-retire-apply":
        payload = pmb_workspace_retire_apply(
            args.workspace_id,
            reason=args.reason,
            confirm=bool(args.confirm_apply),
        )
    elif args.command == "pmb-workspace-rebind-plan":
        payload = pmb_workspace_rebind_plan(
            args.workspace_id,
            target_name=args.workspace_name,
            target_root=args.workspace_root,
        )
    elif args.command == "pmb-workspace-rebind-apply":
        payload = pmb_workspace_rebind_apply(
            args.workspace_id,
            target_name=args.workspace_name,
            target_root=args.workspace_root,
            confirm=bool(args.confirm_apply),
        )
    elif args.command == "consolidation-plan":
        payload = consolidation_plan(limit=max(1, int(args.limit)))
    elif args.command == "review-summary":
        payload = review_summary(limit=max(1, int(args.limit)))
    elif args.command == "pmb-fact-repair-plan":
        payload = pmb_fact_repair_plan(ids=args.ids, limit=max(1, int(args.limit)))
    elif args.command == "pmb-fact-apply-approved":
        payload = pmb_fact_apply_approved(
            args.ids,
            limit=max(1, int(args.limit)),
            confirm=bool(args.confirm_apply),
        )
    elif args.command == "apply-approved":
        payload = apply_approved(
            args.ids,
            limit=max(1, int(args.limit)),
            confirm=bool(args.confirm_apply),
            archive_notes=not bool(args.keep_source_notes),
        )
    elif args.command in {"iteration-candidate-plan", "iteration-candidate-apply"}:
        candidate, record = _iteration_candidate_queue_item(args.ids, required_status="approved")
        if not candidate:
            payload = record
        elif args.command == "iteration-candidate-plan":
            payload = iteration_candidate_plan(candidate)
        else:
            payload = iteration_candidate_apply(candidate, confirm=bool(args.confirm_apply))
    elif args.command == "iteration-candidate-validate":
        candidate, record = _iteration_candidate_queue_item(args.ids, required_status="applied")
        payload = iteration_candidate_validate(candidate) if candidate else record
    elif args.command == "iteration-candidate-recall":
        payload = iteration_candidate_recall(args.ids)
    elif args.command == "recall-checks":
        payload = recall_checks(limit=max(1, int(args.limit)))
    elif args.command == "recall-verify":
        payload = recall_verify(limit=max(1, int(args.limit)))
    elif args.command == "work-note-add":
        payload = work_note_add(args.text, source=args.source, scope=args.scope, reason=args.reason)
    elif args.command == "work-note-read":
        payload = work_note_read(limit=max(1, int(args.limit)))
    elif args.command == "work-note-dispose":
        payload = work_note_dispose(args.ids, args.disposition)
    elif args.command == "work-note-clear":
        payload = work_note_clear()
    else:
        payload = pmb_organize_plan(limit=max(1, int(args.limit)))
    print(json.dumps(json_safe(payload), ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
