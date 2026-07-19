#!/usr/bin/env python3
"""Regression tests for the Work Git authority and mirror release boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_workspace_access_requires_writable_worktree_and_git_store(self) -> None:
        worktree, _bare = self._paths()
        (worktree / ".git").mkdir()

        payload = owner.workspace_access_state(worktree)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["worktree_writable"])
        self.assertTrue(payload["git_writable"])
        self.assertFalse(payload["root_required_for_daily_work"])

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


class DesktopProjectRegistrationTests(unittest.TestCase):
    def _state_path(self, state: dict | None = None) -> Path:
        path = Path(tempfile.mkdtemp()) / ".codex-global-state.json"
        path.write_text(json.dumps(state or {}, ensure_ascii=False), encoding="utf-8")
        return path

    def test_snapshot_accepts_current_desktop_schema_without_legacy_atoms(self) -> None:
        state: dict = {}
        owner.ensure_wsl_desktop_project(state, now_ms=123)
        state.pop("electron-saved-workspace-roots", None)
        state.pop("electron-workspace-root-labels", None)
        path = self._state_path(state)

        payload = owner.desktop_project_snapshot(path)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["registered"])
        self.assertFalse(payload["projection_required"])

    def test_apply_requires_exact_confirmation_before_backup_or_ipc(self) -> None:
        path = self._state_path()
        with patch.object(owner, "create_backup") as backup:
            payload = owner.desktop_project_apply(confirm="", global_state_path=path)

        self.assertFalse(payload["ok"])
        self.assertEqual("blocked", payload["status"])
        backup.assert_not_called()

    def test_apply_uses_live_ipc_and_requires_persisted_project(self) -> None:
        path = self._state_path()

        class FakeClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, _expression: str) -> dict:
                state = json.loads(path.read_text(encoding="utf-8"))
                owner.ensure_wsl_desktop_project(state, now_ms=123)
                path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                return {"ok": True, "dispatched": True, "visibleInDom": True}

            def close(self) -> None:
                pass

        with patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}), patch.object(
            owner.codex_desktop_model_runtime,
            "_find_codex_page",
            return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
        ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FakeClient):
            payload = owner.desktop_project_apply(
                confirm=owner.DESKTOP_PROJECT_CONFIRM,
                global_state_path=path,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual("completed", payload["status"])
        self.assertTrue(payload["after"]["registered"])

    def test_apply_reports_ipc_failure_without_mutating_project_state(self) -> None:
        path = self._state_path()

        class FailingClient:
            def __init__(self, _ws_url: str) -> None:
                raise RuntimeError("cdp unavailable")

        with patch.object(owner, "create_backup", return_value={"ok": True}), patch.object(
            owner.codex_desktop_model_runtime,
            "_find_codex_page",
            return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
        ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FailingClient):
            payload = owner.desktop_project_apply(
                confirm=owner.DESKTOP_PROJECT_CONFIRM,
                global_state_path=path,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual("desktop_project_ipc_failed", payload["reason"])
        self.assertFalse(owner.desktop_project_snapshot(path)["registered"])

    def test_validate_blocks_when_desktop_project_is_absent(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"release_ready": True, "issues": []},
            "desktop_project": {
                "ok": True,
                "registered": False,
                "projection_changed_fields": ["local-projects.wsl"],
                "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT,
            },
        }
        with patch.object(owner, "snapshot", return_value=state):
            payload = owner.validate(SimpleNamespace())

        self.assertFalse(payload["ok"])
        self.assertEqual("wsl_desktop_project_not_registered", payload["issues"][0]["code"])
        self.assertFalse(payload["acceptance"]["desktop_project_registered"])

    def test_mirror_export_projects_registered_desktop_identity_without_host_state(self) -> None:
        state = {
            "desktop_project": {
                "ok": True,
                "registered": True,
                "project_id": "project-123",
                "name": "WSL Codex 工作区",
                "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT,
                "linux_root": str(owner.DEFAULT_WORKTREE),
                "reason": "registered",
            }
        }
        args = SimpleNamespace(kind="desktop-project-registration")
        with patch.object(owner, "snapshot", return_value=state):
            payload = owner.mirror_export(args)

        self.assertTrue(payload["ok"])
        self.assertEqual("desktop_ipc", payload["registration_method"])
        self.assertEqual("project-123", payload["project_id"])
        self.assertEqual(owner.WSL_DESKTOP_PROJECT_ROOT, payload["desktop_root"])
        self.assertNotIn("global_state_path", payload)
        self.assertFalse(payload["activation_performed"])
        self.assertFalse(payload["host_runtime_imported"])

    def test_windows_mirror_export_delegates_to_declared_wsl_runtime(self) -> None:
        args = SimpleNamespace(
            distribution="Codex-Wsl-Lab",
            user="codexlab",
            worktree=r"\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace",
            bare_repo=r"C:\WSL\Codex-Wsl-Lab\git\codex-workspace.git",
            mirror_root=r"C:\Users\45543\codex-env-mirror",
            timeout=300,
        )
        delegated = {"schema": "wsl_workspace_owner.v1.mirror_export.bootstrap.v1", "ok": True}
        with patch.object(owner.shutil, "which", return_value="wsl.exe"), patch.object(
            owner,
            "_wsl_export_path",
            return_value="/home/codexlab/.local/bin:/runtime/rg:/usr/bin:/bin",
        ), patch.object(
            owner,
            "_run",
            return_value={"ok": True, "returncode": 0, "stdout": json.dumps(delegated), "stderr": ""},
        ) as run:
            payload = owner._delegate_mirror_export_to_wsl(args, "bootstrap")

        self.assertEqual(delegated, payload)
        command = run.call_args.args[0]
        self.assertEqual(["wsl.exe", "-d", "Codex-Wsl-Lab", "-u", "codexlab", "--"], command[:6])
        self.assertEqual("/usr/bin/env", command[6])
        self.assertEqual("PATH=/home/codexlab/.local/bin:/runtime/rg:/usr/bin:/bin", command[7])
        self.assertIn("/home/codexlab/work/codex-workspace/workspace/_bridge/wsl_workspace_owner.py", command)
        self.assertIn("/mnt/c/WSL/Codex-Wsl-Lab/git/codex-workspace.git", command)
        self.assertIn("/mnt/c/Users/45543/codex-env-mirror", command)
        self.assertIsNone(run.call_args.kwargs["output_limit"])

    def test_wsl_export_path_discovers_current_codex_runtime_rg_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "runtime-a"
            second = root / "runtime-b"
            ignored = root / "runtime-without-rg"
            for candidate in (first, second, ignored):
                candidate.mkdir()
            (first / "rg").touch()
            (second / "rg").touch()

            entries = owner._wsl_export_path(
                "Codex-Wsl-Lab",
                "codexlab",
                runtime_root=root,
            ).split(":")

        self.assertEqual("/home/codexlab/.local/bin", entries[0])
        self.assertIn(str(first), entries)
        self.assertIn(str(second), entries)
        self.assertNotIn(str(ignored), entries)


class HostCompatibilityProjectionTests(unittest.TestCase):
    def _roots(self) -> tuple[Path, Path]:
        base = Path(tempfile.mkdtemp())
        source = base / "work-git" / "workspace"
        target = base / "windows-projection"
        (source / "_bridge").mkdir(parents=True)
        (target / "_bridge").mkdir(parents=True)
        for relative in owner.HOST_PROJECTION_FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"source:{relative}\n", encoding="utf-8")
        return source, target

    def test_host_projection_requires_exact_confirmation(self) -> None:
        source, target = self._roots()
        with patch.object(owner, "create_backup") as backup:
            payload = owner.host_compatibility_projection_apply(
                confirm="",
                source_root=source,
                target_root=target,
            )

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["applied"])
        backup.assert_not_called()

    def test_host_projection_is_allowlisted_one_way_and_hash_verified(self) -> None:
        source, target = self._roots()
        (target / ".git").mkdir()
        existing = target / owner.HOST_PROJECTION_FILES[1]
        existing.write_text("old\n", encoding="utf-8")
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ) as backup:
            payload = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )

        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["applied"])
        self.assertEqual(backup.call_args.args[0], [str(existing)])
        for relative in owner.HOST_PROJECTION_FILES:
            self.assertEqual((target / relative).read_bytes(), (source / relative).read_bytes())
        manifest = json.loads((target / owner.HOST_PROJECTION_MANIFEST).read_text(encoding="utf-8"))
        self.assertFalse(manifest["source_authority"])
        self.assertFalse(manifest["reverse_sync_allowed"])
        self.assertFalse(
            owner.host_compatibility_projection_plan(source_root=source, target_root=target)["would_change"]
        )

    def test_host_projection_does_not_rewrite_current_manifest_for_file_only_drift(self) -> None:
        source, target = self._roots()
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ):
            first = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )
            manifest_path = target / owner.HOST_PROJECTION_MANIFEST
            manifest_before = manifest_path.read_bytes()
            drifted = target / owner.HOST_PROJECTION_FILES[0]
            drifted.write_text("drifted\n", encoding="utf-8")
            second = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(manifest_before, manifest_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
