#!/usr/bin/env python3
"""Regression tests for owner-marked work-note dispositions."""

from __future__ import annotations

import tempfile
from pathlib import Path

import memory_work_notes
from workflow_closeout_package import build_pending_disposition


def no_sensitive_hits(_: str) -> list[dict[str, str]]:
    return []


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "current.jsonl"
        rejected = memory_work_notes.work_note_add(path, "retain this draft", sensitive_hits=no_sensitive_hits, scope="officecli-evaluation-draft")
        assert not rejected["ok"]
        assert rejected["reason"] == "persistent_draft_requires_drafts_store"
        assert rejected["recommended_path"] == "_bridge/shared/drafts/"
        assert not path.exists()

        added = memory_work_notes.work_note_add(path, "check the route after the main task", sensitive_hits=no_sensitive_hits, scope="tool-routing")
        assert added["ok"]
        note_id = added["id"]
        before = memory_work_notes.work_note_read(path)
        assert before["active_count"] == 1 and len(before["entries"]) == 1
        pending_before = build_pending_disposition(notes=before["entries"], proposals=[], profile_candidate_count=0, external_candidate_count=0, fallback_tools=[], negative_items=[], unverified_items=[])
        assert any(item.get("kind") == "work_notes" for item in pending_before["items"])

        disposed = memory_work_notes.work_note_dispose(path, ids=[note_id], disposition="deferred")
        assert disposed["ok"] and disposed["content_preserved"] and disposed["removed_from_pending"]
        after = memory_work_notes.work_note_read(path)
        assert after["active_count"] == 0 and after["entries"] == [] and not path.exists()
        archive = path.with_name("resolved.jsonl")
        archived = memory_work_notes._read_entries(archive)
        assert archived[0]["text"] == "check the route after the main task"
        assert archived[0]["disposition"] == "deferred"
        pending_after = build_pending_disposition(notes=after["entries"], proposals=[], profile_candidate_count=0, external_candidate_count=0, fallback_tools=[], negative_items=[], unverified_items=[])
        assert not any(item.get("kind") == "work_notes" for item in pending_after["items"])
    print("memory_work_notes disposition regression ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
