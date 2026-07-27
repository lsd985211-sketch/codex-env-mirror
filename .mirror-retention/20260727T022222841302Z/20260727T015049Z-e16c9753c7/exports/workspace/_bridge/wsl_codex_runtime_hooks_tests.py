#!/usr/bin/env python3
"""Focused tests for governed WSL Codex hook projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import wsl_codex_runtime as owner


class WslCodexRuntimeHooksTests(unittest.TestCase):
    def test_render_hooks_uses_wsl_workspace_and_observational_events_only(self) -> None:
        rendered = owner.render_hooks()
        payload = json.loads(rendered)
        self.assertEqual({"UserPromptSubmit", "PostToolUse", "Stop"}, set(payload["hooks"]))
        self.assertNotIn("PreToolUse", rendered)
        self.assertIn(str(owner.ROOT), rendered)
        self.assertNotIn("C:\\Users\\", rendered)

    def test_materialize_writes_hooks_under_existing_config_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_home = root / "codex-home"
            sqlite_home = root / "sqlite-home"
            profile = root / ".profile"
            lease = Mock(unsafe=True)

            def write_text(path: Path, text: str) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            with (
                patch.object(owner, "CODEX_HOME", codex_home),
                patch.object(owner, "SQLITE_HOME", sqlite_home),
                patch.object(owner, "PROFILE_PATH", profile),
                patch.object(owner, "reconcile_environment_selection", return_value={"changed": False}),
                patch.object(owner, "render_config", return_value=""),
                patch.object(owner, "desktop_table_from_config", return_value=""),
                patch.object(owner, "portable_root_values_from_text", return_value={}),
                patch.object(owner, "render_profile", return_value=""),
                patch.object(owner, "link_or_verify", return_value={"ok": True, "status": "linked"}),
                patch.object(owner, "link_skill_tree", return_value={"ok": True, "status": "linked"}),
                patch.object(owner, "project_sessions", return_value={"ok": True, "changed": False}),
                patch.object(owner, "project_state_db", return_value={"ok": True, "changed": False}),
                patch.object(owner, "project_plugins", return_value={"ok": True, "changed": False}),
                patch.object(owner.state_write_authority, "try_acquire_state_write_lease", return_value=lease) as acquire,
                patch.object(owner, "atomic_write_text", side_effect=write_text),
            ):
                result = owner.materialize(write=True)

            hooks = codex_home / "hooks.json"
            self.assertTrue(result["ok"], result)
            self.assertTrue(hooks.is_file())
            self.assertEqual(owner.render_hooks(), hooks.read_text(encoding="utf-8"))
            acquire.assert_called_once()
            lease.assert_current.assert_called_once()
            lease.release.assert_called_once()

    def test_focused_hooks_projection_does_not_touch_unrelated_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "codex-home"
            lease = Mock(unsafe=True)

            def write_text(path: Path, text: str) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            with (
                patch.object(owner, "CODEX_HOME", codex_home),
                patch.object(owner.state_write_authority, "try_acquire_state_write_lease", return_value=lease),
                patch.object(owner, "atomic_write_text", side_effect=write_text),
                patch.object(owner, "project_sessions") as sessions,
                patch.object(owner, "project_state_db") as state_db,
                patch.object(owner, "project_plugins") as plugins,
            ):
                result = owner.hooks_projection(write=True)

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["matches_template"])
            self.assertFalse(result["unrelated_runtime_projection_touched"])
            sessions.assert_not_called()
            state_db.assert_not_called()
            plugins.assert_not_called()
            lease.assert_current.assert_called_once()
            lease.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
