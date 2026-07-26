#!/usr/bin/env python3
"""Read-only resource acquisition strategy review.

This module turns resource JSONL observations into policy proposals. It never
fetches resources, installs tools, mutates policies, or writes promotion files.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_SAFETY_BOUNDARIES = (
    "read_only_review",
    "proposal_only",
    "no_network_fetch",
    "no_tool_install",
    "no_package_manager",
    "no_git_clone",
    "no_login_or_session_state",
    "no_policy_mutation",
    "no_cache_or_filesystem_write",
)


@dataclass(frozen=True)
class ResourceStrategyProposal:
    bucket: str
    priority: str
    summary: str
    proposal: str
    validation: str
    risk_flags: tuple[str, ...] = ()
    needs_user_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _string(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or default


def _metadata(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def inferred_resource_fields(entry: dict[str, Any]) -> dict[str, str]:
    metadata = _metadata(entry)
    stage = _string(metadata.get("stage") or metadata.get("cli_command"))
    intent = _string(entry.get("intent") or metadata.get("intent") or metadata.get("declared_intent"), "")
    kind = _string(entry.get("resource_kind") or metadata.get("resource_kind"), "")
    if not intent and metadata.get("cli_command") == "fetch-file":
        intent = "legacy_cli_fetch_file"
    elif not intent and metadata.get("cli_command") == "fetch-url":
        intent = "legacy_cli_fetch_url"
    if not kind and metadata.get("cli_command") == "fetch-file":
        kind = "local_file"
    elif not kind and metadata.get("cli_command") in {"fetch-url", "probe-url", "preview-url"}:
        kind = "url"
    return {
        "intent": intent or "unknown",
        "kind": kind or "unknown",
        "stage": stage,
        "decision": _string(entry.get("decision"), "none"),
    }


def resource_strategy_bucket(entry: dict[str, Any]) -> str:
    fields = inferred_resource_fields(entry)
    intent = fields["intent"]
    kind = fields["kind"]
    stage = fields["stage"]
    decision = fields["decision"]
    return f"{intent}|{kind}|{stage}|{decision}"


def read_resource_log(log_path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[-limit:] if limit > 0 else lines
    entries: list[dict[str, Any]] = []
    for line in selected:
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def summarize_resource_observations(entries: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, Counter[str]] = defaultdict(Counter)
    errors: Counter[str] = Counter()
    risk_flags: Counter[str] = Counter()
    intents: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    writes = 0

    for entry in entries:
        fields = inferred_resource_fields(entry)
        bucket = resource_strategy_bucket(entry)
        ok = bool(entry.get("ok"))
        decision = fields["decision"]
        intent = fields["intent"]
        stage = fields["stage"]
        error = _string(entry.get("error"), "")

        buckets[bucket]["total"] += 1
        buckets[bucket]["ok" if ok else "failed"] += 1
        intents[intent] += 1
        stages[stage] += 1
        decisions[decision] += 1
        if error:
            errors[error] += 1
        for flag in entry.get("risk_flags") or ():
            risk_flags[str(flag)] += 1
        if entry.get("stored_path") or entry.get("local_path"):
            writes += 1

    return {
        "entry_count": len(entries),
        "bucket_count": len(buckets),
        "write_observation_count": writes,
        "intents": dict(intents),
        "stages": dict(stages),
        "decisions": dict(decisions),
        "errors": dict(errors.most_common(12)),
        "risk_flags": dict(risk_flags.most_common(12)),
        "buckets": {key: dict(value) for key, value in sorted(buckets.items())},
    }


def _proposal_for_bucket(bucket: str, stats: dict[str, int]) -> ResourceStrategyProposal | None:
    intent, kind, stage, decision = (bucket.split("|") + ["unknown"] * 4)[:4]
    total = int(stats.get("total", 0))
    failed = int(stats.get("failed", 0))
    ok = int(stats.get("ok", 0))

    if intent in {"inline_url_candidate", "unknown"}:
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P0",
            summary=f"{intent} observations should remain non-materializing by default.",
            proposal="Keep this category at discover/probe/preview or deferred; do not auto-download arbitrary message text URLs.",
            validation="Run resource_fetcher_tests.py and a strategy-review fixture that confirms proposal-only output.",
            risk_flags=("implicit_resource", "permission_boundary"),
        )

    if intent.startswith("legacy_cli_"):
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P2",
            summary="Legacy CLI resource log entries are missing explicit intent metadata.",
            proposal="Prefer acquire --intent or staged commands for new resource operations so future strategy review can distinguish attachments, docs, dependencies, and user URLs.",
            validation="Run strategy-review and confirm legacy buckets are reported as legacy_cli_* rather than generic unknown.",
            risk_flags=("legacy_metadata",),
            needs_user_confirmation=False,
        )

    if intent in {"package_dependency", "external_dependency"}:
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P0",
            summary=f"{intent} observations require explicit approval before side-effecting acquisition.",
            proposal="Prefer read-only metadata, docs lookup, or probe/preview. Never run package managers, git clone, installers, or persistent config changes from this strategy layer.",
            validation="Confirm no command produced by strategy-review contains package-manager, git clone, installer, or write action.",
            risk_flags=("external_dependency", "side_effect_boundary"),
        )

    if intent == "documentation_lookup":
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P1",
            summary="Documentation lookups are good candidates for staged read-only routing.",
            proposal="Prefer purpose-built docs tools first, then URL probe/preview, and materialize only when the user explicitly asks to save a resource.",
            validation="Run probe-url/preview-url smoke checks and keep documentation_lookup materialization deferred unless explicitly approved.",
            risk_flags=("freshness_sensitive",),
        )

    if intent == "explicit_user_url" and stage in {"probe", "preview", "acquire"}:
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P1",
            summary="Explicit user URLs can benefit from staged probe/preview before materialization.",
            proposal="Keep using probe before preview/materialize when size, redirect, content type, or source trust is unclear.",
            validation="Use a no-log probe/preview smoke test and confirm no cache file is written during probe/preview.",
            risk_flags=("network_resource",),
            needs_user_confirmation=False,
        )

    if intent == "explicit_attachment" and ok > 0:
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P2",
            summary="Explicit attachments are the only default materialization path.",
            proposal="Keep sha256, size cap, cache metadata, and analysis preview as required attachment metadata before Codex delivery.",
            validation="Run resource-layer-smoke-check and verify stored attachment metadata fields are present.",
            risk_flags=("attachment_path",),
            needs_user_confirmation=False,
        )

    if failed and total and failed >= ok:
        return ResourceStrategyProposal(
            bucket=bucket,
            priority="P2",
            summary="This bucket fails often enough to justify a narrower preflight.",
            proposal="Prefer discover/probe first and surface the failure reason in prompt metadata instead of retrying through materialization.",
            validation="Add or keep a regression fixture for the dominant failure before promoting a stricter policy.",
            risk_flags=("failure_pattern",),
        )

    return None


def build_resource_strategy_review(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_resource_observations(entries)
    proposals: list[ResourceStrategyProposal] = []
    for bucket, stats in summary["buckets"].items():
        proposal = _proposal_for_bucket(bucket, stats)
        if proposal:
            proposals.append(proposal)

    proposals.sort(key=lambda item: (item.priority, item.bucket))
    return {
        "ok": True,
        "mode": "read_only",
        "writes_files": False,
        "executes_tools": False,
        "safety_boundaries": DEFAULT_SAFETY_BOUNDARIES,
        "summary": summary,
        "proposal_count": len(proposals),
        "proposals": [proposal.to_dict() for proposal in proposals],
        "recommended_next_actions": [
            "Review proposals before changing policies, skills, or tool routing.",
            "Promote only rules with explicit user approval, backup, and regression tests.",
            "Keep resource strategy evolution separate from materialization code paths.",
        ],
    }


def format_resource_strategy_review(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "Resource strategy review",
        f"Mode: {report.get('mode', 'unknown')} writes_files={report.get('writes_files')} executes_tools={report.get('executes_tools')}",
        f"Entries: {summary.get('entry_count', 0)} buckets={summary.get('bucket_count', 0)} proposals={report.get('proposal_count', 0)}",
        "",
        "Top intents:",
    ]
    intents = summary.get("intents") or {}
    if intents:
        for intent, count in sorted(intents.items(), key=lambda item: (-item[1], item[0]))[:8]:
            lines.append(f"- {intent}: {count}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Proposals:")
    proposals = report.get("proposals") or []
    if not proposals:
        lines.append("- none")
    for index, proposal in enumerate(proposals, start=1):
        lines.append(f"{index}. [{proposal['priority']}] {proposal['summary']}")
        lines.append(f"   bucket={proposal['bucket']}")
        lines.append(f"   proposal={proposal['proposal']}")
        lines.append(f"   validation={proposal['validation']}")
    return "\n".join(lines)
