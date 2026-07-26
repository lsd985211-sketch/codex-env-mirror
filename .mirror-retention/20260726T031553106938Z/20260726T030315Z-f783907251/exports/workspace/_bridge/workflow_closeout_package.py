#!/usr/bin/env python3
"""Closeout package assembly for Codex workflow entry.

This module owns only pure dictionary assembly. It does not read files, run
tools, save records, clear work notes, or apply proposals.
"""

from __future__ import annotations

from typing import Any

from shared.json_cli import now_iso
from user_profile_candidates import profile_candidate_review_item
from workflow_review_queue import stable_review_key, unique_review_items


REVIEW_FIELD_LABELS = {
    "source_item_id": "id",
    "source_url": "source",
    "trust_tier": "trust",
    "freshness_class": "freshness",
    "proposed_destination_namespace": "target",
    "approval_action": "approval",
}


def has_proposal_type(proposals: list[dict[str, Any]], *types: str) -> bool:
    expected = {item for item in types if item}
    return any(str(item.get("type") or "") in expected for item in proposals)


def decision_flag(*items: list[str]) -> str:
    return "review" if any(item for group in items for item in group) else "not_needed"


def review_items(values: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for value in unique_review_items(values, kind="review_item", limit=limit):
        if not isinstance(value, dict):
            continue
        items.append({
            "source_item_id": value.get("source_item_id", ""),
            "title": value.get("title", ""),
            "summary": value.get("summary", ""),
            "source_url": value.get("source_url", ""),
            "trust_tier": value.get("trust_tier", ""),
            "freshness_class": value.get("freshness_class", ""),
            "path": value.get("path", ""),
            "proposed_destination_namespace": value.get("proposed_destination_namespace", ""),
            "approval_action": value.get("approval_action", "review_candidate"),
            "required_checks": value.get("required_checks", []),
            "attributes": value.get("attributes", {}),
            "covered_by": value.get("covered_by", ""),
            "candidate_id": value.get("candidate_id", ""),
            "source_checkpoint": value.get("source_checkpoint", ""),
            "stable_conclusion": value.get("stable_conclusion", ""),
            "target_namespace": value.get("target_namespace") or value.get("proposed_destination_namespace", ""),
            "affected_system": value.get("affected_system", ""),
        })
    return items


def review_card(item: dict[str, Any]) -> dict[str, Any]:
    """Build one compact human-review card from a pending review item."""
    attributes = dict(item.get("attributes") or {}) if isinstance(item.get("attributes"), dict) else {}
    attributes.update({
        REVIEW_FIELD_LABELS["trust_tier"]: item.get("trust_tier") or "",
        REVIEW_FIELD_LABELS["freshness_class"]: item.get("freshness_class") or "",
        REVIEW_FIELD_LABELS["proposed_destination_namespace"]: item.get("proposed_destination_namespace") or "",
    })
    return {
        "title": item.get("title") or item.get("source_item_id") or "untitled",
        "digest": item.get("summary") or item.get("detail") or "",
        "source": item.get("source_url") or "",
        "attributes": attributes,
        "approval_action": item.get("approval_action") or "review_candidate",
        "required_checks": item.get("required_checks") or [],
        "id": item.get("source_item_id") or "",
    }


def _compact_attribute_text(attributes: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in attributes.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        parts.append(f"{key}={value}")
    return "; ".join(parts) if parts else "none"


def _compact_required_checks(checks: list[Any]) -> str:
    values = [str(item).strip() for item in checks if str(item).strip()]
    return "; ".join(values) if values else "none"


def render_review_cards_markdown(cards: list[dict[str, Any]], *, hidden_count: int = 0) -> str:
    """Render review cards in the format final replies should show."""

    if not cards:
        return ""
    sections: list[str] = []
    for index, card in enumerate(cards, start=1):
        title = str(card.get("title") or card.get("id") or "untitled").strip()
        kind = str(card.get("kind") or "review").strip()
        digest = str(card.get("digest") or "").strip() or "No digest provided."
        source = str(card.get("source") or "").strip() or "local closeout"
        attributes = card.get("attributes") if isinstance(card.get("attributes"), dict) else {}
        action = str(card.get("approval_action") or "review_candidate").strip()
        checks = card.get("required_checks") if isinstance(card.get("required_checks"), list) else []
        sections.append(
            "\n".join(
                [
                    f"### Review Card {index}: {title}",
                    f"- Kind: {kind}",
                    f"- Digest: {digest}",
                    f"- Source: {source}",
                    f"- Attributes: {_compact_attribute_text(attributes)}",
                    f"- Approval action: {action}",
                    f"- Required checks: {_compact_required_checks(checks)}",
                ]
            )
        )
    if hidden_count > 0:
        sections.append(f"### More Review Cards\n- Hidden count: {hidden_count}")
    return "\n\n".join(sections)


def build_review_summary(package: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    pending = package.get("pending_disposition") if isinstance(package.get("pending_disposition"), dict) else {}
    pending_items = pending.get("items") if isinstance(pending.get("items"), list) else []
    cards: list[dict[str, Any]] = []
    seen_cards: set[str] = set()
    incomplete_count = 0

    def append_card(card: dict[str, Any]) -> None:
        key = stable_review_key(
            {
                "source_item_id": card.get("id", ""),
                "source_url": card.get("source", ""),
                "title": card.get("title", ""),
                "summary": card.get("digest", ""),
            },
            kind=str(card.get("kind") or ""),
        )
        if key in seen_cards:
            return
        seen_cards.add(key)
        cards.append(card)

    for pending_item in pending_items:
        if not isinstance(pending_item, dict):
            continue
        review_values = pending_item.get("review_items") if isinstance(pending_item.get("review_items"), list) else []
        if review_values:
            for review_value in unique_review_items(review_values, kind=str(pending_item.get("kind") or ""), limit=limit * 4):
                if isinstance(review_value, dict):
                    card = review_card(review_value)
                    card["kind"] = pending_item.get("kind", "")
                    append_card(card)
        elif pending_item.get("must_surface_to_user") or pending_item.get("approval_required_for_write"):
            incomplete_count += 1
            append_card({
                "kind": pending_item.get("kind", ""),
                "title": f"Incomplete review evidence: {pending_item.get('kind', 'pending_item')}",
                "digest": "The owner reported pending work but supplied no concrete review_items. Do not approve or apply the work until the producing owner returns the actual items.",
                "source": "",
                "attributes": {
                    "count": pending_item.get("count", 0),
                    "approval_required": bool(pending_item.get("approval_required_for_write")),
                    "evidence_complete": False,
                },
                "approval_action": "retrieve_concrete_owner_items_before_approval",
                "required_checks": [
                    "Run the producing owner doctor/repair-plan/detail command",
                    "Fix the owner contract if concrete review_items remain absent",
                ],
                "id": "",
            })
    visible_limit = max(1, int(limit))
    visible_cards = cards[:visible_limit]
    hidden_count = max(0, len(cards) - visible_limit)
    return {
        "schema": "codex_workflow_entry.review_summary.v1",
        "ok": True,
        "generated_at": now_iso(),
        "total_review_cards": len(cards),
        "shown_count": min(len(cards), visible_limit),
        "hidden_count": hidden_count,
        "incomplete_count": incomplete_count,
        "detail_complete": incomplete_count == 0,
        "cards": visible_cards,
        "markdown": render_review_cards_markdown(visible_cards, hidden_count=hidden_count),
        "display_contract": {
            "show_title": True,
            "show_digest": True,
            "show_source_and_attributes": True,
            "show_approval_action": True,
            "show_required_checks": True,
            "show_markdown_cards": True,
            "avoid_raw_json_dump": True,
        },
        "rule": "Every pending approval must carry concrete review_items. Final replies must show the markdown cards; a generic owner status is evidence-incomplete and cannot stand in for the actual items.",
    }


def compact_text(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def work_note_review_items(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, note in enumerate(notes[:20], start=1):
        note_id = str(note.get("id") or f"work-note-{index}")
        scope = str(note.get("scope") or "unspecified scope")
        items.append({
            "source_item_id": note_id,
            "title": f"Work note: {scope}",
            "summary": compact_text(note.get("text") or note.get("raw") or note.get("reason") or ""),
            "source_url": str(note.get("source") or "local work note"),
            "trust_tier": "current_thread_work_note",
            "freshness_class": str(note.get("created_at") or "current_closeout"),
            "proposed_destination_namespace": "workflow.work_note_disposition",
            "approval_action": f"after handling, record disposition: python _bridge\\memory_governance.py work-note-dispose --ids {note_id} --disposition <handled_read_only|proposal|deferred|discarded>",
            "required_checks": [
                "Do not inherit authorization for derived writes or external actions",
                "Record the exact disposition only after the approved action succeeds",
                "Deferred content stays stored but must not reenter review",
            ],
            "attributes": {
                "reason": str(note.get("reason") or ""),
                "status": str(note.get("status") or ""),
            },
        })
    return items


def proposal_review_items(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals[:20], start=1):
        proposal_type = str(proposal.get("type") or "general")
        title = str(proposal.get("title") or f"Proposal {index}")
        artifact_ref = str(proposal.get("artifact_ref") or "").strip()
        proposal_identity = artifact_ref or stable_review_key(
            {
                "title": title,
                "summary": proposal.get("detail") or title,
                "proposed_destination_namespace": f"workflow.proposal.{proposal_type}",
            },
            kind=proposal_type,
        )
        items.append({
            "source_item_id": f"proposal:{proposal_type}:{proposal_identity}",
            "title": title,
            "summary": compact_text(proposal.get("detail") or title),
            "source_url": "local closeout proposal",
            "trust_tier": "current_turn_proposal",
            "freshness_class": "current_closeout",
            "path": artifact_ref,
            "proposed_destination_namespace": f"workflow.proposal.{proposal_type}",
            "approval_action": "approve|revise|reject",
            "required_checks": [
                "Apply only the exact approved proposal scope",
                "Read artifact_ref before deciding" if artifact_ref else "Use the supplied proposal detail",
            ],
            "attributes": {
                "type": proposal_type,
                "status": proposal.get("status", "pending_user_review"),
                "artifact_ref": artifact_ref,
                "content_maturity": "draft" if proposal_type == "draft_review" else "",
                "workflow_status": "pending_review" if proposal_type == "draft_review" else "",
            },
        })
    return items


def build_pending_disposition(
    *,
    notes: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    profile_candidate_count: int,
    external_candidate_count: int,
    fallback_tools: list[str],
    negative_items: list[dict[str, Any]],
    unverified_items: list[dict[str, Any]],
    profile_review_items: list[dict[str, Any]] | None = None,
    external_review_items: list[dict[str, Any]] | None = None,
    self_update_signals: list[dict[str, Any]] | None = None,
    iteration_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    self_update_signals = self_update_signals or []
    iteration_candidates = iteration_candidates or []
    native_negative_without_fallback = bool(negative_items) and not bool(fallback_tools)
    owner_mcp_missing = any(
        str(item.get("key") or "") == "external_research_owner_mcp_missing"
        for item in unverified_items
    )
    if notes:
        items.append({
            "kind": "work_notes",
            "count": len(notes),
            "action": "read_raw_entries_then_handle_read_only_or_propose",
            "approval_required_for_write": True,
            "mark_after_disposition": True,
            "clear_after_disposition": False,
            "must_surface_to_user": True,
            "review_items": work_note_review_items(notes),
        })
    if proposals:
        items.append({
            "kind": "proposals",
            "count": len(proposals),
            "action": "surface_to_user_or_apply_only_if_exactly_approved",
            "approval_required_for_write": True,
            "must_surface_to_user": True,
            "review_items": proposal_review_items(proposals),
        })
    if profile_candidate_count:
        profile_reviews = review_items(profile_review_items or [])
        items.append({
            "kind": "user_profile_candidates",
            "count": len(profile_reviews) or profile_candidate_count,
            "action": "surface_candidate_facts; write_profile_only_after_explicit_approval",
            "approval_required_for_write": True,
            "must_surface_to_user": True,
            "review_items": profile_reviews,
        })
    if external_candidate_count:
        external_reviews = review_items(external_review_items or [])
        items.append({
            "kind": "external_knowledge_memory_candidates",
            "count": len(external_reviews) or external_candidate_count,
            "action": "review_auto_written_candidate_notes_then_approve_or_reject_absorb_plan",
            "approval_required_for_write": True,
            "must_surface_to_user": True,
            "review_items": external_reviews,
        })
    if iteration_candidates:
        iteration_reviews = review_items(iteration_candidates)
        items.append({
            "kind": "iteration_candidates",
            "count": len(iteration_reviews),
            "action": "approve_then_dispatch_exact_candidate_to_mapped_owner",
            "approval_required_for_write": True,
            "must_surface_to_user": True,
            "review_items": iteration_reviews,
            "lifecycle": ["pending", "approved", "applied", "validated", "resolved"],
            "rule": "capture is read-only; approval does not authorize the coordinator to bypass the target owner",
        })
    if native_negative_without_fallback or unverified_items:
        review_items_for_tools: list[dict[str, Any]] = []
        if native_negative_without_fallback:
            for index, item in enumerate(negative_items[:20], start=1):
                key = str(item.get("key") or item.get("code") or item.get("profile") or f"negative-{index}")
                review_items_for_tools.append(
                    {
                        "source_item_id": f"tool-negative:{key}:{index}",
                        "title": str(item.get("title") or f"Tool failure without completed fallback: {key}"),
                        "summary": compact_text(item.get("detail") or item.get("error") or item.get("message") or key),
                        "source_url": str(item.get("source") or item.get("path") or ""),
                        "trust_tier": "local_runtime_evidence",
                        "freshness_class": "current_turn",
                        "proposed_destination_namespace": "workflow.tool_routing",
                        "approval_action": "continue_from_the_failed_priority_stage",
                        "required_checks": [
                            "mcp_capability_routes lookup for the profile",
                            "Continue from the failed stage through the configured forward-only chain",
                            "Do not classify the task as blocked until the route chain is exhausted",
                        ],
                        "attributes": {
                            "profile": item.get("profile", ""),
                            "tool": item.get("tool", ""),
                            "status": item.get("status", ""),
                        },
                    }
                )
        if owner_mcp_missing:
            review_items_for_tools.append(
                {
                    "source_item_id": "external_research_owner_mcp_missing",
                    "title": "External research skipped owner MCP route",
                    "summary": "Generic web search was recorded without source-owning MCP evidence or a fallback reason. Rerun the owner MCP route or document why it was unavailable, insufficient, or not applicable.",
                    "source_url": "",
                    "trust_tier": "workflow_runtime_evidence",
                    "freshness_class": "current_turn",
                    "proposed_destination_namespace": "workflow.external_docs_research",
                    "approval_action": "rerun_owner_mcp_or_record_fallback_reason",
                    "required_checks": [
                        "Microsoft/Windows/Azure: Microsoft Docs MCP",
                        "libraries/SDKs/frameworks: Context7 MCP",
                        "repository facts: GitHub MCP",
                        "page/runtime evidence: browser, DevTools, or Playwright MCP",
                    ],
                }
            )
        for index, item in enumerate(unverified_items[:20], start=1):
            if not isinstance(item, dict) or str(item.get("key") or "") == "external_research_owner_mcp_missing":
                continue
            key = str(item.get("key") or item.get("code") or f"unverified-{index}")
            review_items_for_tools.append(
                {
                    "source_item_id": f"tool-unverified:{key}",
                    "title": str(item.get("title") or f"Unverified tool evidence: {key}"),
                    "summary": compact_text(item.get("detail") or item.get("reason") or item.get("message") or key),
                    "source_url": str(item.get("source") or item.get("path") or ""),
                    "trust_tier": "workflow_runtime_evidence",
                    "freshness_class": "current_turn",
                    "proposed_destination_namespace": "workflow.tool_routing",
                    "approval_action": str(item.get("next_action") or "verify_or_reclassify_before_closeout"),
                    "required_checks": ["Use the configured MCP priority chain and owner validator"],
                    "attributes": {"evidence_state": "unverified"},
                }
            )
        items.append({
            "kind": "tool_evidence",
            "count": len(review_items_for_tools),
            "action": "resolve_each_unverified_or_unrouted_tool_item",
            "approval_required_for_write": True,
            "must_surface_to_user": True,
            "review_items": review_items_for_tools,
        })
    if self_update_signals:
        review_items_for_self_update = []
        represented_queue_kinds = {
            kind
            for kind, present in (("work_notes", bool(notes)), ("proposals", bool(proposals)))
            if present
        }
        for signal in self_update_signals[:10]:
            if not isinstance(signal, dict):
                continue
            code = str(signal.get("code") or "self_update_signal")
            surface = str(signal.get("surface") or "unknown")
            severity = str(signal.get("severity") or "warn")
            nested = signal.get("review_items") if isinstance(signal.get("review_items"), list) else []
            if nested:
                for nested_item in nested:
                    if not isinstance(nested_item, dict):
                        continue
                    if str(nested_item.get("covered_by") or "") in represented_queue_kinds:
                        continue
                    review_items_for_self_update.append(nested_item)
                continue
            review_items_for_self_update.append(
                {
                    "source_item_id": f"self_update:{surface}:{code}",
                    "title": f"Incomplete self-update evidence: {surface}",
                    "summary": compact_text(signal.get("detail") or code),
                    "source_url": "",
                    "trust_tier": "local_owner_doctor",
                    "freshness_class": "closeout_current_run",
                    "proposed_destination_namespace": f"workflow.self_update.{surface}",
                    "approval_action": "retrieve_concrete_owner_items_before_approval",
                    "required_checks": [
                        "Run the owner doctor/repair-plan/detail command",
                        "Update the owner signal contract to return review_items",
                    ],
                    "attributes": {"severity": severity, "evidence_complete": False},
                }
            )
        if review_items_for_self_update:
            items.append({
                "kind": "self_update_governance",
                "count": len(review_items_for_self_update),
                "action": "review_concrete_owner_items_then_update_only_through_owner_surfaces",
                "approval_required_for_write": True,
                "must_surface_to_user": True,
                "review_items": review_items_for_self_update,
            })
    return {
        "schema": "codex_workflow_entry.pending_disposition.v1",
        "ok": True,
        "pending_count": len(items),
        "items": items,
        "rule": "This is the one closeout queue. Every pending approval must include concrete review_items; resolved evidence such as a successful fallback is not a pending approval. Handle read-only follow-up automatically, and keep writes behind exact approval.",
    }


def build_external_section(external_candidates: dict[str, Any], external_candidate_count: int) -> dict[str, Any]:
    external_reviews = review_items(external_candidates.get("would_write", []))
    return {
        "schema": external_candidates.get("schema"),
        "ok": bool(external_candidates.get("ok")),
        "exists": bool(external_candidates.get("exists")),
        "trigger": external_candidates.get("trigger", ""),
        "candidate_count": int(external_candidates.get("candidate_count") or 0),
        "selected_count": len(external_reviews) if external_reviews else external_candidate_count,
        "would_write": external_reviews,
        "candidate_note_apply_command": external_candidates.get(
            "candidate_note_apply_command",
            "python _bridge\\external_knowledge.py memory-candidates --apply",
        ),
        "absorb_plan_command": external_candidates.get(
            "absorb_plan_command",
            "python _bridge\\memory_governance.py absorb-plan --limit 20",
        ),
        "writes_long_term_memory": False,
        "requires_user_review": bool(external_candidates.get("requires_user_review")),
        "rule": "external knowledge closeout may auto-write candidate notes as a draft layer; long-term absorption still requires explicit user approval",
    }


def build_persistence_decisions(
    *,
    notes: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    profile_candidate_count: int,
    external_candidate_count: int,
    used_slash: list[str],
    fallback_tools: list[str],
    negative_items: list[dict[str, Any]],
    finalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finalization = finalization if isinstance(finalization, dict) else {}
    signals = finalization.get("signals", {}) if isinstance(finalization.get("signals"), dict) else {}
    baseline_signal = bool(signals.get("config_changed"))
    checkpoint_signal = bool(signals.get("major_change"))
    return {
        "memory_absorb_needed": "review" if notes or has_proposal_type(proposals, "memory", "pmb") else "not_needed",
        "user_profile_update_needed": "review" if profile_candidate_count else "not_needed",
        "skill_revision_needed": "review" if has_proposal_type(proposals, "skill") else "not_needed",
        "slash_template_needed": decision_flag([item for item in used_slash if item.startswith("missing:")]),
        "tool_matrix_update_needed": decision_flag(fallback_tools, [item.get("value", "") for item in negative_items]),
        "tool_route_completion_needed": "review" if negative_items and not fallback_tools else "not_needed",
        "baseline_update_needed": "auto_handled" if baseline_signal else decision_flag([p.get("title", "") for p in proposals if p.get("type") == "baseline"]),
        "project_checkpoint_needed": "auto_handled" if checkpoint_signal else "not_needed",
        "external_knowledge_capture_needed": "review" if external_candidate_count else "not_needed",
        "external_knowledge_absorb_needed": "review" if external_candidate_count else "not_needed",
        "rule": "decisions are routing signals only; writes require separate approval unless already explicitly granted for that exact action",
    }


def build_closeout_package(ctx: dict[str, Any]) -> dict[str, Any]:
    finalization = ctx.get("finalization", {}) if isinstance(ctx.get("finalization"), dict) else {}
    finalization_ok = bool(finalization.get("ok", True))
    profile_count = int(ctx["profile_candidates"].get("candidate_count") or 0)
    profile_review_items = review_items(
        [
            profile_candidate_review_item(candidate)
            for candidate in (ctx["profile_candidates"].get("candidates", []) or [])
            if isinstance(candidate, dict)
        ]
    )
    external_review_items = review_items(ctx["external_candidates"].get("would_write", []))
    external_count = len(external_review_items) or int(ctx["external_candidates"].get("selected_count") or 0)
    pending = build_pending_disposition(
        notes=ctx["notes"],
        proposals=ctx["proposals"],
        profile_candidate_count=profile_count,
        external_candidate_count=external_count,
        fallback_tools=ctx["fallback_tools"],
        negative_items=ctx["negative_items"],
        unverified_items=ctx["unverified_items"],
        profile_review_items=profile_review_items,
        external_review_items=external_review_items,
        self_update_signals=ctx.get("self_update_governance", {}).get("signals", []),
        iteration_candidates=ctx.get("iteration_capture", {}).get("candidates", []),
    )
    package = {
        "schema": "codex_workflow_entry.closeout.v2",
        "ok": finalization_ok,
        "generated_at": now_iso(),
        "machine_first": True,
        "record_path": ctx["record_path"],
        "task_kind": ctx["task_kind"],
        "status": {
            "outcome": ctx["outcome"],
            "main_task_complete": ctx["outcome"] in {"ok", "complete", "partial"} and finalization_ok,
            "derived_work_write_authorization_inherited": False,
        },
        "used": ctx["used"],
        "skill_usage": ctx["skill_usage"],
        "tool_evidence": ctx["tool_evidence"],
        "work_notes": ctx["work_notes"],
        "memory_routing": ctx["memory_routing"],
        "user_profile_candidates": {
            "schema": ctx["profile_candidates"].get("schema"),
            "ok": bool(ctx["profile_candidates"].get("ok")),
            "candidate_count": profile_count,
            "candidates": ctx["profile_candidates"].get("candidates", []),
            "review_items": profile_review_items,
            "writes_profile": False,
            "rule": "closeout may surface inferred profile candidates, but user_profile writes require separate explicit approval",
        },
        "external_knowledge_candidates": build_external_section(ctx["external_candidates"], external_count),
        "self_update_governance": ctx.get("self_update_governance", {}),
        "iteration_capture": ctx.get("iteration_capture", {}),
        "validation": ctx["validation"],
        "persistence_decisions": build_persistence_decisions(
            notes=ctx["notes"],
            proposals=ctx["proposals"],
            profile_candidate_count=profile_count,
            external_candidate_count=external_count,
            used_slash=ctx["used"]["slash_templates"],
            fallback_tools=ctx["fallback_tools"],
            negative_items=ctx["negative_items"],
            finalization=ctx.get("finalization", {}),
        ),
        "proposals": ctx["proposals"],
        "finalization": ctx.get("finalization", {}),
        "persistence_gates": {
            "memory_write_requires_approval": True,
            "skill_write_requires_approval": True,
            "baseline_write_requires_approval": False,
            "baseline_write_rule": "Explicit --config-changed plus successful outcome authorizes bounded startup baseline adoption through codex_baseline_update.",
            "project_checkpoint_write_requires_approval": False,
            "project_checkpoint_write_rule": "Explicit --major-change plus successful outcome authorizes bounded project checkpoint creation through project_checkpoint_finalize.",
            "external_action_requires_approval": True,
            "work_note_derived_write_requires_separate_approval": True,
        },
        "pending_disposition": pending,
        "final_reply_requirements": [
            "read_this_package_before_final_reply",
            "read_pending_disposition_as_the_single_closeout_queue",
            "show_final_reply_must_show_cards_when_present",
            "render_final_reply_must_show_markdown_when_review_cards_exist",
            "if_external_knowledge_candidate_notes_exist_show_review_cards_with_title_digest_source_attributes_and_action",
            "surface_pending_proposals_or_state_no_persistence_needed",
            "record_each_work_note_disposition_after_handling_so_it_does_not_reenter_review",
        ],
    }
    package["final_reply_must_show"] = build_review_summary(package, limit=20)
    return package
