from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from knowledge_finalizer import memory_plan
from project_checkpoint_finalize import (
    Checkpoint,
    backup_manifest,
    build_checkpoint,
    build_suggestions,
    checkpoint_path_contract,
    find_existing_checkpoint,
)


class ProjectCheckpointSuggestionsTests(unittest.TestCase):
    def checkpoint(self) -> Checkpoint:
        return Checkpoint(
            project_id="codex-work-environment",
            change_type="maintenance",
            title="Test",
            summary="Verified summary.",
            evidence=[],
            verification=[],
            backups=[],
            changed_files=[],
            stable_conclusions=["Stable conclusion."],
            followups=[],
            created_at="2026-07-12T00:00:00+00:00",
            checkpoint_id="checkpoint-test",
            input_signature="abc123",
            logical_ref="checkpoints/codex-work-environment/test.md",
        )

    def test_checkpoint_identity_is_stable_for_the_same_semantic_input(self) -> None:
        args = Namespace(
            project_id="demo",
            change_type="maintenance",
            title="Stable closeout",
            summary="Verified",
            evidence=["receipt=ok"],
            verification=["tests=ok"],
            backup=["manifest.json"],
            changed_file=["_bridge/example.py"],
            stable_conclusion=["reuse by signature"],
            followup=[],
        )
        first = build_checkpoint(args)
        second = build_checkpoint(args)

        self.assertEqual(first.input_signature, second.input_signature)
        self.assertEqual(first.checkpoint_id, second.checkpoint_id)

    def test_existing_checkpoint_is_reused_by_input_signature(self) -> None:
        checkpoint = self.checkpoint()
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / checkpoint.project_id
            project_root.mkdir(parents=True)
            existing = project_root / "existing.md"
            existing.write_text(f"- input_signature: {checkpoint.input_signature}\n", encoding="utf-8")
            with patch("project_checkpoint_finalize.CHECKPOINT_ROOT", Path(temp_dir)):
                result = find_existing_checkpoint(checkpoint)

        self.assertEqual(existing, result)

    def test_suggestions_use_active_pmb_and_checkpoint_owners(self) -> None:
        suggestions = build_suggestions(self.checkpoint())

        self.assertEqual(set(suggestions), {"pmb_memory", "project_checkpoint"})
        self.assertEqual(suggestions["pmb_memory"]["owner"], "local-pmb-memory")
        self.assertEqual(suggestions["pmb_memory"]["candidate"]["text"], "Stable conclusion.")
        project = suggestions["project_checkpoint"]
        self.assertNotIn("path", project)
        self.assertTrue(Path(project["workspace_path"]).is_absolute())
        self.assertEqual(
            project["workspace_relative_path"],
            "_bridge/shared/checkpoints/codex-work-environment/test.md",
        )

    def test_path_contract_distinguishes_logical_and_physical_paths(self) -> None:
        paths = checkpoint_path_contract(self.checkpoint())

        self.assertEqual(paths["logical_ref"], "checkpoints/codex-work-environment/test.md")
        self.assertNotIn("path", paths)
        self.assertNotIn("legacy_path_field", paths)
        self.assertEqual(
            paths["workspace_relative_path"],
            "_bridge/shared/checkpoints/codex-work-environment/test.md",
        )
        self.assertEqual(
            Path(paths["workspace_path"]).parts[-5:],
            ("_bridge", "shared", "checkpoints", "codex-work-environment", "test.md"),
        )

    def test_knowledge_finalizer_preserves_new_owner_contract(self) -> None:
        plan = memory_plan({"suggestions": build_suggestions(self.checkpoint())})

        self.assertIn("pmb_memory", plan)
        self.assertIn("project_checkpoint", plan)
        self.assertNotIn("candidates", plan)

    def test_existing_manifest_uses_backup_router_before_checkpoint_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "MANIFEST.md"
            manifest.write_text("# Checkpoints\n", encoding="utf-8")
            with patch("project_checkpoint_finalize.create_backup", return_value={"ok": True, "manifest_paths": ["backup.json"]}) as create:
                result = backup_manifest(manifest)
            self.assertTrue(result["ok"])
            create.assert_called_once()
            self.assertEqual(create.call_args.args[0], [str(manifest)])


if __name__ == "__main__":
    unittest.main()
