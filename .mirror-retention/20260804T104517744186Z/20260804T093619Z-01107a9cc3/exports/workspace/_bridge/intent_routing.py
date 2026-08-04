#!/usr/bin/env python3
"""Shared deterministic intent-signal analysis for workspace routers.

Ownership: normalize request text, match bounded terms, suppress locally
negated evidence, score intent rules, and expose explainable ranking records.
Non-goals: executing routes, choosing owner permissions, calling models or
network services, persisting state, or replacing domain-specific policy.
State behavior: pure and read-only; results depend only on supplied text and
rules.
Caller context: workflow, skill, resource, maintenance, and capability routers
that need consistent lexical evidence before applying their own policy.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence


CLAUSE_BOUNDARY_RE = re.compile(r"[\n\r,，。；;！!？?]+")
ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+\-/]*")
NEGATION_WINDOW = 28
NEGATION_PATTERNS = (
    re.compile(r"(?:不要|无需|无须|不需要|不是|不能|禁止|避免|排除|不应|不可|不必|没有)[^,，。；;！!？?]{0,12}$"),
    re.compile(r"不(?!同|少|只|仅|一定|一样)[^,，。；;！!？?]{0,4}$"),
    re.compile(r"(?:do\s+not|don't|doesn't|didn't|not|no|without|avoid|exclude|never)(?:\s+\w+){0,3}\s*$"),
)
NEGATION_RESETS = ("而是", "但是", "但", "instead", "however", "but", "rather")

# Installation words can describe either a requested side effect or an
# observed local fact. Keep that distinction in the shared lexical layer so
# resource and workflow fact routers cannot drift apart.
INSTALLATION_STATE_PATTERNS = (
    r"(?:已|已经|本机|本地|当前|现有|已有|预装|内置)\s*安装(?:了|有)?",
    r"(?:未|没有|尚未|尚无)\s*安装",
    r"安装\s*(?:状态|情况|版本|路径|位置|信息|记录|列表|能力)",
    r"\b(?:already\s+installed|pre[- ]?installed|installed)\b",
    r"\binstallation\s+(?:status|state|version|path|location|info|list)\b",
)


@dataclass(frozen=True)
class IntentRule:
    key: str
    terms: tuple[str, ...]
    low_signal_terms: tuple[str, ...] = ()
    priority: int = 0


@lru_cache(maxsize=4096)
def _normalize_text_cached(text: str) -> str:
    """Reuse bounded normalized text artifacts; never cache routing results."""

    text = text.lower().replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_text(value: str) -> str:
    return _normalize_text_cached(str(value or ""))


def installation_action_text(value: str) -> str:
    """Remove installation facts before testing for an install action."""

    normalized = normalize_text(value)
    for pattern in INSTALLATION_STATE_PATTERNS:
        normalized = re.sub(pattern, " ", normalized, flags=re.IGNORECASE)
    return normalized


def has_install_action(value: str) -> bool:
    """Return whether a request still contains an explicit install action."""

    action_text = installation_action_text(value)
    return any(
        term_matches(action_text, term)
        for term in ("安装", "install", "重新安装", "reinstall", "升级", "upgrade", "卸载", "uninstall")
    )


def ascii_tokens(value: str) -> set[str]:
    return set(ASCII_TOKEN_RE.findall(normalize_text(value)))


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str] | None:
    """Reuse bounded compiled ASCII boundaries; never cache routing results."""

    if any(ord(ch) > 127 for ch in term):
        return None
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


def _occurrences(text: str, term: str) -> list[tuple[int, int]]:
    needle = normalize_text(term)
    if not needle:
        return []
    pattern = _term_pattern(needle)
    if pattern is not None:
        return [(match.start(), match.end()) for match in pattern.finditer(text)]
    output: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return output
        output.append((index, index + len(needle)))
        start = index + max(1, len(needle))


def _local_prefix(text: str, start: int) -> str:
    clause_start = 0
    for match in CLAUSE_BOUNDARY_RE.finditer(text, 0, start):
        clause_start = match.end()
    prefix = text[max(clause_start, start - NEGATION_WINDOW) : start]
    for reset in NEGATION_RESETS:
        index = prefix.rfind(reset)
        if index >= 0:
            prefix = prefix[index + len(reset) :]
    return re.sub(r"\bnot\s+only\b", "", prefix).strip()


def occurrence_is_negated(text: str, start: int) -> bool:
    prefix = _local_prefix(text, start)
    return any(pattern.search(prefix) for pattern in NEGATION_PATTERNS)


def term_evidence(text: str, term: str) -> dict[str, Any]:
    normalized = normalize_text(text)
    positions = _occurrences(normalized, term)
    positive = [(start, end) for start, end in positions if not occurrence_is_negated(normalized, start)]
    negated = [(start, end) for start, end in positions if occurrence_is_negated(normalized, start)]
    return {
        "term": str(term),
        "matched": bool(positive),
        "positive_count": len(positive),
        "negated_count": len(negated),
        "positive_positions": positive,
        "negated_positions": negated,
    }


def term_matches(text: str, term: str, *, include_negated: bool = False) -> bool:
    evidence = term_evidence(text, term)
    return bool(evidence["matched"] or (include_negated and evidence["negated_count"]))


def matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    return [str(term) for term in terms if term_matches(text, str(term))]


def negated_terms(text: str, terms: Iterable[str]) -> list[str]:
    output: list[str] = []
    for term in terms:
        evidence = term_evidence(text, str(term))
        if evidence["negated_count"] and not evidence["positive_count"]:
            output.append(str(term))
    return output


def term_weight(term: str, *, low_signal_terms: Iterable[str] = ()) -> int:
    needle = normalize_text(term)
    low_signal = {normalize_text(item) for item in low_signal_terms}
    if needle in low_signal:
        return 1
    if any(separator in needle for separator in (" ", "-", "_", "/", "\\")):
        return 4
    if any(ord(ch) > 127 for ch in needle):
        return 3 if len(needle) >= 3 else (2 if len(needle) == 2 else 1)
    return 3 if len(needle) >= 5 else (2 if len(needle) >= 3 else 1)


def rank_intents(
    text: str,
    rules: Sequence[IntentRule],
    *,
    explicit_keys: Iterable[str] = (),
    bonuses: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    explicit = {str(item) for item in explicit_keys if str(item)}
    score_bonuses = bonuses or {}
    records: list[dict[str, Any]] = []
    for rule in rules:
        hits = matched_terms(text, rule.terms)
        suppressed = negated_terms(text, rule.terms)
        is_explicit = rule.key in explicit
        bonus = int(score_bonuses.get(rule.key, 0))
        if not hits and not is_explicit and bonus <= 0:
            continue
        low_signal = {normalize_text(item) for item in rule.low_signal_terms}
        weights = {hit: term_weight(hit, low_signal_terms=low_signal) for hit in hits}
        lexical_score = sum(weights.values())
        explicit_score = 100 if is_explicit else 0
        score = explicit_score + lexical_score + int(rule.priority) + bonus
        records.append(
            {
                "key": rule.key,
                "score": score,
                "lexical_score": lexical_score,
                "explicit_score": explicit_score,
                "priority": int(rule.priority),
                "bonus": bonus,
                "hits": hits,
                "weights": weights,
                "suppressed_negated_hits": suppressed,
                "low_signal_only": bool(hits) and all(normalize_text(hit) in low_signal for hit in hits),
                "explicit": is_explicit,
            }
        )
    records.sort(key=lambda item: (-int(item["score"]), str(item["key"])))
    top_score = int(records[0]["score"]) if records else 0
    second_score = int(records[1]["score"]) if len(records) > 1 else 0
    for record in records:
        score = int(record["score"])
        record["candidate_ratio"] = round(score / max(top_score, 1), 3)
        record["route_confidence"] = 1.0 if second_score <= 0 else round(top_score / max(top_score + second_score, 1), 3)
        record["top_margin"] = top_score - second_score
    return records


def validate() -> dict[str, Any]:
    _normalize_text_cached.cache_clear()
    _term_pattern.cache_clear()
    rules = (
        IntentRule("package", ("package", "dependency", "install", "安装", "依赖")),
        IntentRule("skills", ("skill", "技能")),
        IntentRule("process", ("kill", "stop", "停止")),
    )
    negative_package = rank_intents("Research routing. Do not install packages or dependencies.", rules)
    skill_boundary = rank_intents("Improve skill routing and classification", rules)
    positive_package = rank_intents("Install the package dependency", rules)
    chinese_negative = rank_intents("不要安装依赖，只查询官方文档", rules)
    inventory_install = has_install_action("识别本机已经安装和可用的 OCR 能力")
    explicit_install = has_install_action("安装 PaddleOCR")
    install_after_probe = has_install_action("未安装 PaddleOCR，需要安装依赖")
    compound_bonus = rank_intents("现有资产施工", rules, bonuses={"process": 3})
    different_images = term_evidence("下载十张不同图片", "图片")
    repeated_ascii_first = term_matches("Install ripgrep, not grepper.", "ripgrep")
    repeated_ascii_second = term_matches("Install ripgrep again.", "ripgrep")
    boundary_rejected = not term_matches("Install ripgrep, not grepper.", "grep")
    unicode_first = term_matches("查询依赖文档", "依赖")
    unicode_second = term_matches("查询依赖文档", "依赖")
    reuse_info = _term_pattern.cache_info()
    normalized_expected = re.sub(
        r"[ \t]+",
        " ",
        str([" A\tB "] or "").lower().replace("\u3000", " "),
    ).strip()
    normalized_non_string_first = normalize_text([" A\tB "])
    normalized_before_reuse = _normalize_text_cached.cache_info()
    normalized_non_string_second = normalize_text([" A\tB "])
    normalized_after_reuse = _normalize_text_cached.cache_info()
    for index in range(4_200):
        _term_pattern(f"bounded-term-{index}")
        normalize_text(f" BOUNDED\tTEXT {index} ")
    bounded_info = _term_pattern.cache_info()
    normalized_bounded_info = _normalize_text_cached.cache_info()
    checks = [
        {"name": "negated_package_suppressed", "ok": not any(item["key"] == "package" for item in negative_package)},
        {"name": "skill_does_not_match_kill", "ok": [item["key"] for item in skill_boundary] == ["skills"]},
        {"name": "positive_package_matches", "ok": bool(positive_package and positive_package[0]["key"] == "package")},
        {"name": "chinese_negation_suppressed", "ok": not any(item["key"] == "package" for item in chinese_negative)},
        {
            "name": "installation_inventory_is_not_an_action",
            "ok": not inventory_install and explicit_install and install_after_probe,
        },
        {"name": "different_is_not_negation", "ok": bool(different_images["matched"] and not different_images["negated_count"])},
        {"name": "compound_context_bonus_can_admit_without_isolated_keyword", "ok": [item["key"] for item in compound_bonus] == ["process"]},
        {
            "name": "compiled_ascii_pattern_reused_with_boundary_semantics",
            "ok": repeated_ascii_first and repeated_ascii_second and boundary_rejected and reuse_info.hits >= 1,
        },
        {
            "name": "unicode_substring_semantics_preserved",
            "ok": unicode_first and unicode_second,
        },
        {
            "name": "compiled_pattern_cache_is_bounded",
            "ok": bounded_info.maxsize == 4096 and bounded_info.currsize <= bounded_info.maxsize,
        },
        {
            "name": "normalized_text_cache_preserves_input_conversion",
            "ok": normalized_non_string_first == normalized_expected == normalized_non_string_second,
        },
        {
            "name": "normalized_text_artifact_is_reused",
            "ok": normalized_after_reuse.hits == normalized_before_reuse.hits + 1,
        },
        {
            "name": "normalized_text_cache_is_bounded",
            "ok": (
                normalized_bounded_info.maxsize == 4096
                and normalized_bounded_info.currsize <= normalized_bounded_info.maxsize
            ),
        },
        {
            "name": "match_results_are_not_cached",
            "ok": not term_matches("avoid install", "install") and term_matches("install now", "install"),
        },
    ]
    return {"schema": "intent_routing.validate.v1", "ok": all(item["ok"] for item in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate shared deterministic intent routing primitives.")
    parser.add_argument("command", choices=("validate", "analyze"))
    parser.add_argument("--text", default="")
    parser.add_argument("--terms", nargs="*", default=[])
    args = parser.parse_args()
    if args.command == "validate":
        payload = validate()
    else:
        payload = {
            "schema": "intent_routing.analyze.v1",
            "ok": True,
            "text": args.text,
            "evidence": [term_evidence(args.text, term) for term in args.terms],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
