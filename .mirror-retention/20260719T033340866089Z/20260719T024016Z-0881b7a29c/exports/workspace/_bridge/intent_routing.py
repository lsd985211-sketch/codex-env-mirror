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


@dataclass(frozen=True)
class IntentRule:
    key: str
    terms: tuple[str, ...]
    low_signal_terms: tuple[str, ...] = ()
    priority: int = 0


def normalize_text(value: str) -> str:
    text = str(value or "").lower().replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def ascii_tokens(value: str) -> set[str]:
    return set(ASCII_TOKEN_RE.findall(normalize_text(value)))


def _term_pattern(term: str) -> re.Pattern[str] | None:
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
        if not hits and not is_explicit:
            continue
        low_signal = {normalize_text(item) for item in rule.low_signal_terms}
        weights = {hit: term_weight(hit, low_signal_terms=low_signal) for hit in hits}
        lexical_score = sum(weights.values())
        explicit_score = 100 if is_explicit else 0
        bonus = int(score_bonuses.get(rule.key, 0))
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
    rules = (
        IntentRule("package", ("package", "dependency", "install", "安装", "依赖")),
        IntentRule("skills", ("skill", "技能")),
        IntentRule("process", ("kill", "stop", "停止")),
    )
    negative_package = rank_intents("Research routing. Do not install packages or dependencies.", rules)
    skill_boundary = rank_intents("Improve skill routing and classification", rules)
    positive_package = rank_intents("Install the package dependency", rules)
    chinese_negative = rank_intents("不要安装依赖，只查询官方文档", rules)
    different_images = term_evidence("下载十张不同图片", "图片")
    checks = [
        {"name": "negated_package_suppressed", "ok": not any(item["key"] == "package" for item in negative_package)},
        {"name": "skill_does_not_match_kill", "ok": [item["key"] for item in skill_boundary] == ["skills"]},
        {"name": "positive_package_matches", "ok": bool(positive_package and positive_package[0]["key"] == "package")},
        {"name": "chinese_negation_suppressed", "ok": not any(item["key"] == "package" for item in chinese_negative)},
        {"name": "different_is_not_negation", "ok": bool(different_images["matched"] and not different_images["negated_count"])},
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
