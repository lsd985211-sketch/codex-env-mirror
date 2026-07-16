from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from _bridge import memory_pmb_workspaces


def create_workspace(root: Path, workspace_id: str, *, events: int) -> Path:
    workspace = root / workspace_id
    workspace.mkdir(parents=True)
    db_path = workspace / "events.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, archived_at TEXT)")
        conn.executemany("INSERT INTO events (archived_at) VALUES (NULL)", [()] * events)
        conn.commit()
    finally:
        conn.close()
    return workspace


class PmbWorkspaceRetirementTests(unittest.TestCase):
    def test_invalid_workspace_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            result = memory_pmb_workspaces.workspace_retire_plan(
                root,
                "../escape",
                active_workspace_id="main",
                quarantine_root=Path(temp_dir) / "retired",
                tombstone_path=Path(temp_dir) / "tombstones.jsonl",
            )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["blockers"][0]["code"], "invalid_workspace_id")

    def test_active_workspace_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            create_workspace(root, "main", events=0)
            result = memory_pmb_workspaces.workspace_retire_plan(
                root,
                "main",
                active_workspace_id="main",
                quarantine_root=Path(temp_dir) / "retired",
                tombstone_path=Path(temp_dir) / "tombstones.jsonl",
            )
        self.assertFalse(result["eligible"])
        self.assertIn("active_workspace_protected", {item["code"] for item in result["blockers"]})

    def test_nonempty_workspace_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            create_workspace(root, "candidate", events=1)
            result = memory_pmb_workspaces.workspace_retire_plan(
                root,
                "candidate",
                active_workspace_id="main",
                quarantine_root=Path(temp_dir) / "retired",
                tombstone_path=Path(temp_dir) / "tombstones.jsonl",
            )
        self.assertFalse(result["eligible"])
        self.assertIn("workspace_not_empty", {item["code"] for item in result["blockers"]})

    def test_empty_workspace_moves_to_quarantine_and_writes_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "workspaces"
            source = create_workspace(root, "candidate", events=0)
            tombstone_path = base / "governance" / "tombstones.jsonl"
            result = memory_pmb_workspaces.workspace_retire_apply(
                root,
                "candidate",
                active_workspace_id="main",
                quarantine_root=base / "retired",
                tombstone_path=tombstone_path,
                reason="test",
                confirm=True,
            )
            destination = Path(result["destination"])
            self.assertTrue(result["ok"])
            self.assertFalse(source.exists())
            self.assertTrue(destination.is_dir())
            self.assertTrue((destination / "RETIREMENT_TOMBSTONE.json").is_file())
            self.assertIn('"workspace_id": "candidate"', tombstone_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
