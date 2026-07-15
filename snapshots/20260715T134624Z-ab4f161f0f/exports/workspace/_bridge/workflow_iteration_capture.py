#!/usr/bin/env python3
"""Capture verified iteration candidates from structured closeout evidence.

Ownership: read-only semantic extraction, privacy screening, and deterministic
candidate identity for the workflow iteration layer.
Non-goals: approving candidates, writing memory/skills/rules, executing owner
commands, or scanning arbitrary workspace files.
State behavior: pure/read-only; callers may persist returned candidates only in
the governed workflow review queue.
Caller context: ``codex_workflow_entry.closeout`` and focused synthetic tests.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from memory_note_analysis import highest_severity, sensitive_hits


MAX_TEXT_LENGTH = 4000
EXACT_HEADINGS = {
    "summary": "summary",
    "changed files": "changed_files",
    "evidence": "evidence",
    "verification": "verification",
    "backups": "backups",
    "stable conclusions": "stable_conclusions",
    "followups": "followups",
}
MEMORY_NAMESPACE = "memory.project_conclusions"
OWNER_BY_PREFIX = {
    "memory.": "memory_governance",
    "project_checkpoint.": "project_checkpoint_finalize",
    "skills.": "skill_owner",
    "rules.": "rule_governance",
    "maintenance.": "maintenance_owner",
    "system_membership.": "system_membership",
}


def _clean_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit].strip()


def _clean_list(values: Any, *, limit: int = 100) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values] if values else []
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _unwrap_checkpoint(value: dict[str, Any]) -> dict[str, Any]:
    current = value
    for key in ("result", "checkpoint"):
        nested = current.get(key)
        if isinstance(nested, dict):
            current = nested
    return current


def _markdown_sections(markdown: str) -> dict[str, Any]:
    sections: dict[str, list[str]] = {value: [] for value in EXACT_HEADINGS.values()}
    active = ""
    for raw_line in str(markdown or "").splitlines():
        heading = re.fullmatch(r"\s*##\s+(.+?)\s*", raw_line)
        if heading:
            active = EXACT_HEADINGS.get(heading.group(1).strip().lower(), "")
            continue
        if not active:
            continue
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        line = _clean_text(line)
        if line:
            sections[active].append(line)
    return {
        "summary": " ".join(sections["summary"]),
        **{key: values for key, values in sections.items() if key != "summary"},
    }


def parse_checkpoint(checkpoint: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    """Parse one structured checkpoint, with exact-heading Markdown fallback."""

    if checkpoint is None:
        source: dict[str, Any] = {}
    elif isinstance(checkpoint, dict):
        source = _unwrap_checkpoint(checkpoint)
    else:
        raw = str(checkpoint)
        path = Path(raw)
        if "\n" not in raw and path.is_file():
            raw = path.read_text(encoding="utf-8", errors="replace")
        source = _markdown_sections(raw)
    return {
        "checkpoint_id": _clean_text(source.get("checkpoint_id")),
        "project_id": _clean_text(source.get("project_id")),
        "path": _clean_text(source.get("path")),
        "summary": _clean_text(source.get("summary")),
        "changed_files": _clean_list(source.get("changed_files")),
        "evidence": _clean_list(source.get("evidence")),
        "verification": _clean_list(source.get("verification")),
        "backups": _clean_list(source.get("backups")),
        "stable_conclusions": _clean_list(source.get("stable_conclusions")),
        "followups": _clean_list(source.get("followups")),
    }


def stable_candidate_id(
    *,
    text: str,
    source_checkpoint: str,
    stable_conclusion: str,
    target_namespace: str,
    affected_system: str,
) -> str:
    identity = {
        "text": _clean_text(text),
        "source_checkpoint": _clean_text(source_checkpoint),
        "stable_conclusion": _clean_text(stable_conclusion),
        "target_namespace": _clean_text(target_namespace),
        "affected_system": _clean_text(affected_system),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"iteration:{digest}"


def owner_for_namespace(namespace: str) -> str:
    clean = _clean_text(namespace)
    return next((owner for prefix, owner in OWNER_BY_PREFIX.items() if clean.startswith(prefix)), "")


def structured_capture_reasons(
    *,
    outcome: str,
    config_changed: bool = False,
    major_change: bool = False,
    corrections: Iterable[Any] = (),
    verified_root_causes: Iterable[Any] = (),
    regression_tests: Iterable[Any] = (),
    prevention_guards: Iterable[Any] = (),
    repeated_manual_steps: Iterable[Any] = (),
    checkpoint: dict[str, Any] | str | Path | None = None,
) -> list[str]:
    parsed = parse_checkpoint(checkpoint)
    reasons: list[str] = []
    for enabled, reason in (
        (config_changed, "config_changed"),
        (major_change, "major_change"),
        (_clean_text(outcome).lower() in {"failed", "blocked", "partial"}, f"outcome:{_clean_text(outcome).lower()}"),
        (bool(list(corrections)), "user_correction"),
        (bool(list(verified_root_causes)), "verified_root_cause"),
        (bool(list(regression_tests)), "regression_test"),
        (bool(list(prevention_guards)), "prevention_guard"),
        (bool(list(repeated_manual_steps)), "repeated_manual_step"),
        (bool(parsed["stable_conclusions"]), "checkpoint_stable_conclusion"),
    ):
        if enabled and reason not in reasons:
            reasons.append(reason)
    return reasons


def _signal_items(values: Iterable[Any], *, signal_kind: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for value in values:
        if isinstance(value, dict):
            text = _clean_text(value.get("text") or value.get("summary") or value.get("detail"))
            namespace = _clean_text(value.get("target_namespace")) or MEMORY_NAMESPACE
            affected = _clean_text(value.get("affected_system"))
        else:
            text = _clean_text(value)
            namespace = MEMORY_NAMESPACE
            affected = ""
        if text:
            items.append({"text": text, "target_namespace": namespace, "affected_system": affected, "signal_kind": signal_kind})
    return items


def _candidate(
    *,
    text: str,
    source_checkpoint: str,
    target_namespace: str,
    affected_system: str,
    signal_kind: str,
    checkpoint_path: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate_id = stable_candidate_id(
        text=text,
        source_checkpoint=source_checkpoint,
        stable_conclusion=text,
        target_namespace=target_namespace,
        affected_system=affected_system,
    )
    privacy_hits = sensitive_hits(text)
    severity = highest_severity(privacy_hits)
    if severity in {"high", "medium"}:
        return None, {
            "candidate_id": candidate_id,
            "reason": "sensitive_content_blocked_before_queue",
            "privacy_severity": severity,
            "privacy_hit_codes": [item.get("code") for item in privacy_hits],
        }
    owner = owner_for_namespace(target_namespace)
    identity = {
        "text": text,
        "source_checkpoint": source_checkpoint,
        "stable_conclusion": text,
        "target_namespace": target_namespace,
        "affected_system": affected_system,
    }
    return {
        "candidate_id": candidate_id,
        "source_item_id": candidate_id,
        "title": f"Verified iteration candidate: {signal_kind.replace('_', ' ')}",
        "summary": text,
        "source_url": checkpoint_path,
        "source_checkpoint": source_checkpoint,
        "stable_conclusion": text,
        "target_namespace": target_namespace,
        "affected_system": affected_system,
        "proposed_destination_namespace": target_namespace,
        "trust_tier": "local_verified_closeout",
        "freshness_class": "current_closeout",
        "approval_action": "approve_then_dispatch_to_exact_owner",
        "required_checks": [
            "Verify the conclusion is durable and current",
            "Review privacy and secret classification",
            "Apply only through the mapped owner with explicit confirmation",
            "Validate owner readback/recall before resolving",
        ],
        "attributes": {
            "signal_kind": signal_kind,
            "owner": owner or "unmapped",
            "privacy_severity": severity,
            "identity": identity,
            "write_authorization_inherited": False,
        },
    }, None


def capture_iteration_candidates(
    *,
    outcome: str,
    config_changed: bool = False,
    major_change: bool = False,
    corrections: Iterable[Any] = (),
    verified_root_causes: Iterable[Any] = (),
    regression_tests: Iterable[Any] = (),
    prevention_guards: Iterable[Any] = (),
    repeated_manual_steps: Iterable[Any] = (),
    checkpoint: dict[str, Any] | str | Path | None = None,
    affected_system: str = "",
) -> dict[str, Any]:
    """Return bounded, privacy-screened candidates without writing state."""

    parsed = parse_checkpoint(checkpoint)
    effective_system = _clean_text(affected_system or parsed["project_id"] or "unknown")[:120]
    source_checkpoint = parsed["checkpoint_id"] or parsed["path"] or "closeout-structured-facts"
    signals: list[dict[str, str]] = []
    for conclusion in parsed["stable_conclusions"]:
        signals.append({
            "text": conclusion,
            "target_namespace": MEMORY_NAMESPACE,
            "affected_system": effective_system,
            "signal_kind": "checkpoint_stable_conclusion",
        })
    for values, kind in (
        (corrections, "user_correction"),
        (verified_root_causes, "verified_root_cause"),
        (regression_tests, "regression_test"),
        (prevention_guards, "prevention_guard"),
        (repeated_manual_steps, "repeated_manual_step"),
    ):
        signals.extend(_signal_items(values, signal_kind=kind))

    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in signals[:100]:
        target_namespace = signal["target_namespace"] or MEMORY_NAMESPACE
        item, rejected = _candidate(
            text=signal["text"],
            source_checkpoint=source_checkpoint,
            target_namespace=target_namespace,
            affected_system=signal["affected_system"] or effective_system,
            signal_kind=signal["signal_kind"],
            checkpoint_path=parsed["path"],
        )
        if rejected:
            blocked.append(rejected)
        elif item and item["candidate_id"] not in seen:
            seen.add(item["candidate_id"])
            candidates.append(item)
    reasons = structured_capture_reasons(
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        corrections=corrections,
        verified_root_causes=verified_root_causes,
        regression_tests=regression_tests,
        prevention_guards=prevention_guards,
        repeated_manual_steps=repeated_manual_steps,
        checkpoint=checkpoint,
    )
    return {
        "schema": "workflow_iteration_capture.v1",
        "ok": True,
        "triggered": bool(reasons),
        "trigger_reasons": reasons,
        "candidate_count": len(candidates),
        "blocked_count": len(blocked),
        "candidates": candidates,
        "blocked_candidates": blocked,
        "checkpoint": parsed,
        "writes_targets": False,
        "write_authorization_inherited": False,
    }


def verify_candidate_identity(item: dict[str, Any]) -> bool:
    candidate_id = _clean_text(item.get("candidate_id") or item.get("source_item_id"))
    expected = stable_candidate_id(
        text=_clean_text(item.get("summary")),
        source_checkpoint=_clean_text(item.get("source_checkpoint")),
        stable_conclusion=_clean_text(item.get("stable_conclusion") or item.get("summary")),
        target_namespace=_clean_text(item.get("target_namespace") or item.get("proposed_destination_namespace")),
        affected_system=_clean_text(item.get("affected_system")),
    )
    return bool(candidate_id) and candidate_id == expected
