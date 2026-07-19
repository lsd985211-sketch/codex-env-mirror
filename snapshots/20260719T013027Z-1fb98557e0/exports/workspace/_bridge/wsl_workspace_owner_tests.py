#!/usr/bin/env python3
"""Regression tests for the Work Git authority and mirror release boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wsl_workspace_owner as owner


class WorkGitAuthorityTests(unittest.TestCase):
    def _paths(self) -> tuple[Path, Path]:
        root = Path(tempfile.mkdtemp())
        worktree = root / "worktree"
        bare = root / "codex-workspace.git"
        worktree.mkdir()
        bare.mkdir()
        return worktree, bare

    def _git_probe(self, *, work_head: str = "abc", bare_head: str = "abc"):
        def probe(args: list[str], _distribution: str, _user: str = owner.DEFAULT_USER, *, timeout: int = 30):
            if "--is-bare-repository" in args:
                return {"ok": True, "stdout": "true", "stderr": ""}
            if "--abbrev-ref" in args:
                return {"ok": True, "stdout": "main", "stderr": ""}
            if args[-1] == "HEAD":
                return {"ok": True, "stdout": work_head, "stderr": ""}
            if "refs/heads/main" in args:
                return {"ok": True, "stdout": bare_head, "stderr": ""}
            return {"ok": False, "stdout": "", "stderr": "unexpected_probe"}

        return probe

    def test_clean_synced_work_git_is_release_ready(self) -> None:
        worktree, bare = self._paths()
        clean = {"available": True, "clean": True, "change_count": 0, "changes": []}
        with patch.object(owner, "_wsl_git", side_effect=self._git_probe()), patch.object(owner, "git_state", return_value=clean):
            payload = owner.work_git_state(worktree, bare, "Codex-Wsl-Lab")
        self.assertTrue(payload["available"])
        self.assertTrue(payload["release_ready"])
        self.assertEqual(payload["issues"], [])
        self.assertEqual(payload["wsl_user"], "codexlab")

    def test_dirty_worktree_blocks_release_without_rejecting_git_authority(self) -> None:
        worktree, bare = self._paths()
        dirty = {"available": True, "clean": False, "change_count": 2, "changes": [" M a", "?? b"]}
        with patch.object(owner, "_wsl_git", side_effect=self._git_probe()), patch.object(owner, "git_state", return_value=dirty):
            payload = owner.work_git_state(worktree, bare, "Codex-Wsl-Lab")
        self.assertTrue(payload["available"])
        self.assertFalse(payload["release_ready"])
        self.assertEqual(payload["issues"][0]["code"], "worktree_dirty")

    def test_head_mismatch_blocks_release(self) -> None:
        worktree, bare = self._paths()
        clean = {"available": True, "clean": True, "change_count": 0, "changes": []}
        with patch.object(owner, "_wsl_git", side_effect=self._git_probe(bare_head="def")), patch.object(owner, "git_state", return_value=clean):
            payload = owner.work_git_state(worktree, bare, "Codex-Wsl-Lab")
        self.assertFalse(payload["release_ready"])
        self.assertEqual(payload["issues"][0]["code"], "worktree_bare_head_mismatch")

    def test_wsl_git_uses_local_git_inside_wsl(self) -> None:
        with patch.object(owner, "_inside_wsl", return_value=True), patch.object(
            owner,
            "_run",
            return_value={"ok": True, "stdout": "ok", "stderr": ""},
        ) as runner:
            payload = owner._wsl_git(["status"], "Codex-Wsl-Lab")
        self.assertTrue(payload["ok"])
        runner.assert_called_once_with(["git", "status"], timeout=30)

    def test_wsl_state_reports_current_distribution_inside_wsl(self) -> None:
        with patch.object(owner, "_inside_wsl", return_value=True), patch.dict(
            owner.os.environ,
            {"WSL_DISTRO_NAME": "Codex-Wsl-Lab"},
        ), patch.object(
            owner,
            "wsl_interop_state",
            return_value={"present": True, "enabled": True, "interpreter": "/init", "probe_ok": True, "error": ""},
        ):
            payload = owner.wsl_state("Codex-Wsl-Lab")
        self.assertTrue(payload["present"])
        self.assertTrue(payload["running"])
        self.assertTrue(payload["interop"]["probe_ok"])

    def test_wsl_interop_probe_uses_target_distribution(self) -> None:
        probe = {"ok": True, "stdout": "enabled\ninterpreter /init\nflags: P", "stderr": ""}
        with patch.object(owner, "_inside_wsl", return_value=False), patch.object(
            owner.shutil,
            "which",
            return_value="wsl.exe",
        ), patch.object(owner, "_run", return_value=probe) as runner:
            payload = owner.wsl_interop_state("Codex-Wsl-Lab", "codexlab")

        self.assertTrue(payload["present"])
        self.assertTrue(payload["probe_ok"])
        self.assertEqual(payload["interpreter"], "/init")
        command = runner.call_args.args[0]
        self.assertEqual(command[1:5], ["-d", "Codex-Wsl-Lab", "-u", "codexlab"])


if __name__ == "__main__":
    unittest.main()
