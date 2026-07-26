#!/usr/bin/env python3
"""Structured read-only knowledge objects for iteration-layer extraction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeCandidate:
    candidate_id: str
    type: str
    summary: str
    evidence_refs: list[str]
    scope: str
    preconditions: list[str]
    recommended_action: str
    confidence: str
    promotion_target: str
    source_kind: str


@dataclass(frozen=True)
class KnowledgeCluster:
    cluster_id: str
    type: str
    summary: str
    candidate_ids: list[str]
    scope: str
    promotion_target: str
    confidence: str


@dataclass(frozen=True)
class KnowledgePromotionSuggestion:
    cluster_id: str
    priority: str
    target: str
    reason: str
    validation: str
    apply_mode: str = "proposal_only"
