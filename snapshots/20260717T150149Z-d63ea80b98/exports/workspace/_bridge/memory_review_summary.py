#!/usr/bin/env python3
"""Dry-run review summary builder for memory governance.

Owns: aggregating memory dry-run plans into a user-facing approval checklist
and rendering the checklist text.
Non-goals: applying approvals, writing memory/PMB/profile state, archiving
notes, or deciding long-term policy.
State behavior: consumes already-built plan payloads through caller-provided
read-only functions; never writes state.
Normal callers: `memory_governance.py review-summary` and closeout checks.
"""

from __future__ import annotations

from typing import Any, Callable

from _bridge.shared.json_cli import now_iso


JsonDict = dict[str, Any]
PlanBuilder = Callable[[int], JsonDict]


def first_items(items: Any, limit: int = 5) -> list[Any]:
    if not isinstance(items, list):
        return []
    return items[: max(0, int(limit))]


def format_bullets(items: list[Any], *, empty: str = "无") -> list[str]:
    bullets: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            bullets.append(text)
    return bullets or [empty]


def build_review_text(approval_items: list[JsonDict], *, generated_at: str) -> str:
    lines: list[str] = [
        "待审批记忆/画像吸收计划",
        "",
        f"生成时间：{generated_at}",
        "执行原则：当前仅为审批清单，不写入记忆、不修改画像、不归档 note。",
        "",
    ]
    if not approval_items:
        lines.append("当前没有可提交审批的记忆吸收项。")
        return "\n".join(lines)
    for index, item in enumerate(approval_items, start=1):
        lines.extend(
            [
                f"{index}. {item.get('title') or item.get('id')}",
                f"   来源：{'; '.join(format_bullets(first_items(item.get('sources'), 6)))}",
                f"   目标：{item.get('destination') or '待定'}",
                "   拟保留：",
            ]
        )
        for point in format_bullets(first_items(item.get("keep"), 5), empty="需人工从来源中提炼稳定事实"):
            lines.append(f"   - {point}")
        lines.append("   拟排除：")
        for point in format_bullets(first_items(item.get("exclude"), 5)):
            lines.append(f"   - {point}")
        lines.extend(
            [
                f"   风险：敏感={item.get('sensitive_severity') or '无'}；时效/当前态={item.get('drift_severity') or '无'}",
                f"   影响：{item.get('future_behavior_impact') or '吸收后作为后续工作召回和行动指导依据。'}",
                f"   执行方式：{item.get('execution')}",
                f"   验证：{'; '.join(format_bullets(first_items(item.get('validation'), 4)))}",
                f"   需要批准：{item.get('approval_request')}",
                "",
            ]
        )
    lines.append("可回复：批准全部 / 批准第 N 项 / 拒绝 / 修改后再提交。")
    return "\n".join(lines)


def build_review_cards(approval_items: list[JsonDict]) -> list[JsonDict]:
    """Return compact review-card records for user-facing rendering."""
    cards: list[JsonDict] = []
    for index, item in enumerate(approval_items, start=1):
        keep = format_bullets(first_items(item.get("keep"), 3), empty="需人工从来源中提炼稳定事实")
        exclude = format_bullets(first_items(item.get("exclude"), 3))
        cards.append(
            {
                "index": index,
                "id": str(item.get("id") or ""),
                "kind": str(item.get("kind") or ""),
                "title": str(item.get("title") or item.get("id") or ""),
                "sources": first_items(item.get("sources"), 6),
                "destination": str(item.get("destination") or "待定"),
                "keep": keep,
                "exclude": exclude,
                "risk": {
                    "sensitive": str(item.get("sensitive_severity") or "none"),
                    "drift": str(item.get("drift_severity") or "none"),
                },
                "impact": str(item.get("future_behavior_impact") or "吸收后作为后续工作召回和行动指导依据。"),
                "approval_action": str(item.get("approval_request") or "approve_modify_or_reject"),
                "validation": first_items(item.get("validation"), 4),
            }
        )
    return cards


def build_review_summary(
    *,
    limit: int = 20,
    consolidation_plan: PlanBuilder,
    absorb_plan: PlanBuilder,
    pmb_organize_plan: PlanBuilder,
    profile_plan: PlanBuilder | None = None,
) -> JsonDict:
    """Build a user-facing approval checklist from memory dry-run plans."""
    safe_limit = max(1, int(limit))
    consolidation = consolidation_plan(safe_limit)
    absorb = absorb_plan(min(safe_limit, 20))
    pmb = pmb_organize_plan(max(200, safe_limit * 20))
    profile = profile_plan(safe_limit) if profile_plan else {"schema": "", "candidates": []}
    approval_items: list[JsonDict] = []
    consolidated_sources: set[str] = set()
    single_note_sources: set[str] = set()

    for candidate in first_items(profile.get("candidates"), limit=safe_limit):
        if not isinstance(candidate, dict):
            continue
        fact = candidate.get("proposed_fact") if isinstance(candidate.get("proposed_fact"), dict) else {}
        approval_items.append(
            {
                "id": candidate.get("id"),
                "kind": "user_profile_candidate",
                "title": f"画像候选：{fact.get('category') or 'user_profile'}",
                "sources": [candidate.get("source") or ""],
                "destination": "user_profile",
                "keep": [
                    str(fact.get("value") or ""),
                    f"旧画像处理：{candidate.get('old_profile_action') or 'new'}"
                    + (f" -> {candidate.get('related_existing_fact_id')}" if candidate.get("related_existing_fact_id") else ""),
                ],
                "exclude": [
                    "不写入未批准候选",
                    "不吸收 AGENTS/工作区准则/技能里的规则型内容",
                    "不吸收一次性任务状态、临时授权或故障日志",
                ],
                "sensitive_severity": candidate.get("sensitive_severity") or "",
                "drift_severity": "",
                "future_behavior_impact": "批准后写入用户画像，作为后续工作风格、目标或取舍判断的指导依据。",
                "execution": "批准后先备份 user_profile.json，再按新增/合并/更新/移除处理旧画像，最后运行 memory_governance validate。",
                "validation": [
                    "python _bridge\\memory_governance.py validate",
                    "python _bridge\\memory_governance.py snapshot",
                ],
                "approval_request": candidate.get("approval_request") or "批准、修改或拒绝该画像候选。",
            }
        )

    for candidate in first_items(absorb.get("candidates"), limit=safe_limit):
        if not isinstance(candidate, dict):
            continue
        note = candidate.get("note") if isinstance(candidate.get("note"), dict) else {}
        note_name = str(note.get("name") or note.get("path") or "").strip()
        dest = candidate.get("recommended_destination") if isinstance(candidate.get("recommended_destination"), dict) else {}
        single_note_sources.add(note_name)
        approval_items.append(
            {
                "id": f"absorb:{note_name or candidate.get('title')}",
                "kind": "single_note_absorption",
                "title": f"单条 note：{candidate.get('title')}",
                "sources": [note_name],
                "destination": dest.get("destination", "workspace.mcsmanager.operational"),
                "keep": candidate.get("proposed_stable_points") or [],
                "exclude": candidate.get("excluded_by_default") or [],
                "sensitive_severity": candidate.get("sensitive_severity") or "",
                "drift_severity": "",
                "future_behavior_impact": "把单条候选 note 转为可召回的稳定事实或规则，避免只留在会话上下文。",
                "execution": "批准后备份目标，按推荐 namespace 写入或转为更窄的 PMB/画像/基线提案。",
                "validation": [
                    "python _bridge\\memory_governance.py validate",
                    "目标 namespace 回读",
                ],
                "approval_request": "批准该 note 吸收、改目标、或要求继续保留为候选。",
            }
        )

    for group in first_items(consolidation.get("groups"), limit=safe_limit):
        if not isinstance(group, dict):
            continue
        sources = [str(source) for source in (group.get("source_notes") or []) if str(source or "").strip()]
        consolidated_sources.update(sources)
        if sources and set(sources).issubset(single_note_sources):
            continue
        approval_items.append(
            {
                "id": f"consolidation:{group.get('theme_id')}",
                "kind": "note_consolidation",
                "title": f"合并主题：{group.get('theme_id')}",
                "sources": sources,
                "destination": group.get("destination"),
                "keep": group.get("stable_points") or [],
                "exclude": [
                    "原始日志和一次性事故细节",
                    "未验证的当前状态",
                    "敏感内容和凭据形态文本",
                    "重复 note 的全文堆叠",
                ],
                "sensitive_severity": group.get("sensitive_severity") or "",
                "drift_severity": group.get("drift_severity") or "",
                "future_behavior_impact": "把同一主题的零散 note 压缩成稳定规则，后续通过 PMB/工作区记忆召回。",
                "execution": "批准后先备份目标记忆/索引，再写入摘要或 PMB 事实，最后标记来源 note 的吸收状态。",
                "validation": [
                    "python _bridge\\memory_governance.py validate",
                    "python _bridge\\memory_governance.py metrics",
                    "PMB recall 或本地索引回读 promoted theme",
                ],
                "approval_request": "批准该主题吸收、要求删改保留点、或拒绝。",
            }
        )

    for action in first_items(pmb.get("actions"), limit=5):
        if not isinstance(action, dict):
            continue
        approval_items.append(
            {
                "id": f"pmb:{action.get('id')}",
                "kind": "pmb_organization",
                "title": f"PMB 整理：{action.get('id')}",
                "sources": [str(pmb.get("pmb_db") or "")],
                "destination": "local-pmb-memory",
                "keep": [
                    f"候选数量：{action.get('candidate_count', action.get('candidate_groups', 0))}",
                    f"模式：{action.get('mode')}",
                ],
                "exclude": [
                    "不自动删除 PMB 事实",
                    "不自动覆盖长期记忆",
                    "不把敏感候选原文写入审批摘要",
                ],
                "sensitive_severity": "review" if "sensitive" in str(action.get("id") or "") else "",
                "drift_severity": "review" if "drift" in str(action.get("id") or "") else "",
                "future_behavior_impact": "降低重复、过期当前态和敏感候选对后续召回的干扰。",
                "execution": "批准后生成更细 repair/apply 计划；默认不直接删除或重写 PMB。",
                "validation": [
                    "python _bridge\\memory_governance.py pmb-organize-plan",
                    "python _bridge\\memory_governance.py validate",
                ],
                "approval_request": "批准进入更细整理计划，或保持只读观察。",
            }
        )

    generated_at = now_iso()
    review_cards = build_review_cards(approval_items)
    return {
        "schema": "memory_governance.review_summary.v1",
        "ok": True,
        "generated_at": generated_at,
        "dry_run": True,
        "requires_user_approval_before_apply": True,
        "writes_memory": False,
        "approval_item_count": len(approval_items),
        "approval_items": approval_items,
        "total_review_cards": len(review_cards),
        "review_cards": review_cards,
        "cards": review_cards,
        "display_contract": {
            "format": "review_cards_required_before_apply",
            "required_fields": ["id", "kind", "title", "sources", "destination", "keep", "exclude", "risk", "approval_action"],
            "user_visible": True,
            "fallback": "render review_text only if card rendering is unavailable",
        },
        "source_plans": {
            "profile_schema": profile.get("schema"),
            "consolidation_schema": consolidation.get("schema"),
            "absorb_schema": absorb.get("schema"),
            "pmb_organize_schema": pmb.get("schema"),
        },
        "review_text": build_review_text(approval_items, generated_at=generated_at),
        "closeout_rule": (
            "When any memory plan has approval items, Codex must show review_cards "
            "or an equivalent card list in the final/user-facing response before applying changes."
        ),
    }
