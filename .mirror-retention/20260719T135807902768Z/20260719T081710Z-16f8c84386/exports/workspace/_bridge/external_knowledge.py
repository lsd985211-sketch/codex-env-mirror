#!/usr/bin/env python3
"""External knowledge capture and distillation governance.

This tool stores web/external evidence as cited review material. It does not
write PMB, user profile, or long-term memory directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from workflow_review_queue import unique_review_items


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_MEMORY_ROOT = Path.home() / "Desktop" / "Codex资源库" / "memory"
STORE_ROOT = RESOURCE_MEMORY_ROOT / "external_knowledge"
ITEMS_ROOT = STORE_ROOT / "items"
INDEX_ROOT = STORE_ROOT / "index"
INDEX_PATH = INDEX_ROOT / "external_knowledge.sqlite"
GOVERNANCE_ROOT = STORE_ROOT / "governance"
PENDING_MEMORY_CANDIDATES = GOVERNANCE_ROOT / "pending_memory_candidates.json"
AD_HOC_NOTES = Path.home() / ".codex" / "memories" / "extensions" / "ad_hoc" / "notes"
AD_HOC_ARCHIVED = Path.home() / ".codex" / "memories" / "extensions" / "ad_hoc" / "archived"

SCHEMA = "codex-external-knowledge-item/v1"
SNAPSHOT_SCHEMA = "external_knowledge.snapshot.v1"

TRUST_TIERS = {"official", "primary", "reputable", "community", "unknown"}
SOURCE_TYPES = {"official_docs", "spec", "paper", "repository", "release_notes", "article", "forum", "search_result", "other"}
SCOPES = {"global", "workspace", "project", "tool", "domain", "task"}
FRESHNESS_CLASSES = {"stable", "versioned", "volatile", "ephemeral"}
STATUSES = {"captured", "distill_candidate", "absorbed", "rejected", "expired", "test"}
REUSE_VALUES = {"auto", "low", "medium", "high"}

SECRET_PATTERNS = (
    ("github_token_shape", "high", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("openai_key_shape", "high", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("bearer_token_shape", "high", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{12,}")),
    (
        "credential_value_shape",
        "medium",
        re.compile(r"(?i)\b(api[_-]?key|private[_-]?key|password|passwd|cookie|token|secret|授权码|口令)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    ),
)

DOMAIN_DESTINATIONS = (
    (("mcp", "tool", "codex", "stdio", "transport"), "tools.mcp.stability"),
    (("email", "mail", "smtp", "imap", "inbox", "outbox"), "email.workflow"),
    (("memory", "rag", "knowledge", "provenance", "pmb"), "system.maintenance.lessons"),
    (("bridge", "weixin", "openclaw", "mobile"), "workspace.mcsmanager.operational"),
    (("skill", "template", "slash"), "skills.index"),
)

CAPTURE_DECISION_POLICY = {
    "schema": "external_knowledge.capture_decision_policy.v1",
    "decisions": {
        "capture": "Write to the external evidence store now. This is still evidence, not long-term memory.",
        "proposal_only": "Show a capture proposal or keep it in current context until a second source, clearer scope, or user approval exists.",
        "do_not_capture": "Do not persist. Use it only for the current answer or refuse storage if it contains sensitive material.",
    },
    "capture_when_all_true": [
        "source has URL/citation, title, and summary",
        "trust_tier is official, primary, or reputable",
        "reuse_value is inferred or declared as medium or high",
        "freshness_class is stable, versioned, or volatile with review date",
        "scope/domain is clear enough for later retrieval",
        "no secret-like or sensitive credential content is detected",
    ],
    "proposal_only_when_any_true": [
        "trust_tier is community or unknown",
        "source_type is forum, search_result, or other without corroboration",
        "freshness_class is ephemeral",
        "reuse_value is inferred or declared as low but the user explicitly asked to preserve it",
        "claim may conflict with existing memory or depends on current product behavior",
        "summary is useful but domain/scope is still unclear",
    ],
    "do_not_capture_when_any_true": [
        "credential, token, cookie, recovery code, authorization value, or private secret is present",
        "no citation or source can be retained",
        "content is only a one-off answer, transient search result, ad, price, or UI state",
        "raw copyrighted/private content would be stored instead of a short sourced summary",
        "the item is only useful for the current task and has no likely future retrieval value",
    ],
    "long_term_memory_boundary": "External knowledge capture never writes PMB, profile, or long-term memory directly. Absorption requires distill-plan, deduplication, and approval.",
    "self_judgement_required": "After web access, Codex must judge reuse value before capture. The default is auto inference, not a silent medium value.",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return "".join(ch if ch in "\n\r\t" or ord(ch) >= 32 else " " for ch in value)
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {}, "json_root_not_object"
    return payload, ""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_item_ids_from_archives() -> set[str]:
    ids: set[str] = set()
    if not AD_HOC_ARCHIVED.exists():
        return ids
    for manifest_path in AD_HOC_ARCHIVED.glob("*/manifest.json"):
        payload, error = read_json(manifest_path)
        if error:
            continue
        moved = payload.get("moved") if isinstance(payload.get("moved"), list) else []
        for item in moved:
            if not isinstance(item, dict):
                continue
            for value in (item.get("source"), item.get("archived")):
                name = Path(str(value or "")).name
                match = re.search(r"external-knowledge-(ek-[0-9a-fA-F-]+)\.md$", name)
                if match:
                    ids.add(match.group(1).replace("-", "_"))
    return ids


def candidate_note_paths_for_source_id(source_item_id: str) -> list[Path]:
    if not source_item_id or not AD_HOC_NOTES.exists():
        return []
    paths: list[Path] = []
    needle = f"source_item_id: {source_item_id}"
    for path in sorted(AD_HOC_NOTES.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if needle in text:
            paths.append(path)
    return paths


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate unique path for {path}")


def compact_memory_candidate_plan(plan: dict[str, Any], *, trigger: str, changed_ids: list[str] | None = None) -> dict[str, Any]:
    would_write = plan.get("would_write") if isinstance(plan.get("would_write"), list) else []
    review_items = unique_review_items([
        item
        for item in would_write
        if isinstance(item, dict)
        and str(item.get("candidate_note_status") or "") in {"existing", "written"}
        and Path(str(item.get("path") or "")).exists()
    ], kind="external_knowledge_memory_candidates", limit=20)
    materialization_required = unique_review_items([
        item
        for item in would_write
        if isinstance(item, dict)
        and str(item.get("candidate_note_status") or "") == "would_write"
        and not Path(str(item.get("path") or "")).exists()
    ], kind="external_knowledge_materialization", limit=200)
    return {
        "schema": "external_knowledge.pending_memory_candidates.v1",
        "ok": bool(plan.get("ok", True)),
        "generated_at": now_iso(),
        "trigger": trigger,
        "changed_ids": changed_ids or [],
        "candidate_count": int(plan.get("candidate_count") or 0),
        "selected_count": len(review_items),
        "would_write": review_items,
        "materialization_required_count": len(materialization_required),
        "candidate_note_refresh_command": "python _bridge\\external_knowledge.py memory-candidates --apply",
        "absorb_plan_command": "python _bridge\\memory_governance.py absorb-plan --limit 20",
        "requires_user_review": bool(review_items),
        "policy": {
            "default_action": "materialize_external_knowledge_as_candidate_notes_then_review",
            "writes_long_term_memory": False,
            "candidate_note_write_requires_apply": False,
            "closeout_auto_materializes_candidate_notes": True,
            "candidate_note_write_is_draft_layer": True,
            "long_term_absorption_requires_memory_governance_and_user_approval": True,
        },
    }


def refresh_pending_memory_candidate_plan(
    trigger: str,
    changed_ids: list[str] | None = None,
    limit: int = 20,
    *,
    apply_notes: bool = False,
) -> dict[str, Any]:
    plan = memory_candidates(argparse.Namespace(limit=limit, apply=apply_notes, refresh_pending=False))
    pending = compact_memory_candidate_plan(plan, trigger=trigger, changed_ids=changed_ids)
    write_json(PENDING_MEMORY_CANDIDATES, pending)
    return pending


def read_pending_memory_candidate_plan() -> dict[str, Any]:
    if not PENDING_MEMORY_CANDIDATES.exists():
        return {
            "schema": "external_knowledge.pending_memory_candidates.v1",
            "ok": True,
            "exists": False,
            "candidate_count": 0,
            "selected_count": 0,
            "would_write": [],
            "requires_user_review": False,
        }
    payload, error = read_json(PENDING_MEMORY_CANDIDATES)
    if error:
        return {"schema": "external_knowledge.pending_memory_candidates.v1", "ok": False, "exists": True, "error": error}
    payload["exists"] = True
    would_write = payload.get("would_write") if isinstance(payload.get("would_write"), list) else []
    active: list[dict[str, Any]] = []
    archived_or_missing: list[dict[str, Any]] = []
    for item in would_write:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        note_status = str(item.get("candidate_note_status") or "")
        if path.exists() and note_status in {"existing", "written"}:
            active.append(item)
        else:
            archived_or_missing.append({
                "source_item_id": item.get("source_item_id", ""),
                "title": item.get("title", ""),
                "path": str(path) if str(path) else "",
                "candidate_note_status": note_status,
            })
    deduped_active = unique_review_items(active, kind="external_knowledge_memory_candidates", limit=20)
    duplicate_count = max(0, len(active) - len(deduped_active))
    if archived_or_missing or duplicate_count:
        payload["would_write"] = deduped_active
        payload["selected_count"] = len(deduped_active)
        payload["requires_user_review"] = bool(deduped_active)
        payload["stale_candidate_count"] = len(archived_or_missing)
        payload["stale_candidates_filtered"] = archived_or_missing[:20]
        payload["duplicate_candidate_count"] = duplicate_count
    payload.setdefault("materialization_required_count", 0)
    return payload


def item_paths() -> list[Path]:
    if not ITEMS_ROOT.exists():
        return []
    return sorted(ITEMS_ROOT.glob("*/*.json"))


def load_items() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path in item_paths():
        payload, error = read_json(path)
        if error:
            errors.append({"path": str(path), "error": error})
        else:
            payload["_path"] = str(path)
            items.append(payload)
    return items, errors


def normalized_url(item: dict[str, Any]) -> str:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return str(source.get("url") or "").strip().lower()


def classify_sensitive(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for code, severity, pattern in SECRET_PATTERNS:
        if pattern.search(text or ""):
            hits.append({"code": code, "severity": severity})
    return hits


def infer_reuse_value(
    *,
    title: str,
    summary: str,
    domain: str,
    source_type: str,
    trust_tier: str,
    freshness_class: str,
    key_points: list[str] | None = None,
    user_requested: bool = False,
) -> dict[str, Any]:
    text = " ".join([title or "", summary or "", domain or "", " ".join(key_points or [])]).lower()
    high_markers = (
        "best practice",
        "architecture",
        "api",
        "protocol",
        "schema",
        "security",
        "mcp",
        "codex",
        "memory",
        "pmb",
        "tool",
        "governance",
        "doctor",
        "validate",
        "maintenance",
        "workflow",
        "automation",
        "version",
        "官方",
        "规范",
        "协议",
        "架构",
        "工具",
        "记忆",
        "治理",
        "维护",
        "安全",
        "流程",
    )
    low_markers = (
        "price",
        "pricing",
        "discount",
        "news",
        "today",
        "current ui",
        "temporary",
        "one-off",
        "search snippet",
        "广告",
        "价格",
        "新闻",
        "今天",
        "临时",
        "一次性",
        "当前界面",
    )
    reasons: list[str] = []
    high_score = sum(1 for marker in high_markers if marker in text)
    low_score = sum(1 for marker in low_markers if marker in text)
    if trust_tier in {"official", "primary"} and source_type in {"official_docs", "spec", "repository", "release_notes"}:
        high_score += 1
        reasons.append("primary_or_official_source")
    if freshness_class in {"stable", "versioned"}:
        high_score += 1
        reasons.append(f"durable_freshness:{freshness_class}")
    if user_requested:
        high_score += 1
        reasons.append("user_requested_preservation")
    if high_score >= 3 and low_score == 0:
        value = "high"
    elif low_score >= 2 and high_score == 0:
        value = "low"
    elif high_score >= 1 and low_score <= 1:
        value = "medium"
    else:
        value = "low"
    if high_score:
        reasons.append(f"high_marker_score:{high_score}")
    if low_score:
        reasons.append(f"low_marker_score:{low_score}")
    return {
        "value": value,
        "mode": "auto",
        "reasons": reasons or ["no_reuse_markers_found"],
        "scores": {"high": high_score, "low": low_score},
    }


def capture_decision_from_fields(
    *,
    url: str,
    title: str,
    summary: str,
    source_type: str,
    trust_tier: str,
    scope: str,
    freshness_class: str,
    reuse_value: str,
    domain: str,
    key_points: list[str] | None = None,
    raw_excerpt: str = "",
    user_requested: bool = False,
    corroborated: bool = False,
) -> dict[str, Any]:
    text_for_scan = "\n".join([url or "", title or "", summary or "", raw_excerpt or "", *(key_points or [])])
    sensitive_hits = classify_sensitive(text_for_scan)
    inferred_reuse = infer_reuse_value(
        title=title,
        summary=summary,
        domain=domain,
        source_type=source_type,
        trust_tier=trust_tier,
        freshness_class=freshness_class,
        key_points=key_points,
        user_requested=user_requested,
    )
    effective_reuse_value = inferred_reuse["value"] if reuse_value == "auto" else reuse_value
    reasons: list[str] = []
    blockers: list[str] = []
    advisories: list[str] = []

    has_citation = bool(url)
    has_required_text = bool(title and summary)
    clear_scope = scope in SCOPES and bool(domain or scope in {"global", "tool", "domain", "project"})
    trusted = trust_tier in {"official", "primary", "reputable"}
    low_trust = trust_tier in {"community", "unknown"} or source_type in {"forum", "search_result", "other"}
    reusable = effective_reuse_value in {"medium", "high"}
    secret_like = any(hit.get("severity") in {"high", "medium"} for hit in sensitive_hits)

    if secret_like:
        blockers.append("secret_or_credential_like_content")
    if not has_citation:
        blockers.append("citation_missing")
    if not has_required_text:
        blockers.append("title_or_summary_missing")
    if effective_reuse_value == "low" and not user_requested:
        blockers.append("low_reuse_without_user_request")

    if low_trust and not corroborated:
        advisories.append("low_trust_requires_corroboration")
    if freshness_class == "ephemeral":
        advisories.append("ephemeral_information_should_not_auto_capture")
    if not clear_scope:
        advisories.append("scope_or_domain_unclear")
    if raw_excerpt and len(raw_excerpt) > 1200:
        advisories.append("raw_excerpt_too_long_prefer_summary")

    if blockers:
        decision = "do_not_capture"
        reasons.extend(blockers)
    elif trusted and reusable and freshness_class != "ephemeral" and clear_scope:
        decision = "capture"
        reasons.extend(["trusted_reusable_sourced_evidence", f"trust_tier:{trust_tier}", f"reuse_value:{effective_reuse_value}"])
    else:
        decision = "proposal_only"
        reasons.extend(advisories or ["needs_human_or_additional_context"])

    if decision == "proposal_only" and user_requested and not secret_like and has_citation and has_required_text:
        reasons.append("user_requested_preservation_but_capture_not_auto_safe")

    return {
        "schema": "external_knowledge.capture_decision.v1",
        "decision": decision,
        "reasons": reasons,
        "blockers": blockers,
        "advisories": advisories,
        "inputs": {
            "source_type": source_type,
            "trust_tier": trust_tier,
            "scope": scope,
            "freshness_class": freshness_class,
            "reuse_value": effective_reuse_value,
            "requested_reuse_value": reuse_value,
            "reuse_value_judgement": inferred_reuse,
            "user_requested": user_requested,
            "corroborated": corroborated,
            "has_citation": has_citation,
            "clear_scope": clear_scope,
        },
        "sensitive_hits": sensitive_hits,
        "next_step": {
            "capture": "May write to external_knowledge evidence store; long-term memory still requires distill-plan.",
            "proposal_only": "Show capture proposal or gather stronger source/corroboration before writing.",
            "do_not_capture": "Do not persist; keep only in current task context or discard.",
        }[decision],
    }


def next_review_at(freshness_class: str) -> str | None:
    days = {
        "stable": 365,
        "versioned": 180,
        "volatile": 30,
        "ephemeral": 7,
    }.get(freshness_class)
    if not days:
        return None
    return (now_utc() + timedelta(days=days)).isoformat()


def make_id(url: str, title: str, summary: str) -> str:
    stamp = now_utc().strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha256(f"{url}\n{title}\n{summary}\n{stamp}".encode("utf-8")).hexdigest()[:10]
    return f"ek_{stamp}_{digest}"


def validate_enum(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise SystemExit(f"{name} must be one of: {', '.join(sorted(allowed))}")


def build_item(args: argparse.Namespace) -> dict[str, Any]:
    for name, allowed in [
        ("trust_tier", TRUST_TIERS),
        ("source_type", SOURCE_TYPES),
        ("scope", SCOPES),
        ("freshness_class", FRESHNESS_CLASSES),
        ("status", STATUSES),
        ("reuse_value", REUSE_VALUES),
    ]:
        validate_enum(name, getattr(args, name), allowed)
    text_for_scan = "\n".join([args.url or "", args.title or "", args.summary or "", *(args.key_point or [])])
    sensitive_hits = classify_sensitive(text_for_scan)
    decision = capture_decision_from_fields(
        url=args.url,
        title=args.title,
        summary=args.summary,
        source_type=args.source_type,
        trust_tier=args.trust_tier,
        scope=args.scope,
        freshness_class=args.freshness_class,
        reuse_value=args.reuse_value,
        domain=args.domain,
        key_points=args.key_point or [],
        raw_excerpt=args.raw_excerpt or "",
        user_requested=bool(args.user_requested),
        corroborated=bool(args.corroborated),
    )
    decision_inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
    item_id = make_id(args.url, args.title, args.summary)
    captured_at = now_iso()
    return {
        "schema": SCHEMA,
        "id": item_id,
        "captured_at": captured_at,
        "source": {
            "url": args.url,
            "title": args.title,
            "publisher": args.publisher or "",
            "source_type": args.source_type,
            "retrieved_at": args.retrieved_at or captured_at,
        },
        "classification": {
            "trust_tier": args.trust_tier,
            "scope": args.scope,
            "freshness_class": args.freshness_class,
            "domain": args.domain or "",
            "language": args.language or "unknown",
            "reuse_value": decision_inputs.get("reuse_value", args.reuse_value),
            "requested_reuse_value": args.reuse_value,
            "reuse_value_judgement": decision_inputs.get("reuse_value_judgement"),
        },
        "content": {
            "summary": args.summary,
            "key_points": args.key_point or [],
            "raw_excerpt": args.raw_excerpt or "",
        },
        "provenance": {
            "captured_by": "codex",
            "tool": args.tool or "web_search",
            "query": args.query or "",
            "citations": [args.url] if args.url else [],
        },
        "review": {
            "status": args.status,
            "confidence": args.confidence,
            "next_review_at": args.next_review_at or next_review_at(args.freshness_class),
            "absorption_requires_approval": True,
            "capture_decision": decision,
        },
        "safety": {
            "secret_forbidden": True,
            "sensitive_hits": sensitive_hits,
        },
        "memory_candidates": [],
    }


def capture(args: argparse.Namespace) -> dict[str, Any]:
    item = build_item(args)
    decision = ((item.get("review") or {}).get("capture_decision") or {}).get("decision")
    if decision in {"do_not_capture", "proposal_only"} and not args.force:
        return {
            "schema": "external_knowledge.capture.v1",
            "ok": False,
            "dry_run": bool(args.dry_run),
            "reason": "capture_decision_rejected" if decision == "do_not_capture" else "capture_decision_requires_proposal_or_approval",
            "decision": (item.get("review") or {}).get("capture_decision"),
        }
    if args.dry_run:
        return {
            "schema": "external_knowledge.capture.v1",
            "ok": True,
            "dry_run": True,
            "item": item,
            "decision": (item.get("review") or {}).get("capture_decision"),
            "would_write": str(ITEMS_ROOT / now_utc().strftime("%Y%m") / f"{item['id']}.json"),
        }
    month_dir = ITEMS_ROOT / now_utc().strftime("%Y%m")
    path = month_dir / f"{item['id']}.json"
    write_json(path, item)
    pending_plan = refresh_pending_memory_candidate_plan("capture", [str(item["id"])])
    return {
        "schema": "external_knowledge.capture.v1",
        "ok": True,
        "dry_run": False,
        "path": str(path),
        "id": item["id"],
        "decision": (item.get("review") or {}).get("capture_decision"),
        "sensitive_hit_count": len(item["safety"]["sensitive_hits"]),
        "pending_memory_candidates": pending_plan,
    }


def summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_trust: dict[str, int] = {}
    by_freshness: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for item in items:
        cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
        review = item.get("review") if isinstance(item.get("review"), dict) else {}
        by_trust[str(cls.get("trust_tier") or "unknown")] = by_trust.get(str(cls.get("trust_tier") or "unknown"), 0) + 1
        by_freshness[str(cls.get("freshness_class") or "unknown")] = by_freshness.get(str(cls.get("freshness_class") or "unknown"), 0) + 1
        by_status[str(review.get("status") or "unknown")] = by_status.get(str(review.get("status") or "unknown"), 0) + 1
    return {"by_trust": by_trust, "by_freshness": by_freshness, "by_status": by_status}


def snapshot(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    items, errors = load_items()
    recent = sorted(items, key=lambda item: str(item.get("captured_at") or ""), reverse=True)[:10]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "ok": not errors,
        "generated_at": now_iso(),
        "store_root": str(STORE_ROOT),
        "items_root": str(ITEMS_ROOT),
        "index_path": str(INDEX_PATH),
        "store_exists": STORE_ROOT.exists(),
        "item_count": len(items),
        "json_error_count": len(errors),
        "errors": errors[:10],
        "summary": summarize_items(items),
        "recent_items": [
            {
                "id": item.get("id"),
                "title": (item.get("source") or {}).get("title") if isinstance(item.get("source"), dict) else "",
                "trust_tier": (item.get("classification") or {}).get("trust_tier") if isinstance(item.get("classification"), dict) else "",
                "freshness_class": (item.get("classification") or {}).get("freshness_class") if isinstance(item.get("classification"), dict) else "",
                "status": (item.get("review") or {}).get("status") if isinstance(item.get("review"), dict) else "",
                "path": item.get("_path"),
            }
            for item in recent
        ],
    }


def item_issues(item: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
    review = item.get("review") if isinstance(item.get("review"), dict) else {}
    safety = item.get("safety") if isinstance(item.get("safety"), dict) else {}
    if item.get("schema") != SCHEMA:
        issues.append({"severity": "risk", "code": "schema_mismatch", "id": item.get("id")})
    if not source.get("url") and not (item.get("provenance") or {}).get("citations"):
        issues.append({"severity": "risk", "code": "citation_missing", "id": item.get("id")})
    if cls.get("trust_tier") not in TRUST_TIERS:
        issues.append({"severity": "risk", "code": "trust_tier_missing", "id": item.get("id")})
    if cls.get("freshness_class") not in FRESHNESS_CLASSES:
        issues.append({"severity": "risk", "code": "freshness_class_missing", "id": item.get("id")})
    sensitive_hits = safety.get("sensitive_hits") if isinstance(safety.get("sensitive_hits"), list) else []
    high_hits = [hit for hit in sensitive_hits if isinstance(hit, dict) and hit.get("severity") == "high"]
    if high_hits:
        issues.append({"severity": "risk", "code": "secret_like_content_detected", "id": item.get("id"), "hits": high_hits})
    if cls.get("trust_tier") in {"community", "unknown"} and review.get("status") in {"captured", "distill_candidate"}:
        issues.append({"severity": "advisory", "code": "low_trust_requires_corroboration", "id": item.get("id")})
    next_review = str(review.get("next_review_at") or "")
    if next_review:
        try:
            if datetime.fromisoformat(next_review) < now_utc():
                issues.append({"severity": "advisory", "code": "review_overdue", "id": item.get("id"), "next_review_at": next_review})
        except ValueError:
            issues.append({"severity": "advisory", "code": "next_review_at_invalid", "id": item.get("id"), "next_review_at": next_review})
    return issues


def doctor(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    snap = snapshot()
    items, errors = load_items()
    issues: list[dict[str, Any]] = []
    if not STORE_ROOT.exists():
        issues.append({"severity": "advisory", "code": "external_knowledge_store_missing", "message": "External knowledge store has not been initialized yet."})
    for error in errors:
        issues.append({"severity": "risk", "code": "item_json_unreadable", **error})
    for item in items:
        issues.extend(item_issues(item))
    severities = {issue.get("severity") for issue in issues}
    status = "risk" if "risk" in severities else "advisory" if issues else "ok"
    return {
        "schema": "external_knowledge.doctor.v1",
        "ok": status != "risk",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "summary": {
            "item_count": snap.get("item_count", 0),
            "risk_count": sum(1 for issue in issues if issue.get("severity") == "risk"),
            "advisory_count": sum(1 for issue in issues if issue.get("severity") == "advisory"),
        },
    }


def destination_for(item: dict[str, Any]) -> str:
    cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
    haystack = " ".join(
        [
            str(cls.get("domain") or ""),
            str((item.get("source") or {}).get("title") if isinstance(item.get("source"), dict) else ""),
            str((item.get("content") or {}).get("summary") if isinstance(item.get("content"), dict) else ""),
        ]
    ).lower()
    for keywords, destination in DOMAIN_DESTINATIONS:
        if any(keyword in haystack for keyword in keywords):
            return destination
    return "workspace.mcsmanager.operational"


def capture_policy(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    return CAPTURE_DECISION_POLICY


def capture_decision(args: argparse.Namespace) -> dict[str, Any]:
    decision = capture_decision_from_fields(
        url=args.url,
        title=args.title,
        summary=args.summary,
        source_type=args.source_type,
        trust_tier=args.trust_tier,
        scope=args.scope,
        freshness_class=args.freshness_class,
        reuse_value=args.reuse_value,
        domain=args.domain,
        key_points=args.key_point or [],
        raw_excerpt=args.raw_excerpt or "",
        user_requested=bool(args.user_requested),
        corroborated=bool(args.corroborated),
    )
    return {
        **decision,
        "schema": "external_knowledge.capture_decision_result.v1",
        "ok": True,
        "generated_at": now_iso(),
        "policy_schema": CAPTURE_DECISION_POLICY["schema"],
    }


def distill_plan(args: argparse.Namespace) -> dict[str, Any]:
    items, errors = load_items()
    candidates: list[dict[str, Any]] = []
    archived_source_ids = source_item_ids_from_archives()
    skipped_absorbed: list[dict[str, str]] = []
    for item in items:
        if len(candidates) >= args.limit:
            break
        source_item_id = str(item.get("id") or "")
        issues = item_issues(item)
        risk_codes = [issue.get("code") for issue in issues if issue.get("severity") == "risk"]
        cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
        review = item.get("review") if isinstance(item.get("review"), dict) else {}
        if review.get("status") == "absorbed" or source_item_id in archived_source_ids:
            skipped_absorbed.append({"source_item_id": source_item_id, "reason": "already_absorbed_or_archived"})
            continue
        if review.get("status") not in {"captured", "distill_candidate", "test"}:
            continue
        keep = not risk_codes and cls.get("trust_tier") in {"official", "primary", "reputable"}
        candidates.append(
            {
                "source_item_id": source_item_id,
                "source_url": (item.get("source") or {}).get("url") if isinstance(item.get("source"), dict) else "",
                "title": (item.get("source") or {}).get("title") if isinstance(item.get("source"), dict) else "",
                "summary": (item.get("content") or {}).get("summary") if isinstance(item.get("content"), dict) else "",
                "proposed_destination_namespace": destination_for(item),
                "keep": keep,
                "exclude_reason": ";".join(risk_codes) if risk_codes else "",
                "requires_approval": True,
                "required_checks": [
                    "verify citation still reachable when freshness is volatile/versioned",
                    "deduplicate against existing PMB and ad hoc notes",
                    "store only distilled rule, not raw webpage content",
                ],
                "freshness_class": cls.get("freshness_class"),
                "trust_tier": cls.get("trust_tier"),
            }
        )
    return {
        "schema": "external_knowledge.distill_plan.v1",
        "ok": not errors,
        "generated_at": now_iso(),
        "dry_run": True,
        "candidate_count": len(candidates),
        "json_error_count": len(errors),
        "skipped_absorbed_count": len(skipped_absorbed),
        "skipped_absorbed": skipped_absorbed[:20],
        "candidates": candidates,
    }


def slugify(value: str, fallback: str = "external-knowledge") -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value or "").strip("-").lower()
    return (slug[:64].strip("-") or fallback)


def memory_candidate_note(candidate: dict[str, Any]) -> str:
    title = str(candidate.get("title") or candidate.get("source_item_id") or "external knowledge candidate")
    source_url = str(candidate.get("source_url") or "")
    summary = str(candidate.get("summary") or "")
    destination = str(candidate.get("proposed_destination_namespace") or "system.maintenance.lessons")
    trust_tier = str(candidate.get("trust_tier") or "")
    freshness_class = str(candidate.get("freshness_class") or "")
    source_item_id = str(candidate.get("source_item_id") or "")
    return "\n".join(
        [
            f"# External knowledge absorption candidate: {title}",
            "",
            "## Source",
            f"- source_item_id: {source_item_id}",
            f"- url: {source_url}",
            f"- trust_tier: {trust_tier}",
            f"- freshness_class: {freshness_class}",
            "",
            "## Proposed Destination",
            f"- namespace: {destination}",
            "",
            "## Stable Points To Review",
            f"- {summary or 'Review the source item and keep only stable, reusable conclusions.'}",
            "",
            "## Exclude By Default",
            "- raw webpage content",
            "- one-off task context",
            "- volatile current-state claims unless revalidated",
            "- secrets, credentials, cookies, tokens, and private data",
            "",
            "## Absorption Policy",
            "- This note is a candidate only.",
            "- Run memory_governance absorb-plan before any long-term memory write.",
            "- Long-term absorption requires user approval and post-apply validation.",
            "",
        ]
    )


def existing_candidate_note_path(source_item_id: str) -> Path | None:
    if not source_item_id or not AD_HOC_NOTES.exists():
        return None
    needle = f"source_item_id: {source_item_id}"
    for path in sorted(AD_HOC_NOTES.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if needle in text:
            return path
    return None


def candidate_note_path(source_item_id: str, title: str) -> Path:
    existing = existing_candidate_note_path(source_item_id)
    if existing:
        return existing
    return AD_HOC_NOTES / f"external-knowledge-{slugify(source_item_id or title)}.md"


def memory_candidates(args: argparse.Namespace) -> dict[str, Any]:
    plan = distill_plan(argparse.Namespace(limit=args.limit))
    candidates = [
        item
        for item in plan.get("candidates", [])
        if isinstance(item, dict) and bool(item.get("keep")) and not item.get("exclude_reason")
    ]
    selected = candidates[: max(0, int(args.limit))]
    would_write: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []
    if args.apply:
        AD_HOC_NOTES.mkdir(parents=True, exist_ok=True)
    for index, candidate in enumerate(selected, start=1):
        source_item_id = str(candidate.get("source_item_id") or f"item-{index}")
        title = str(candidate.get("title") or source_item_id)
        path = candidate_note_path(source_item_id, title)
        item = {
            "source_item_id": source_item_id,
            "title": title,
            "summary": candidate.get("summary"),
            "source_url": candidate.get("source_url"),
            "trust_tier": candidate.get("trust_tier"),
            "freshness_class": candidate.get("freshness_class"),
            "path": str(path),
            "proposed_destination_namespace": candidate.get("proposed_destination_namespace"),
            "approval_action": "review_absorb_plan_then_apply_approved",
            "required_checks": candidate.get("required_checks", []),
            "candidate_note_status": "existing" if path.exists() else ("written" if args.apply else "would_write"),
        }
        would_write.append(item)
        if args.apply:
            if path.exists():
                written.append({**item, "candidate_note_status": "existing"})
            else:
                path.write_text(memory_candidate_note(candidate), encoding="utf-8")
                written.append({**item, "candidate_note_status": "written"})
    payload = {
        "schema": "external_knowledge.memory_candidates.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply": bool(args.apply),
        "source": "external_knowledge.distill_plan",
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "would_write": would_write,
        "written": written,
        "next_step": "Run `python _bridge\\memory_governance.py absorb-plan --limit 20`; approve or reject the absorption plan once.",
        "policy": {
            "writes_long_term_memory": False,
            "candidate_notes_are_draft_layer": True,
            "candidate_notes_are_idempotent_by_source_item_id": True,
            "writes_candidate_notes_only_when_apply": True,
            "requires_absorb_plan_before_long_term_memory": True,
            "requires_user_approval_for_long_term_apply": True,
        },
    }
    if getattr(args, "refresh_pending", True):
        payload["pending_plan"] = compact_memory_candidate_plan(payload, trigger="memory-candidates-apply" if args.apply else "memory-candidates-dry-run")
        write_json(PENDING_MEMORY_CANDIDATES, payload["pending_plan"])
    return payload


def rebuild_index(args: argparse.Namespace) -> dict[str, Any]:
    items, errors = load_items()
    if args.dry_run:
        return {
            "schema": "external_knowledge.index.v1",
            "ok": not errors,
            "dry_run": True,
            "would_index_count": len(items),
            "json_error_count": len(errors),
            "index_path": str(INDEX_PATH),
        }
    INDEX_ROOT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(INDEX_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_knowledge (
                id TEXT PRIMARY KEY,
                captured_at TEXT,
                title TEXT,
                url TEXT,
                trust_tier TEXT,
                freshness_class TEXT,
                status TEXT,
                domain TEXT,
                summary TEXT,
                path TEXT
            )
            """
        )
        conn.execute("DELETE FROM external_knowledge")
        for item in items:
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
            review = item.get("review") if isinstance(item.get("review"), dict) else {}
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            conn.execute(
                """
                INSERT OR REPLACE INTO external_knowledge
                (id, captured_at, title, url, trust_tier, freshness_class, status, domain, summary, path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("id"),
                    item.get("captured_at"),
                    source.get("title"),
                    source.get("url"),
                    cls.get("trust_tier"),
                    cls.get("freshness_class"),
                    review.get("status"),
                    cls.get("domain"),
                    content.get("summary"),
                    item.get("_path"),
                ),
            )
        conn.commit()
    return {
        "schema": "external_knowledge.index.v1",
        "ok": not errors,
        "dry_run": False,
        "indexed_count": len(items),
        "json_error_count": len(errors),
        "index_path": str(INDEX_PATH),
    }


def query(args: argparse.Namespace) -> dict[str, Any]:
    term = (args.term or "").lower()
    items, errors = load_items()
    matches: list[dict[str, Any]] = []
    for item in items:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        cls = item.get("classification") if isinstance(item.get("classification"), dict) else {}
        review = item.get("review") if isinstance(item.get("review"), dict) else {}
        haystack = json.dumps([source, content, cls], ensure_ascii=False).lower()
        if term and term not in haystack:
            continue
        matches.append(
            {
                "id": item.get("id"),
                "title": source.get("title"),
                "url": source.get("url"),
                "summary": content.get("summary"),
                "trust_tier": cls.get("trust_tier"),
                "freshness_class": cls.get("freshness_class"),
                "status": review.get("status"),
                "duplicate_of": review.get("duplicate_of"),
                "path": item.get("_path"),
            }
        )
        if len(matches) >= args.limit:
            break
    return {
        "schema": "external_knowledge.query.v1",
        "ok": not errors,
        "generated_at": now_iso(),
        "term": args.term,
        "match_count": len(matches),
        "json_error_count": len(errors),
        "matches": matches,
    }


def validate(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    snap = snapshot()
    doc = doctor()
    checks = [
        {"name": "store_root_available", "ok": STORE_ROOT.exists(), "detail": str(STORE_ROOT)},
        {"name": "items_root_available", "ok": ITEMS_ROOT.exists(), "detail": str(ITEMS_ROOT)},
        {"name": "doctor_no_risk", "ok": bool(doc.get("ok")), "detail": doc.get("status")},
        {"name": "distill_plan_available", "ok": bool(distill_plan(argparse.Namespace(limit=20)).get("ok")), "detail": "external_knowledge distill-plan"},
        {"name": "memory_candidates_available", "ok": bool(memory_candidates(argparse.Namespace(limit=1, apply=False, refresh_pending=False)).get("ok")), "detail": "external_knowledge memory-candidates"},
        {"name": "pending_memory_candidates_available", "ok": read_pending_memory_candidate_plan().get("schema") == "external_knowledge.pending_memory_candidates.v1", "detail": str(PENDING_MEMORY_CANDIDATES)},
        {"name": "capture_decision_policy_available", "ok": capture_policy().get("schema") == "external_knowledge.capture_decision_policy.v1", "detail": "external_knowledge capture-policy"},
    ]
    failed = [check for check in checks if not check["ok"]]
    return {
        "schema": "external_knowledge.validate.v1",
        "ok": not failed,
        "generated_at": now_iso(),
        "checks": checks,
        "item_count": snap.get("item_count", 0),
        "doctor_status": doc.get("status"),
    }


def metrics(_args: argparse.Namespace | None = None) -> dict[str, Any]:
    snap = snapshot()
    doc = doctor()
    return {
        "schema": "external_knowledge.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "item_count": snap.get("item_count", 0),
        "json_error_count": snap.get("json_error_count", 0),
        "risk_count": (doc.get("summary") or {}).get("risk_count", 0),
        "advisory_count": (doc.get("summary") or {}).get("advisory_count", 0),
        "summary": snap.get("summary", {}),
    }


def dedup_url(args: argparse.Namespace) -> dict[str, Any]:
    items, errors = load_items()
    groups: dict[str, list[dict[str, Any]]] = {}
    target_url = str(args.url or "").strip().lower()
    for item in items:
        url = normalized_url(item)
        if not url:
            continue
        if target_url and url != target_url:
            continue
        groups.setdefault(url, []).append(item)

    duplicate_groups = {url: rows for url, rows in groups.items() if len(rows) > 1}
    planned: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    now = now_iso()
    for url, rows in sorted(duplicate_groups.items()):
        ordered = sorted(rows, key=lambda item: str(item.get("captured_at") or ""))
        keep = ordered[0]
        duplicates = ordered[1:]
        group_plan = {
            "url": url,
            "keep_id": keep.get("id"),
            "keep_path": keep.get("_path"),
            "duplicate_count": len(duplicates),
            "duplicates": [
                {
                    "id": item.get("id"),
                    "path": item.get("_path"),
                    "status": (item.get("review") or {}).get("status") if isinstance(item.get("review"), dict) else "",
                }
                for item in duplicates
            ],
        }
        planned.append(group_plan)
        if not args.apply:
            continue
        for item in duplicates:
            path = Path(str(item.get("_path") or ""))
            review = item.setdefault("review", {})
            if not isinstance(review, dict):
                review = {}
                item["review"] = review
            previous_status = str(review.get("status") or "")
            if previous_status == "absorbed":
                continue
            review["previous_status_before_dedup"] = previous_status
            review["status"] = "rejected"
            review["duplicate_of"] = keep.get("id")
            review["deduped_at"] = now
            review["dedup_reason"] = "same_source_url_merged_preserving_evidence"
            provenance = item.setdefault("provenance", {})
            if isinstance(provenance, dict):
                provenance["merged_into"] = keep.get("id")
            if path.exists():
                write_json(path, item)
            changed.append({"id": item.get("id"), "path": str(path), "duplicate_of": keep.get("id")})

    index_result: dict[str, Any] | None = None
    if args.apply and changed:
        index_result = rebuild_index(argparse.Namespace(dry_run=False))
        pending_plan = refresh_pending_memory_candidate_plan("dedup-url", [str(item.get("id") or "") for item in changed])
    else:
        pending_plan = None
    return {
        "schema": "external_knowledge.dedup_url.v1",
        "ok": not errors,
        "generated_at": now,
        "dry_run": not bool(args.apply),
        "duplicate_group_count": len(planned),
        "planned_duplicate_count": sum(int(item.get("duplicate_count") or 0) for item in planned),
        "changed_count": len(changed),
        "groups": planned,
        "changed": changed,
        "json_error_count": len(errors),
        "index_result": index_result,
        "pending_memory_candidates": pending_plan,
        "policy": "Default is dry-run. Apply preserves every item file and marks duplicate evidence rejected with duplicate_of instead of deleting it.",
    }


def mark_absorbed(args: argparse.Namespace) -> dict[str, Any]:
    requested = {str(item).strip() for item in (args.source_item_id or []) if str(item).strip()}
    if not requested:
        return {
            "schema": "external_knowledge.mark_absorbed.v1",
            "ok": False,
            "reason": "no_source_item_id",
            "dry_run": not bool(args.apply),
        }
    items, errors = load_items()
    now = now_iso()
    changed: list[dict[str, Any]] = []
    missing = sorted(requested)
    for item in items:
        item_id = str(item.get("id") or "")
        if item_id not in requested:
            continue
        if item_id in missing:
            missing.remove(item_id)
        path = Path(str(item.get("_path") or ""))
        review = item.setdefault("review", {})
        if not isinstance(review, dict):
            review = {}
            item["review"] = review
        previous_status = str(review.get("status") or "")
        changed_item = {
            "id": item_id,
            "path": str(path),
            "previous_status": previous_status,
            "new_status": "absorbed",
        }
        changed.append(changed_item)
        if args.apply:
            review["previous_status_before_absorbed"] = previous_status
            review["status"] = "absorbed"
            review["absorbed_at"] = now
            review["absorbed_batch_id"] = args.batch_id or ""
            review["absorbed_by"] = args.absorbed_by or "memory_governance.apply_approved"
            write_json(path, item)

    archived_notes: list[dict[str, str]] = []
    if args.apply and args.archive_candidate_notes:
        archive_dir = AD_HOC_ARCHIVED / (args.batch_id or now.replace(":", "").replace("+", "-"))
        for source_item_id in sorted(requested):
            for path in candidate_note_paths_for_source_id(source_item_id):
                archive_dir.mkdir(parents=True, exist_ok=True)
                target = unique_path(archive_dir / path.name)
                path.replace(target)
                archived_notes.append({"source": str(path), "archived": str(target), "source_item_id": source_item_id})
        if archived_notes:
            manifest_path = archive_dir / "external_knowledge_absorbed_manifest.json"
            write_json(
                manifest_path,
                {
                    "schema": "external_knowledge.mark_absorbed_archive.v1",
                    "created_at": now,
                    "batch_id": args.batch_id or "",
                    "moved": archived_notes,
                    "restore": "Move files back to ad_hoc notes only if rollback is required.",
                },
            )

    index_result: dict[str, Any] | None = None
    pending_plan: dict[str, Any] | None = None
    if args.apply and changed:
        index_result = rebuild_index(argparse.Namespace(dry_run=False))
        pending_plan = refresh_pending_memory_candidate_plan("mark-absorbed", [item["id"] for item in changed])
    return {
        "schema": "external_knowledge.mark_absorbed.v1",
        "ok": not errors and not missing,
        "generated_at": now,
        "dry_run": not bool(args.apply),
        "requested_count": len(requested),
        "changed_count": len(changed),
        "changed": changed,
        "missing": missing,
        "json_error_count": len(errors),
        "archive_candidate_notes": bool(args.archive_candidate_notes),
        "archived_note_count": len(archived_notes),
        "archived_notes": archived_notes,
        "index_result": index_result,
        "pending_memory_candidates": pending_plan,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture and govern sourced external knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--url", required=True)
    capture_parser.add_argument("--title", required=True)
    capture_parser.add_argument("--source-type", default="other", choices=sorted(SOURCE_TYPES))
    capture_parser.add_argument("--trust-tier", default="unknown", choices=sorted(TRUST_TIERS))
    capture_parser.add_argument("--scope", default="domain", choices=sorted(SCOPES))
    capture_parser.add_argument("--freshness-class", default="versioned", choices=sorted(FRESHNESS_CLASSES))
    capture_parser.add_argument("--summary", required=True)
    capture_parser.add_argument("--key-point", action="append", default=[])
    capture_parser.add_argument("--raw-excerpt", default="")
    capture_parser.add_argument("--domain", default="")
    capture_parser.add_argument("--publisher", default="")
    capture_parser.add_argument("--language", default="unknown")
    capture_parser.add_argument("--retrieved-at", default="")
    capture_parser.add_argument("--next-review-at", default="")
    capture_parser.add_argument("--query", default="")
    capture_parser.add_argument("--tool", default="")
    capture_parser.add_argument("--confidence", type=float, default=0.7)
    capture_parser.add_argument("--status", default="captured", choices=sorted(STATUSES))
    capture_parser.add_argument("--reuse-value", default="auto", choices=sorted(REUSE_VALUES))
    capture_parser.add_argument("--user-requested", action="store_true")
    capture_parser.add_argument("--corroborated", action="store_true")
    capture_parser.add_argument("--force", action="store_true", help="Override do_not_capture after explicit human approval")
    capture_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("snapshot")
    sub.add_parser("doctor")
    sub.add_parser("validate")
    sub.add_parser("metrics")
    sub.add_parser("capture-policy")
    decision_parser = sub.add_parser("capture-decision")
    decision_parser.add_argument("--url", default="")
    decision_parser.add_argument("--title", default="")
    decision_parser.add_argument("--source-type", default="other", choices=sorted(SOURCE_TYPES))
    decision_parser.add_argument("--trust-tier", default="unknown", choices=sorted(TRUST_TIERS))
    decision_parser.add_argument("--scope", default="domain", choices=sorted(SCOPES))
    decision_parser.add_argument("--freshness-class", default="versioned", choices=sorted(FRESHNESS_CLASSES))
    decision_parser.add_argument("--summary", default="")
    decision_parser.add_argument("--key-point", action="append", default=[])
    decision_parser.add_argument("--raw-excerpt", default="")
    decision_parser.add_argument("--domain", default="")
    decision_parser.add_argument("--reuse-value", default="auto", choices=sorted(REUSE_VALUES))
    decision_parser.add_argument("--user-requested", action="store_true")
    decision_parser.add_argument("--corroborated", action="store_true")
    distill = sub.add_parser("distill-plan")
    distill.add_argument("--limit", type=int, default=20)
    memory = sub.add_parser("memory-candidates")
    memory.add_argument("--limit", type=int, default=20)
    memory.add_argument("--apply", action="store_true", help="Write candidate notes for later memory_governance absorb-plan review")
    sub.add_parser("pending-memory-candidates")
    index = sub.add_parser("index")
    index.add_argument("--dry-run", action="store_true")
    query_parser = sub.add_parser("query")
    query_parser.add_argument("--term", required=True)
    query_parser.add_argument("--limit", type=int, default=20)
    dedup = sub.add_parser("dedup-url")
    dedup.add_argument("--url", default="", help="Restrict deduplication to one normalized URL")
    dedup.add_argument("--apply", action="store_true", help="Apply reviewed duplicate status changes")
    absorbed = sub.add_parser("mark-absorbed")
    absorbed.add_argument("--source-item-id", action="append", default=[])
    absorbed.add_argument("--batch-id", default="")
    absorbed.add_argument("--absorbed-by", default="memory_governance.apply_approved")
    absorbed.add_argument("--archive-candidate-notes", action="store_true")
    absorbed.add_argument("--apply", action="store_true", help="Mark reviewed external knowledge source items as absorbed")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "capture":
        payload = capture(args)
    elif args.command == "snapshot":
        payload = snapshot(args)
    elif args.command == "doctor":
        payload = doctor(args)
    elif args.command == "validate":
        payload = validate(args)
    elif args.command == "metrics":
        payload = metrics(args)
    elif args.command == "capture-policy":
        payload = capture_policy(args)
    elif args.command == "capture-decision":
        payload = capture_decision(args)
    elif args.command == "distill-plan":
        payload = distill_plan(args)
    elif args.command == "memory-candidates":
        payload = memory_candidates(args)
    elif args.command == "pending-memory-candidates":
        payload = read_pending_memory_candidate_plan()
    elif args.command == "index":
        payload = rebuild_index(args)
    elif args.command == "query":
        payload = query(args)
    elif args.command == "dedup-url":
        payload = dedup_url(args)
    elif args.command == "mark-absorbed":
        payload = mark_absorbed(args)
    else:
        payload = {"ok": False, "reason": "unknown_command", "command": args.command}
    print(json.dumps(json_safe(payload), ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
