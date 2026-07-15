#!/usr/bin/env python3

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_environment_mirror as mirror


class CodexEnvironmentMirrorTests(unittest.TestCase):
    def test_refresh_requires_explicit_confirmation(self) -> None:
        payload = mirror.refresh("")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["required_confirmation"], mirror.REFRESH_CONFIRMATION)

    def test_stage_requires_target_and_confirmation(self) -> None:
        self.assertEqual(mirror.execute("stage")["reason"], "target_root_required")
        payload = mirror.execute("stage", target_root=r"C:\Restore")
        self.assertEqual(payload["reason"], "confirmation_required")

    def test_prune_keeps_only_selected_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshots = root / "snapshots"
            (snapshots / "old").mkdir(parents=True)
            (snapshots / "keep").mkdir()
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                removed = mirror.prune_superseded_snapshots("keep")
            self.assertEqual(removed, ["old"])
            self.assertTrue((snapshots / "keep").is_dir())
            self.assertFalse((snapshots / "old").exists())

    def test_status_reports_missing_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshots").mkdir()
            with patch.dict(os.environ, {"CODEX_ENV_MIRROR_ROOT": str(root)}):
                payload = mirror.status()
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["issues"], [])


if __name__ == "__main__":
    unittest.main()
