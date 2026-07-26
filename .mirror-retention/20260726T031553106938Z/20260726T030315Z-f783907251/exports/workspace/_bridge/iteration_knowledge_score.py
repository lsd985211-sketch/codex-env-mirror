#!/usr/bin/env python3
"""Scoring helpers for read-only iteration knowledge extraction."""

from __future__ import annotations

from typing import Iterable


CONFIDENCE_ORDER = {
    "verified_runtime": 4,
    "verified_file": 3,
    "derived_pattern": 2,
    "heuristic": 1,
}


def confidence_rank(value: str) -> int:
    return CONFIDENCE_ORDER.get(str(value or "").strip().lower(), 0)


def merge_confidence(values: Iterable[str]) -> str:
    best = "heuristic"
    best_rank = -1
    for value in values:
        rank = confidence_rank(value)
        if rank > best_rank:
            best = value
            best_rank = rank
    return best


def suggestion_priority(scope: str, candidate_count: int, confidence: str) -> str:
    scope_text = str(scope or "").strip().lower()
    confidence_text = str(confidence or "").strip().lower()
    if confidence_text == "verified_runtime" and candidate_count >= 2:
        return "P1"
    if "bridge" in scope_text or "resource" in scope_text or "gui" in scope_text:
        return "P1" if candidate_count >= 1 else "P2"
    return "P2" if candidate_count >= 2 else "P3"
