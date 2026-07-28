#!/usr/bin/env python3
"""Regression tests for two-axis draft artifact governance."""

from __future__ import annotations

import draft_governance
import workflow_orchestrator
from workflow_closeout_package import build_pending_disposition


def primary_domain(message: str) -> str:
    records = workflow_orchestrator.classify(message)
    for item in records:
        if workflow_orchestrator.domain_drives_execution(item):
            return str(item["domain"].key)
    return ""


def main() -> int:
    result = draft_governance.validate()
    assert result["ok"], result["issues"]
    snapshot = draft_governance.snapshot()
    assert snapshot["artifact_count"] == 2
    items = {item["id"]: item for item in snapshot["items"]}
    assert "officecli-evaluation-draft-20260711" in items
    assert "workflow-entry-optimization-draft-20260704" in items
    assert "secret-vault-draft-20260630" not in items
    assert all(item["workflow_status"] == "retained_reference" for item in items.values())
    assert all(item["pending_action"] == "none" for item in items.values())
    pending = build_pending_disposition(
        notes=[],
        proposals=[],
        profile_candidate_count=0,
        external_candidate_count=0,
        fallback_tools=[],
        negative_items=[],
        unverified_items=[],
    )
    assert pending["pending_count"] == 0
    assert primary_domain("保留这份 OfficeCLI 草案，不审批、不执行") == "workflow_governance"
    assert primary_domain("提交 OfficeCLI 草案审批") == "workflow_governance"
    assert primary_domain("总结这份草案内容") == "general"
    print("draft governance regression ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
