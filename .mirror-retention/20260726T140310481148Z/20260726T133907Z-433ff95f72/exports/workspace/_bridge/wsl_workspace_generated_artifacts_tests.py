#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import wsl_workspace_generated_artifacts as artifacts


class WorkGitGeneratedArtifactsTests(unittest.TestCase):
    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / ".git").mkdir()
        (root / ".gitignore").write_text(
            "\n".join(str(item["ignore_pattern"]) for item in artifacts.ARTIFACTS) + "\n",
            encoding="utf-8",
        )
        return root

    def test_plan_classifies_only_fixed_generated_artifacts(self) -> None:
        root = self._root()
        cache = root / ".vs"
        cache.mkdir()
        (cache / "VSWorkspaceState.json").write_text("{}", encoding="utf-8")
        (root / "user-data").mkdir()

        plan = artifacts.cleanup_plan(root)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["candidate_count"], 1)
        self.assertEqual(
            [row["relative_path"] for row in plan["candidates"] if row["eligible"]],
            [".vs"],
        )
        self.assertFalse(any(row["relative_path"] == "user-data" for row in plan["candidates"]))

    def test_apply_requires_confirmation_then_removes_generated_cache(self) -> None:
        root = self._root()
        cache = root / ".vs"
        cache.mkdir()
        (cache / "slnx.sqlite").write_bytes(b"")

        blocked = artifacts.cleanup_apply(root, "")
        self.assertFalse(blocked["ok"])
        self.assertTrue(cache.exists())

        applied = artifacts.cleanup_apply(root, artifacts.CLEANUP_CONFIRM)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["deleted_count"], 1)
        self.assertFalse(cache.exists())

    def test_missing_ignore_contract_blocks_cleanup(self) -> None:
        root = self._root()
        (root / ".gitignore").write_text("**/.vs/\n", encoding="utf-8")
        (root / ".vs").mkdir()

        plan = artifacts.cleanup_plan(root)

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["blockers"][0]["code"], "generated_artifact_ignore_contract_missing")

    def test_symlink_is_never_eligible(self) -> None:
        root = self._root()
        outside = Path(tempfile.mkdtemp())
        (root / ".vs").symlink_to(outside, target_is_directory=True)

        plan = artifacts.cleanup_plan(root)

        row = next(item for item in plan["candidates"] if item["relative_path"] == ".vs")
        self.assertTrue(row["symlink"])
        self.assertFalse(row["eligible"])


if __name__ == "__main__":
    unittest.main()
