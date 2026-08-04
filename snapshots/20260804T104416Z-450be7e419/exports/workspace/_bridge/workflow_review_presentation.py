#!/usr/bin/env python3
"""Chinese-primary presentation policy for user-facing approval cards.

Ownership: pure localization and validation of workflow approval presentation.
Non-goals: translating source evidence, changing review identity or lifecycle,
inferring approval, or mutating queue state.
State behavior: pure/read-only.
Caller context: iteration candidate generation and review delivery projection.
"""

from __future__ import annotations

import re
from typing import Any


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

SIGNAL_KIND_TITLES = {
    "checkpoint_stable_conclusion": "已验证的迭代候选：稳定结论",
    "user_correction": "已验证的迭代候选：用户纠正",
    "verified_root_cause": "已验证的迭代候选：根因",
    "prevention_guard": "已验证的迭代候选：预防性约束",
    "repeated_manual_step": "已验证的迭代候选：重复人工步骤",
}

REQUIRED_CHECK_TRANSLATIONS = {
    "Verify the conclusion is durable and current": "确认结论持久且仍然有效",
    "Review privacy and secret classification": "审查隐私与秘密分级",
    "Apply only through the mapped owner with explicit confirmation": "仅在明确确认后通过映射的 owner 应用",
    "Validate owner readback/recall before resolving": "在标记 resolved 前验证 owner 回读或召回结果",
    "Apply only the exact approved proposal scope": "仅应用本次精确批准的提案范围",
    "Use the supplied proposal detail": "依据已提供的提案详情作出决定",
}

PROPOSAL_TITLE_TRANSLATIONS = {
    "Phase 1: reduce low-risk route overhead and reconcile resource attachment satisfaction; no safety-gate weakening.": "第一阶段：降低低风险路由开销并核对资源附件满足情况（不削弱安全门禁）",
}

PROPOSAL_SUMMARY_TRANSLATIONS = {
    "Phase 1: reduce low-risk route overhead and reconcile resource attachment satisfaction; no safety-gate weakening.": "降低低风险路由的重复开销，并核对资源附件是否真正满足调用方需求；实施不得削弱任何安全门禁。",
}


def is_chinese_primary_title(value: Any) -> bool:
    """Return whether a user-facing title has a Chinese primary frame."""

    return bool(_CJK.search(str(value or "").strip()))


def title_for_signal(signal_kind: Any) -> str:
    key = str(signal_kind or "").strip()
    return SIGNAL_KIND_TITLES.get(key, f"已验证的迭代候选：{key or '待分类事项'}")


def project_title(title: Any, *, kind: Any = "", signal_kind: Any = "") -> str:
    """Project a title without translating or replacing its technical literal."""

    original = str(title or "").strip()
    signal = str(signal_kind or "").strip()
    if is_chinese_primary_title(original):
        return original
    if (
        str(kind or "").strip() == "iteration_candidates"
        and original.casefold().startswith("verified iteration candidate:")
        and signal
    ):
        return title_for_signal(signal)
    if str(kind or "").strip() == "proposals" and original in PROPOSAL_TITLE_TRANSLATIONS:
        return PROPOSAL_TITLE_TRANSLATIONS[original]
    return f"审批事项：{original or '未命名事项'}"


def project_required_checks(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    projected: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        projected.append(
            REQUIRED_CHECK_TRANSLATIONS.get(text, text if _CJK.search(text) else f"检查项：{text}")
        )
    return projected


def project_summary(summary: Any, *, kind: Any = "") -> tuple[str, bool]:
    """Return Chinese-primary summary text and whether it is approval-ready."""

    original = str(summary or "").strip()
    if not original:
        return "未提供摘要", False
    if is_chinese_primary_title(original):
        return original, True
    if str(kind or "").strip() == "proposals" and original in PROPOSAL_SUMMARY_TRANSLATIONS:
        return PROPOSAL_SUMMARY_TRANSLATIONS[original], True
    return f"中文摘要待生产者补充；原始证据：{original}", False


def project_card(card: dict[str, Any]) -> dict[str, Any]:
    """Return one presentation-only card projection; identity fields are unchanged."""

    projected = dict(card)
    attributes = card.get("attributes") if isinstance(card.get("attributes"), dict) else {}
    projected["title"] = project_title(
        card.get("title"),
        kind=card.get("kind"),
        signal_kind=attributes.get("signal_kind"),
    )
    projected["summary"], summary_complete = project_summary(
        card.get("summary"),
        kind=card.get("kind"),
    )
    projected["required_checks"] = project_required_checks(card.get("required_checks"))
    projected["presentation_language"] = "zh-CN-primary"
    projected["presentation_complete"] = bool(
        is_chinese_primary_title(projected["title"]) and summary_complete
    )
    return projected
