from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_finalizer import memory_plan
from project_checkpoint_finalize import Checkpoint, backup_manifest, build_suggestions


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
            path="checkpoints/codex-work-environment/test.md",
        )

    def test_suggestions_use_active_pmb_and_checkpoint_owners(self) -> None:
        suggestions = build_suggestions(self.checkpoint())

        self.assertEqual(set(suggestions), {"pmb_memory", "project_checkpoint"})
        self.assertEqual(suggestions["pmb_memory"]["owner"], "local-pmb-memory")
        self.assertEqual(suggestions["pmb_memory"]["candidate"]["text"], "Stable conclusion.")

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
