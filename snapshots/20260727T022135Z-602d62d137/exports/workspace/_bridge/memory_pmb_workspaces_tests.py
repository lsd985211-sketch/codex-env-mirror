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
    (workspace / "meta.yaml").write_text(
        "\n".join(
            [
                f"id: {workspace_id}",
                r"name: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager",
                r"root: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager",
                "source: env",
                "created_at: '2026-06-30T10:27:56.381511Z'",
                "embedding:",
                "  backend: fastembed",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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


class PmbWorkspaceRebindTests(unittest.TestCase):
    TARGET_NAME = "WSL Codex 工作区"
    TARGET_ROOT = r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace"

    def test_rebind_plan_preserves_workspace_id_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            create_workspace(root, "mcsmanager", events=3)
            result = memory_pmb_workspaces.workspace_rebind_plan(
                root,
                "mcsmanager",
                target_name=self.TARGET_NAME,
                target_root=self.TARGET_ROOT,
            )
        self.assertTrue(result["ok"], result["blockers"])
        self.assertTrue(result["would_change"])
        self.assertEqual(result["before"]["id"], "mcsmanager")
        self.assertEqual(result["after"]["id"], "mcsmanager")
        self.assertEqual(result["event_counts"]["total_events"], 3)

    def test_rebind_apply_changes_only_metadata_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            workspace = create_workspace(root, "mcsmanager", events=2)
            db_path = workspace / "events.sqlite"
            before_db = memory_pmb_workspaces._sha256(db_path)
            result = memory_pmb_workspaces.workspace_rebind_apply(
                root,
                "mcsmanager",
                target_name=self.TARGET_NAME,
                target_root=self.TARGET_ROOT,
                confirm=True,
            )
            metadata = (workspace / "meta.yaml").read_text(encoding="utf-8")
            after_db = memory_pmb_workspaces._sha256(db_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(before_db, after_db)
        self.assertIn('name: "WSL Codex 工作区"', metadata)
        self.assertIn('root: "\\\\\\\\wsl.localhost', metadata)
        self.assertIn("  backend: fastembed", metadata)
        self.assertTrue(result["postconditions"]["event_database_unchanged"])

    def test_rebind_apply_rejects_metadata_changed_after_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspaces"
            workspace = create_workspace(root, "mcsmanager", events=2)
            plan = memory_pmb_workspaces.workspace_rebind_plan(
                root,
                "mcsmanager",
                target_name=self.TARGET_NAME,
                target_root=self.TARGET_ROOT,
            )
            meta_path = workspace / "meta.yaml"
            meta_path.write_text(
                meta_path.read_text(encoding="utf-8") + "concurrent_note: preserved\n",
                encoding="utf-8",
            )
            result = memory_pmb_workspaces.workspace_rebind_apply(
                root,
                "mcsmanager",
                target_name=self.TARGET_NAME,
                target_root=self.TARGET_ROOT,
                confirm=True,
                expected_meta_sha256=plan["meta_sha256"],
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "workspace_meta_changed_after_backup")


if __name__ == "__main__":
    unittest.main()
