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

    def test_cleanup_plan_delegates_generated_artifact_classification(self) -> None:
        args = SimpleNamespace(distribution="", user="", worktree="", bare_repo="", mirror_root="", receipt="", timeout=300)
        state = {
            "paths": {
                "worktree": "/tmp/work-git",
                "bare_repo": "/tmp/work-git.git",
            }
        }
        generated = {"ok": True, "candidate_count": 1}
        with patch.object(owner, "snapshot", return_value=state), patch.object(
            owner.wsl_workspace_generated_artifacts,
            "cleanup_plan",
            return_value=generated,
        ) as delegated:
            payload = owner.cleanup_plan(args)

        self.assertEqual(payload["generated_artifacts"], generated)
        self.assertTrue(payload["ok"])
        self.assertIn("cleanup-apply", payload["next_action"])
        delegated.assert_called_once_with(Path("/tmp/work-git"))

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

    def test_snapshot_includes_wsl_codex_app_server_state(self) -> None:
        with patch.object(owner, "desktop_project_snapshot", return_value={"ok": True, "registered": True}), patch.object(owner, "host_compatibility_projection_plan", return_value={"eligible": True, "would_change": False}), patch.object(owner, "wsl_state", return_value={"present": True, "interop": {"probe_ok": True}}), patch.object(owner, "interop_guard_state", return_value={"ready": True}), patch.object(owner, "git_state", return_value={"available": True}), patch.object(owner, "workspace_access_state", return_value={"ok": True}), patch.object(owner, "work_git_state", return_value={"release_ready": True, "issues": []}), patch.object(owner.developer_toolchain_owner, "snapshot", return_value={"ok": True}), patch.object(owner.wsl_codex_app_server, "status", return_value={"ok": True, "active": True, "enabled": True}), patch.object(owner.windows_execution_agent, "snapshot", return_value={"ok": True, "inventory": {"ok": True, "tasks": []}}):
            payload = owner.snapshot(SimpleNamespace(distribution="", user="", worktree="", bare_repo="", mirror_root="", receipt="", timeout=300))
        self.assertTrue(payload["codex_app_server"]["ok"])
        self.assertTrue(payload["windows_execution_agent"]["ok"])

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

    def test_interop_guard_apply_requires_exact_confirmation(self) -> None:
        with patch.object(owner.wsl_interop_guard, "plan", return_value={"ok": True}) as planned, patch.object(
            owner.wsl_interop_guard,
            "_root_run_script",
        ) as root_run:
            payload = owner.interop_guard_apply("", "Codex-Wsl-Lab", "codexlab")

        self.assertFalse(payload["ok"])
        self.assertEqual("blocked", payload["status"])
        planned.assert_called_once()
        root_run.assert_not_called()

    def test_interop_guard_root_transport_uses_target_temp_script(self) -> None:
        visible_script = Path(tempfile.mkdtemp()) / "guard.sh"
        visible_script.write_text("payload", encoding="utf-8")
        with patch.object(owner.wsl_interop_guard, "_inside_wsl", return_value=False), patch.object(
            owner.wsl_interop_guard.shutil,
            "which",
            return_value="wsl.exe",
        ), patch.object(
            owner.wsl_interop_guard,
            "_write_target_script",
            return_value=(visible_script, "/tmp/guard.sh"),
        ), patch.object(
            owner.wsl_interop_guard,
            "_run",
            return_value={"ok": True, "returncode": 0, "stdout": "", "stderr": ""},
        ) as runner:
            payload = owner.wsl_interop_guard._root_run_script(
                "destination=/etc/example\nmkdir -p \"$(dirname \"$destination\")\"",
                "Codex-Wsl-Lab",
            )

        self.assertTrue(payload["ok"])
        command = runner.call_args.args[0]
        self.assertEqual(command[:6], ["wsl.exe", "-d", "Codex-Wsl-Lab", "-u", "root", "--"])
        self.assertEqual(command[-2:], ["sh", "/tmp/guard.sh"])
        self.assertFalse(visible_script.exists())

    def test_interop_guard_missing_registration_uses_init_cmd_transport(self) -> None:
        root = Path(tempfile.mkdtemp())
        init_path = root / "init"
        cmd_path = root / "cmd.exe"
        wsl_path = root / "wsl.exe"
        for path in (init_path, cmd_path, wsl_path):
            path.write_text("", encoding="utf-8")
        with patch.object(owner.wsl_interop_guard, "_inside_wsl", return_value=True), patch.object(
            owner.wsl_interop_guard,
            "INIT_PATH",
            init_path,
        ), patch.object(owner.wsl_interop_guard, "CMD_EXE", cmd_path), patch.object(
            owner.wsl_interop_guard,
            "WSL_EXE",
            wsl_path,
        ), patch.object(
            owner.wsl_interop_guard,
            "INTEROP_ENTRY",
            root / "missing-WSLInterop",
        ):
            command = owner.wsl_interop_guard._wsl_command(
                ["-d", "Codex-Wsl-Lab", "-u", "root", "--", "sh", "-lc", "id -u"],
                tolerate_missing_interop=True,
            )

        self.assertEqual(command[:5], [str(init_path), str(cmd_path), "/d", "/s", "/c"])
        self.assertIn(r"C:\Windows\System32\wsl.exe", command[-1])
        self.assertIn("Codex-Wsl-Lab", command[-1])

    def test_interop_guard_apply_backs_up_and_reads_back(self) -> None:
        before = {
            "ok": True,
            "state": {
                "files": [{"path": "/etc/systemd/system/codex-wsl-interop-guard.service", "exists": True}],
            },
        }
        after = {"ready": True, "files_current": True, "timer_enabled": True, "timer_active": True}
        with patch.object(owner.wsl_interop_guard, "plan", return_value=before), patch.object(
            owner.wsl_interop_guard,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["manifest.json"]},
        ) as backup, patch.object(
            owner.wsl_interop_guard,
            "_root_run_script",
            return_value={"ok": True, "returncode": 0, "stderr": ""},
        ) as root_run, patch.object(owner.wsl_interop_guard, "state", return_value=after):
            payload = owner.interop_guard_apply(
                owner.INTEROP_GUARD_CONFIRM,
                "Codex-Wsl-Lab",
                "codexlab",
            )

        self.assertTrue(payload["ok"])
        backup.assert_called_once()
        self.assertIn("systemctl enable --now codex-wsl-interop-guard.timer", root_run.call_args.args[0])

    def test_interop_guard_state_requires_current_files_and_active_timer(self) -> None:
        expected = owner.wsl_interop_guard.MANAGED_CONTENTS

        def read_file(path: Path, _distribution: str, _user: str) -> str:
            key = next(key for key, value in owner.wsl_interop_guard.MANAGED_PATHS.items() if value == path)
            return expected[key]

        timer = {
            "ok": True,
            "stdout": "LoadState=loaded\nActiveState=active\nSubState=waiting\nUnitFileState=enabled",
            "stderr": "",
        }
        service = {
            "ok": True,
            "stdout": "LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=static\nResult=success",
            "stderr": "",
        }
        with patch.object(owner.wsl_interop_guard, "_target_file_text", side_effect=read_file), patch.object(
            owner.wsl_interop_guard,
            "_target_run",
            side_effect=[timer, service],
        ), patch.object(owner.wsl_interop_guard, "_wsl_command", return_value=["wsl.exe"]):
            payload = owner.interop_guard_state("Codex-Wsl-Lab", "codexlab")

        self.assertTrue(payload["ready"])
        self.assertTrue(payload["files_current"])
        self.assertTrue(payload["timer_enabled"])
        self.assertTrue(payload["timer_active"])


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

        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True}), patch.object(
            owner,
            "desktop_project_classifier_state",
            return_value={"recognized": True, "worker_available": True},
        ):
            payload = owner.desktop_project_snapshot(path)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["registered"])
        self.assertTrue(payload["ready"])
        self.assertFalse(payload["projection_required"])

    def test_snapshot_requires_live_desktop_classifier_acceptance(self) -> None:
        state: dict = {}
        owner.ensure_wsl_desktop_project(state, now_ms=123)
        path = self._state_path(state)

        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True}), patch.object(
            owner,
            "desktop_project_classifier_state",
            return_value={"recognized": False, "worker_available": False, "reason": "desktop_git_unavailable"},
        ):
            payload = owner.desktop_project_snapshot(path)

        self.assertTrue(payload["registered"])
        self.assertTrue(payload["git_recognized"])
        self.assertFalse(payload["desktop_classifier_recognized"])
        self.assertFalse(payload["ready"])
        self.assertEqual("desktop_classifier_rejected_git", payload["reason"])

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

            def evaluate(self, expression: str) -> dict:
                FakeClient.expression = expression
                return {"ok": True, "dispatched": True, "visibleInDom": True}

            def close(self) -> None:
                pass

        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True}), patch.object(
            owner,
            "ensure_desktop_project_git_state",
            return_value={"ok": True, "changed": False},
        ), patch.object(
            owner,
            "desktop_project_classifier_state",
            return_value={"recognized": True, "worker_available": True},
        ), patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}), patch.object(
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
        self.assertTrue(payload["persistence"]["changed"])
        self.assertIn("project-order.wsl", payload["persistence"]["changed_fields"])
        self.assertIn("selected-project.wsl", payload["persistence"]["changed_fields"])
        self.assertIn("active-workspace-roots.wsl", payload["persistence"]["changed_fields"])
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["after"]["project_id"], persisted["project-order"][0])
        self.assertEqual(
            {"type": "local", "projectId": payload["after"]["project_id"]},
            persisted["selected-project"],
        )
        self.assertEqual([owner.WSL_DESKTOP_PROJECT_ROOT], persisted["active-workspace-roots"])
        self.assertIn("electron-set-active-workspace-root", FakeClient.expression)

    def test_apply_reports_ipc_failure_without_mutating_project_state(self) -> None:
        path = self._state_path()

        class FailingClient:
            def __init__(self, _ws_url: str) -> None:
                raise RuntimeError("cdp unavailable")

        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True}), patch.object(
            owner,
            "ensure_desktop_project_git_state",
            return_value={"ok": True, "changed": False},
        ), patch.object(owner, "create_backup", return_value={"ok": True}), patch.object(
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
        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True}):
            self.assertFalse(owner.desktop_project_snapshot(path)["registered"])

    def test_git_state_requires_exact_safe_directory_entry(self) -> None:
        expected = owner._desktop_project_safe_directory()
        config = "\n".join(["*", expected + "/child", "%(prefix)/" + expected.lstrip("/")])
        with patch.object(owner, "windows_git_path_state", return_value={"ok": True, "path_discoverable": True}), patch.object(
            owner,
            "_windows_git_executable",
            return_value=Path("/fake/git.exe"),
        ), patch.object(
            owner,
            "_run",
            side_effect=[
                {"ok": True, "stdout": config, "stderr": "", "returncode": 0},
                {
                    "ok": True,
                    "stdout": expected + "\n" + expected + "/.git",
                    "stderr": "",
                    "returncode": 0,
                },
            ],
        ):
            payload = owner.desktop_project_git_state()

        self.assertTrue(payload["recognized"])
        self.assertFalse(payload["safe_directory_configured"])

    def test_windows_git_path_state_reports_exact_user_entry_and_malformed_machine_pair(self) -> None:
        snapshot = {
            "ok": True,
            "userPath": r"C:\Users\45543\.local\bin;C:\Program Files\Git\cmd",
            "machinePath": r"C:\Windows\System32;C;\Program Files\Git\cmd",
            "resolvedGit": r"C:\Program Files\Git\cmd\git.exe",
            "gitExists": True,
        }
        with patch.object(owner, "_windows_path_snapshot", return_value=snapshot):
            payload = owner.windows_git_path_state()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["user_entry_present"])
        self.assertTrue(payload["path_discoverable"])
        self.assertEqual([r"C;\Program Files\Git\cmd"], payload["malformed_machine_entries"])

    def test_windows_path_entry_append_is_exact_and_idempotent(self) -> None:
        original = r"C:\Tools;C:\Users\45543\.local\bin"
        updated, changed = owner._ensure_windows_path_entry(original, owner.WINDOWS_GIT_CMD_DIR)
        repeated, repeated_changed = owner._ensure_windows_path_entry(updated, owner.WINDOWS_GIT_CMD_DIR + "\\")

        self.assertTrue(changed)
        self.assertFalse(repeated_changed)
        self.assertEqual(updated, repeated)
        self.assertEqual(1, sum(
            owner._canonical_windows_path_entry(item) == owner._canonical_windows_path_entry(owner.WINDOWS_GIT_CMD_DIR)
            for item in updated.split(";")
        ))

    def test_ensure_windows_git_path_adds_only_user_entry_and_preserves_machine_path(self) -> None:
        before_raw = {
            "ok": True,
            "userPath": r"C:\Tools",
            "machinePath": r"C:\Windows\System32;C;\Program Files\Git\cmd",
            "resolvedGit": "",
            "gitExists": True,
        }
        after_raw = {
            **before_raw,
            "userPath": r"C:\Tools;C:\Program Files\Git\cmd",
            "resolvedGit": r"C:\Program Files\Git\cmd\git.exe",
        }
        with patch.object(owner, "_windows_path_snapshot", side_effect=[before_raw, after_raw]), patch.object(
            owner,
            "_backup_windows_environment_path",
            return_value={"ok": True, "manifest_paths": ["manifest.json"]},
        ) as backup, patch.object(owner, "_set_windows_user_path", return_value={"ok": True}) as setter:
            payload = owner.ensure_windows_git_path()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changed"])
        setter.assert_called_once_with(r"C:\Tools;C:\Program Files\Git\cmd")
        backup.assert_called_once_with(before_raw)
        self.assertEqual(r"C:\Windows\System32;C;\Program Files\Git\cmd", after_raw["machinePath"])

    def test_desktop_classifier_consumes_git_worker_metadata(self) -> None:
        class FakeClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, expression: str) -> dict:
                FakeClient.expression = expression
                return {
                    "ok": True,
                    "operationSource": "local_conversation_git_actions",
                    "cwd": owner.WSL_DESKTOP_PROJECT_ROOT,
                    "availabilityBefore": {"type": "ok", "value": {"available": False}},
                    "metadata": {
                        "type": "ok",
                        "value": {"root": owner.WSL_WORKSPACE_ROOT, "commonDir": f"{owner.WSL_WORKSPACE_ROOT}/.git"},
                    },
                    "availabilityAfter": {"type": "ok", "value": {"available": True}},
                }

            def close(self) -> None:
                pass

        with patch.object(
            owner.codex_desktop_model_runtime,
            "_find_codex_page",
            return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
        ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FakeClient):
            payload = owner.desktop_project_classifier_state()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["worker_available"])
        self.assertFalse(payload["worker_available_before"])
        self.assertTrue(payload["recognized"])
        self.assertEqual(owner.WSL_WORKSPACE_ROOT, payload["root"])
        self.assertTrue(payload["metadata_root_matches"])
        self.assertEqual("local_conversation_git_actions", payload["probe"]["operation_source"])
        self.assertEqual(owner.WSL_DESKTOP_PROJECT_ROOT, payload["probe"]["cwd"])
        self.assertIn("availabilityBefore", FakeClient.expression)
        self.assertIn("setTimeout(resolve, 250)", FakeClient.expression)
        self.assertLess(FakeClient.expression.index("availabilityBefore"), FakeClient.expression.index("stable-metadata"))
        self.assertLess(FakeClient.expression.index("stable-metadata"), FakeClient.expression.index("availabilityAfter"))

    def test_desktop_classifier_accepts_metadata_when_availability_is_stale(self) -> None:
        class FakeClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, _expression: str) -> dict:
                return {
                    "ok": True,
                    "operationSource": "local_conversation_git_actions",
                    "cwd": owner.WSL_DESKTOP_PROJECT_ROOT,
                    "availabilityBefore": {"type": "ok", "value": {"available": False}},
                    "metadata": {
                        "type": "ok",
                        "value": {"root": owner.WSL_WORKSPACE_ROOT, "commonDir": f"{owner.WSL_WORKSPACE_ROOT}/.git"},
                    },
                    "availabilityAfter": {"type": "ok", "value": {"available": False}},
                }

            def close(self) -> None:
                pass

        with patch.object(
            owner.codex_desktop_model_runtime,
            "_find_codex_page",
            return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
        ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FakeClient):
            payload = owner.desktop_project_classifier_state()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["recognized"])
        self.assertTrue(payload["metadata_ready"])
        self.assertFalse(payload["worker_available"])
        self.assertEqual("metadata_ready_worker_availability_advisory", payload["reason"])

    def test_desktop_classifier_preserves_bounded_worker_failure_and_launch_receipt(self) -> None:
        class FakeClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, _expression: str) -> dict:
                return {
                    "ok": True,
                    "availabilityBefore": {"type": "ok", "value": {"available": False}},
                    "metadata": {"type": "error", "error": "x" * 5000},
                    "availabilityAfter": {"type": "ok", "value": {"available": False}},
                }

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as raw:
            receipt = Path(raw) / "launch-environment.json"
            receipt.write_bytes(b"\xef\xbb\xbf" + json.dumps({
                "schema": "codex.desktop.launch_environment.v1",
                "ready": True,
                "source": "validated_git_plus_machine_plus_user_registry",
                "generated_at": "2026-07-24T14:00:00+00:00",
                "launcher_pid": 12,
                "desktop_pid": 34,
                "entry_count": 20,
                "rejected_ambiguous_entry_count": 2,
                "preferred_git_directory": r"C:\Program Files\Git\cmd",
                "git_path_present": True,
                "resolved_git": r"C:\Program Files\Git\cmd\git.exe",
                "path_input_signature": "a" * 64,
            }).encode("utf-8"))
            with patch.object(
                owner.codex_desktop_model_runtime,
                "_find_codex_page",
                return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
            ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FakeClient):
                payload = owner.desktop_project_classifier_state(receipt)

        self.assertEqual("desktop_git_worker_unavailable_after_ready_launch", payload["reason"])
        self.assertFalse(payload["worker_responses"]["availability_before"]["available"])
        self.assertFalse(payload["worker_responses"]["availability_after"]["available"])
        metadata = payload["worker_responses"]["metadata"]
        self.assertEqual("error", metadata["type"])
        self.assertEqual("worker_error", metadata["failure_class"])
        self.assertEqual(1200, len(metadata["error"]))
        self.assertTrue(metadata["error_truncated"])
        self.assertTrue(payload["launch_environment"]["ready"])
        self.assertEqual(2, payload["launch_environment"]["rejected_ambiguous_entry_count"])
        self.assertEqual(str(receipt), payload["launch_environment"]["evidence_ref"])
        self.assertNotIn("path", payload["launch_environment"])

    def test_desktop_classifier_names_missing_git_in_launch_environment(self) -> None:
        class FakeClient:
            def __init__(self, _ws_url: str) -> None:
                pass

            def evaluate(self, _expression: str) -> dict:
                return {
                    "ok": True,
                    "availabilityBefore": {"type": "ok", "value": {"available": False}},
                    "metadata": {"type": "ok", "value": None},
                    "availabilityAfter": {"type": "ok", "value": {"available": False}},
                }

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as raw:
            receipt = Path(raw) / "launch-environment.json"
            receipt.write_text(json.dumps({
                "schema": "codex.desktop.launch_environment.v1",
                "ready": False,
                "git_path_present": False,
            }), encoding="utf-8")
            with patch.object(
                owner.codex_desktop_model_runtime,
                "_find_codex_page",
                return_value=(9231, "ws://127.0.0.1:9231/devtools/page/codex", [{}], ""),
            ), patch.object(owner.codex_desktop_model_runtime, "_CdpClient", FakeClient):
                payload = owner.desktop_project_classifier_state(receipt)

        self.assertEqual("desktop_launch_environment_git_missing", payload["reason"])
        self.assertFalse(payload["launch_environment"]["git_path_present"])

    def test_ensure_git_state_adds_exact_safe_directory_once(self) -> None:
        expected = owner._desktop_project_safe_directory()
        before = {"recognized": False, "safe_directory_configured": False}
        after = {"recognized": True, "safe_directory_configured": True, "path_discoverable": True}
        with patch.object(owner, "desktop_project_git_state", side_effect=[before, after]), patch.object(
            owner,
            "_windows_git_executable",
            return_value=Path("/fake/git.exe"),
        ), patch.object(
            owner,
            "ensure_windows_git_path",
            return_value={"ok": True, "changed": False},
        ), patch.object(owner, "_run", return_value={"ok": True, "returncode": 0, "stderr": ""}) as run:
            payload = owner.ensure_desktop_project_git_state()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changed"])
        run.assert_called_once_with([
            "/fake/git.exe",
            "config",
            "--global",
            "--add",
            "safe.directory",
            expected,
        ])

    def test_apply_repairs_git_without_repeating_desktop_ipc(self) -> None:
        state: dict = {}
        owner.ensure_wsl_desktop_project(state, now_ms=123)
        path = self._state_path(state)
        git_states = [
            {"recognized": False},
            {"recognized": True},
        ]
        with patch.object(owner, "desktop_project_git_state", side_effect=git_states), patch.object(
            owner,
            "ensure_desktop_project_git_state",
            return_value={"ok": True, "changed": True},
        ), patch.object(
            owner,
            "desktop_project_classifier_state",
            return_value={"recognized": True, "worker_available": True},
        ), patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}) as backup, patch.object(
            owner.codex_desktop_model_runtime,
            "_find_codex_page",
        ) as find_page:
            payload = owner.desktop_project_apply(
                confirm=owner.DESKTOP_PROJECT_CONFIRM,
                global_state_path=path,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual("desktop_project_git_repaired", payload["reason"])
        self.assertFalse(payload["registration_applied"])
        find_page.assert_not_called()
        backup_paths = backup.call_args.args[0]
        self.assertEqual([str(path), str(owner.DEFAULT_WINDOWS_GIT_CONFIG)], backup_paths)

    def test_apply_defers_until_desktop_worker_restarts_after_path_repair(self) -> None:
        state: dict = {}
        owner.ensure_wsl_desktop_project(state, now_ms=123)
        path = self._state_path(state)
        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True, "path_discoverable": True}), patch.object(
            owner,
            "desktop_project_classifier_state",
            return_value={"recognized": False, "worker_available": False},
        ), patch.object(
            owner,
            "ensure_desktop_project_git_state",
            return_value={"ok": True, "changed": True, "restart_required": True},
        ), patch.object(
            owner.codex_desktop_model_runtime,
            "request_desktop_page_reload",
            return_value={"ok": True, "requested": True, "skipped": False},
        ), patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}):
            payload = owner.desktop_project_apply(
                confirm=owner.DESKTOP_PROJECT_CONFIRM,
                global_state_path=path,
                refresh_wait_seconds=0,
            )

        self.assertFalse(payload["ok"])
        self.assertEqual("deferred", payload["status"])
        self.assertTrue(payload["restart_required"])
        self.assertTrue(payload["page_refresh"]["requested"])
        self.assertEqual("desktop_restart_required_for_git_worker", payload["reason"])

    def test_apply_uses_page_refresh_before_escalating_to_full_restart(self) -> None:
        state: dict = {}
        owner.ensure_wsl_desktop_project(state, now_ms=123)
        path = self._state_path(state)
        classifier_states = [
            {"recognized": False, "worker_available": False},
            {"recognized": False, "worker_available": False},
            {"recognized": True, "worker_available": True},
        ]
        with patch.object(owner, "desktop_project_git_state", return_value={"recognized": True, "path_discoverable": True}), patch.object(
            owner,
            "desktop_project_classifier_state",
            side_effect=classifier_states,
        ), patch.object(
            owner,
            "ensure_desktop_project_git_state",
            return_value={"ok": True, "changed": False, "restart_required": False},
        ), patch.object(
            owner.codex_desktop_model_runtime,
            "request_desktop_page_reload",
            return_value={"ok": True, "requested": True, "skipped": False, "cdp_port": 9231},
        ) as refresh, patch.object(owner, "create_backup", return_value={"ok": True, "manifest_paths": ["manifest.json"]}):
            payload = owner.desktop_project_apply(
                confirm=owner.DESKTOP_PROJECT_CONFIRM,
                global_state_path=path,
                refresh_wait_seconds=0,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual("completed", payload["status"])
        self.assertEqual("desktop_project_refreshed", payload["reason"])
        self.assertFalse(payload["restart_required"])
        refresh.assert_called_once_with(wait_seconds=0.0)

    def test_validate_reports_registered_project_without_windows_git_metadata(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": True},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"issues": []},
            "developer_toolchain": {"ok": True},
            "desktop_project": {
                "ok": True,
                "registered": True,
                "git_recognized": False,
                "git": {"reason": "unsafe_repository_owner"},
                "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT,
            },
            "host_projection": {"eligible": True, "would_change": False},
        }
        with patch.object(owner, "snapshot", return_value=state), patch.object(
            owner.windows_execution_agent,
            "validate",
            return_value={"ok": True, "issues": []},
        ):
            payload = owner.validate(SimpleNamespace())

        issue = next(item for item in payload["issues"] if item["code"] == "wsl_desktop_project_git_unavailable")
        self.assertEqual("unsafe_repository_owner", issue["detail"]["reason"])
        self.assertFalse(payload["acceptance"]["desktop_project_git_recognized"])

    def test_validate_blocks_when_desktop_classifier_rejects_git(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": True},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"issues": []},
            "developer_toolchain": {"ok": True},
            "desktop_project": {
                "ok": True,
                "registered": True,
                "git_recognized": True,
                "desktop_classifier_recognized": False,
                "desktop_classifier": {"reason": "desktop_git_unavailable"},
                "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT,
            },
            "host_projection": {"eligible": True, "would_change": False},
        }
        with patch.object(owner, "snapshot", return_value=state), patch.object(
            owner.windows_execution_agent,
            "validate",
            return_value={"ok": True, "issues": []},
        ):
            payload = owner.validate(SimpleNamespace())

        issue = next(item for item in payload["issues"] if item["code"] == "wsl_desktop_project_classifier_rejected_git")
        self.assertEqual("desktop_git_unavailable", issue["detail"]["reason"])
        self.assertFalse(payload["acceptance"]["desktop_project_classifier_recognized"])

    def test_validate_blocks_when_desktop_project_is_absent(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": True, "files_current": True, "timer_enabled": True, "timer_active": True},
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

    def test_validate_blocks_when_interop_guard_is_not_continuous(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": False, "files_current": True, "timer_enabled": False, "timer_active": False},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"release_ready": True, "issues": []},
            "desktop_project": {
                "ok": True,
                "registered": True,
                "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT,
            },
        }
        with patch.object(owner, "snapshot", return_value=state):
            payload = owner.validate(SimpleNamespace())

        self.assertFalse(payload["ok"])
        self.assertEqual("wsl_interop_guard_not_ready", payload["issues"][0]["code"])
        self.assertFalse(payload["acceptance"]["interop_guard_ready"])

    def test_validate_blocks_when_host_provider_runtime_projection_is_stale(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": True, "files_current": True, "timer_enabled": True, "timer_active": True},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"issues": []},
            "developer_toolchain": {"ok": True},
            "desktop_project": {"ok": True, "registered": True, "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT},
            "host_projection": {
                "eligible": True,
                "would_change": True,
                "files": [{"relative_path": "_bridge/codex_model_provider_watcher.py", "current": False}],
            },
        }
        with patch.object(owner, "snapshot", return_value=state):
            payload = owner.validate(SimpleNamespace())

        self.assertFalse(payload["ok"])
        stale = next(item for item in payload["issues"] if item["code"] == "host_compatibility_projection_stale")
        self.assertEqual(["_bridge/codex_model_provider_watcher.py"], stale["detail"])

    def test_validate_blocks_when_windows_execution_agent_contract_drifts(self) -> None:
        state = {
            "wsl": {"present": True, "interop": {"probe_ok": True}},
            "interop_guard": {"ready": True},
            "codex_app_server": {"ok": True},
            "windows_execution_agent": {"ok": True, "inventory": {"ok": True, "tasks": []}},
            "git": {"available": True},
            "workspace_access": {"ok": True},
            "work_git": {"issues": []},
            "developer_toolchain": {"ok": True},
            "desktop_project": {"ok": True, "registered": True, "desktop_root": owner.WSL_DESKTOP_PROJECT_ROOT},
            "host_projection": {"eligible": True, "would_change": False},
        }
        agent_validation = {"ok": False, "issues": [{"code": "windows_task_run_level_drift"}]}
        with patch.object(owner, "snapshot", return_value=state), patch.object(
            owner.windows_execution_agent,
            "validate",
            return_value=agent_validation,
        ):
            payload = owner.validate(SimpleNamespace())

        self.assertFalse(payload["ok"])
        issue = next(item for item in payload["issues"] if item["code"] == "windows_execution_agent_not_ready")
        self.assertEqual(agent_validation["issues"], issue["detail"])

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

    def test_mirror_export_projects_codex_app_server_status_and_unit(self) -> None:
        args = SimpleNamespace(kind="codex-app-server-status")
        with patch.object(owner.wsl_codex_app_server, "status", return_value={"ok": True, "socket_exists": True}):
            status = owner.mirror_export(args)
        args.kind = "codex-app-server-unit"
        with patch.object(owner.wsl_codex_app_server, "plan", return_value={"ok": True, "unit_sha256": "abc"}):
            unit = owner.mirror_export(args)

        self.assertTrue(status["ok"])
        self.assertEqual("codex_app_server_status", status["export_kind"])
        self.assertTrue(unit["ok"])
        self.assertEqual("abc", unit["unit"]["unit_sha256"])

    def test_mirror_export_projects_local_mcp_hub_status_and_unit(self) -> None:
        args = SimpleNamespace(kind="local-mcp-hub-service-status")
        with patch.object(
            owner.local_mcp_hub_process,
            "hub_service_status",
            return_value={"ok": True, "active": True},
        ):
            status = owner.mirror_export(args)
        args.kind = "local-mcp-hub-user-unit"
        with patch.object(
            owner.local_mcp_hub_process,
            "hub_service_plan",
            return_value={"ok": True, "unit_sha256": "hub-abc"},
        ):
            unit = owner.mirror_export(args)

        self.assertTrue(status["ok"])
        self.assertEqual("local_mcp_hub_service_status", status["export_kind"])
        self.assertTrue(status["service_status"]["active"])
        self.assertTrue(unit["ok"])
        self.assertEqual("local_mcp_hub_user_unit", unit["export_kind"])
        self.assertEqual("hub-abc", unit["unit"]["unit_sha256"])

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
        script_source = source.parent / "codex-home" / "scripts"
        script_source.mkdir(parents=True)
        for relative in owner.DESKTOP_SCRIPT_PROJECTION_FILES:
            (script_source / relative).write_text(f"source:scripts/{relative}\n", encoding="utf-8")
        (source / owner.HOST_STARTUP_BASELINE).write_text(
            json.dumps({
                "global_config": "/home/codexlab/.codex-app/config.toml",
                "project_config_required": False,
                "project_config": "",
                "project_required_values": {},
            }),
            encoding="utf-8",
        )
        (target / owner.HOST_STARTUP_BASELINE).write_text(
            json.dumps({
                "global_config": r"C:\Users\45543\.codex\config.toml",
                "project_config_required": True,
                "project_config": r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\.codex\config.toml",
                "project_required_values": {"sandbox_mode": "danger-full-access"},
                "expected_mcp": {"node_repl": {"required": True}},
            }),
            encoding="utf-8",
        )
        return source, target

    def test_host_projection_includes_hub_process_owner(self) -> None:
        self.assertIn("_bridge/local_mcp_hub_catalog.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/windows_execution_agent.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/shared/codex_scheduler_runner.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/local_mcp_hub_specs.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/local_mcp_hub_graph_tools.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/managed_python_dependency_runtime.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/local_mcp_hub_resource_search.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/resource_source_strategy.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/resource_python_package_installer.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/local_mcp_hub_process.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/github_hub_client.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/rule_governance.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/codex_appserver_model_bridge.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/codex_desktop_protocol_compatibility.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/codex_desktop_model_runtime.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/worker_loop_observability.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/mobile_dashboard.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/openclaw_accounts.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/start_openclaw_gateway_hidden.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/run-openclaw-gateway-loop.ps1", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/retire-openclaw-legacy-runtime.ps1", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/_ctxsend.mjs", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/mobile_openclaw_bridge/_diag_test.mjs", owner.HOST_PROJECTION_FILES)
        self.assertIn("_bridge/network_doctor.py", owner.HOST_PROJECTION_FILES)
        self.assertIn("start-codex-desktop-elevated.ps1", owner.DESKTOP_SCRIPT_PROJECTION_FILES)
        self.assertIn("restart-codex-desktop-cdp.ps1", owner.DESKTOP_SCRIPT_PROJECTION_FILES)

    def test_host_projection_includes_atomic_provider_runtime_bundle(self) -> None:
        expected = {
            "_bridge/codex_model_provider_watcher.py",
            "_bridge/codex_state_repair.py",
            "_bridge/codex_baseline_update.py",
            "_bridge/codex_wsl_resume_context.py",
            "_bridge/codex_config_guard.py",
            "_bridge/codex_config_projection.py",
        }
        self.assertEqual(expected, set(owner.CODEX_PROVIDER_RUNTIME_PROJECTION_FILES))
        self.assertTrue(expected.issubset(owner.HOST_PROJECTION_FILES))

    def test_host_projection_includes_atomic_windows_maintenance_runtime_bundle(self) -> None:
        expected = {
            "_bridge/resource_library_catalog.py",
            "_bridge/defender_governance.py",
            "_bridge/backup_hygiene_doctor.py",
            "_bridge/shared/codex_reporter.py",
            "_bridge/shared/record_store_maintenance.py",
            "_bridge/shared/resource_event_store.py",
            "_bridge/shared/system_maintenance_cli.py",
            "_bridge/shared/performance_maintenance_job.py",
            "_bridge/shared/email_scheduler.py",
        }
        self.assertEqual(expected, set(owner.WINDOWS_MAINTENANCE_RUNTIME_PROJECTION_FILES))
        self.assertTrue(expected.issubset(owner.HOST_PROJECTION_FILES))

    def test_host_projection_includes_atomic_windows_scheduler_runtime_bundle(self) -> None:
        expected = {
            "_bridge/shared/codex_scheduler_runner.py",
            "_bridge/shared/install-codex-scheduler-task.ps1",
        }
        self.assertEqual(expected, set(owner.WINDOWS_SCHEDULER_RUNTIME_PROJECTION_FILES))
        self.assertTrue(expected.issubset(owner.HOST_PROJECTION_FILES))

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
        self.assertEqual(
            backup.call_args.args[0],
            [str(existing), str(target / owner.HOST_STARTUP_BASELINE)],
        )
        for relative in owner.HOST_PROJECTION_FILES:
            self.assertEqual((target / relative).read_bytes(), (source / relative).read_bytes())
        script_source = source.parent / "codex-home" / "scripts"
        script_target = target.parent / ".codex" / "scripts"
        for relative in owner.DESKTOP_SCRIPT_PROJECTION_FILES:
            self.assertEqual((script_target / relative).read_bytes(), (script_source / relative).read_bytes())
        manifest = json.loads((target / owner.HOST_PROJECTION_MANIFEST).read_text(encoding="utf-8"))
        self.assertFalse(manifest["source_authority"])
        self.assertFalse(manifest["reverse_sync_allowed"])
        self.assertEqual(manifest["desktop_script_target_root"], str(script_target.resolve()))
        self.assertFalse(
            owner.host_compatibility_projection_plan(source_root=source, target_root=target)["would_change"]
        )

    def test_directed_host_projection_updates_only_selected_allowlisted_file(self) -> None:
        source, target = self._roots()
        selected = owner.HOST_PROJECTION_FILES[0]
        untouched = owner.HOST_PROJECTION_FILES[1]
        selected_target = target / selected
        untouched_target = target / untouched
        selected_target.write_text("old-selected\n", encoding="utf-8")
        untouched_target.write_text("old-untouched\n", encoding="utf-8")
        manifest_path = target / owner.HOST_PROJECTION_MANIFEST
        manifest_path.write_text('{"existing": true}\n', encoding="utf-8")
        manifest_before = manifest_path.read_bytes()
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ) as backup:
            payload = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
                include=(selected,),
            )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual("directed", payload["selection_mode"])
        self.assertFalse(payload["full_projection_current"])
        self.assertEqual((source / selected).read_bytes(), selected_target.read_bytes())
        self.assertEqual(b"old-untouched\n", untouched_target.read_bytes())
        self.assertEqual(manifest_before, manifest_path.read_bytes())
        self.assertEqual([str(selected_target)], backup.call_args.args[0])
        self.assertIn(f"host_compatibility:{untouched}", payload["remaining_drift"])

    def test_directed_host_projection_rejects_non_allowlisted_selector(self) -> None:
        source, target = self._roots()
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
        ) as backup:
            payload = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
                include=("_bridge/not-allowlisted.py",),
            )

        self.assertFalse(payload["ok"])
        self.assertEqual("projection_not_eligible", payload["reason"])
        self.assertIn(
            "projection_include_not_allowlisted",
            {item["code"] for item in payload["plan"]["blockers"]},
        )
        backup.assert_not_called()

    def test_host_projection_migrates_only_retired_project_baseline_fields(self) -> None:
        source, target = self._roots()
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ):
            payload = owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )

        self.assertTrue(payload["ok"], payload)
        baseline = json.loads((target / owner.HOST_STARTUP_BASELINE).read_text(encoding="utf-8"))
        self.assertEqual(baseline["global_config"], "C:\\Users\\45543\\.codex\\config.toml")
        self.assertFalse(baseline["project_config_required"])
        self.assertEqual(baseline["project_config"], "")
        self.assertEqual(baseline["project_required_values"], {})
        self.assertEqual(baseline["expected_mcp"], {"node_repl": {"required": True}})
        self.assertNotIn("/home/codexlab", json.dumps(baseline))
        plan = owner.host_compatibility_projection_plan(source_root=source, target_root=target)
        self.assertFalse(plan["would_change"], plan)
        self.assertIn(
            f"host_startup_baseline:{owner.HOST_STARTUP_BASELINE}",
            plan["apply_contract"]["fixed_allowlist"],
        )

    def test_host_projection_refuses_invalid_or_project_requiring_source_baseline(self) -> None:
        source, target = self._roots()
        source_baseline = source / owner.HOST_STARTUP_BASELINE
        source_baseline.write_text("{invalid", encoding="utf-8")
        invalid = owner.host_compatibility_projection_plan(source_root=source, target_root=target)
        self.assertFalse(invalid["eligible"])
        self.assertIn("projection_source_startup_baseline_invalid", {item["code"] for item in invalid["blockers"]})

        source_baseline.write_text(json.dumps({"project_config_required": True}), encoding="utf-8")
        requiring = owner.host_compatibility_projection_plan(source_root=source, target_root=target)
        self.assertFalse(requiring["eligible"])
        self.assertIn(
            "projection_source_startup_baseline_requires_project_config",
            {item["code"] for item in requiring["blockers"]},
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

    def test_host_cleanup_plan_is_fixed_classification_and_protects_bridge(self) -> None:
        source, target = self._roots()
        (target / ".cache").mkdir()
        (target / ".cache" / "generated.bin").write_bytes(b"cache")
        (target / "_bridge" / "required.py").write_text("keep\n", encoding="utf-8")
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ):
            owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )
            plan = owner.host_compatibility_cleanup_plan(source_root=source, target_root=target)

        self.assertTrue(plan["ok"], plan)
        cache = next(row for row in plan["candidates"] if row["relative_path"] == ".cache")
        self.assertTrue(cache["eligible"])
        self.assertIn("_bridge", plan["protected_roots"])

    def test_host_cleanup_requires_confirmation_and_deletes_only_fixed_candidates(self) -> None:
        source, target = self._roots()
        (target / ".cache").mkdir()
        (target / ".cache" / "generated.bin").write_bytes(b"cache")
        protected = target / "_bridge" / "required.py"
        protected.write_text("keep\n", encoding="utf-8")
        with patch.object(owner, "_run", return_value={"ok": False, "stdout": ""}), patch.object(
            owner,
            "create_backup",
            return_value={"ok": True, "manifest_paths": ["backup.json"]},
        ):
            owner.host_compatibility_projection_apply(
                confirm=owner.HOST_PROJECTION_CONFIRM,
                source_root=source,
                target_root=target,
            )
            refused = owner.host_compatibility_cleanup_apply(
                confirm="",
                source_root=source,
                target_root=target,
            )
            self.assertTrue((target / ".cache").exists())
            applied = owner.host_compatibility_cleanup_apply(
                confirm=owner.HOST_CLEANUP_CONFIRM,
                source_root=source,
                target_root=target,
            )

        self.assertFalse(refused["ok"])
        self.assertTrue(applied["ok"], applied)
        self.assertFalse((target / ".cache").exists())
        self.assertTrue(protected.is_file())

    def test_host_audio_migration_moves_results_and_prunes_only_regenerable_items(self) -> None:
        source, target = self._roots()
        assets = source.parent / "audio-assets"
        gui_result = target / ".tools" / "audio-work" / "gui-output" / "song" / "song.lrc"
        gui_result.parent.mkdir(parents=True)
        gui_result.write_text("[00:00.00]song\n", encoding="utf-8")
        transcript = target / ".tools" / "whisper-output" / "result.txt"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("result\n", encoding="utf-8")
        intermediate = target / ".tools" / "audio-work" / "asr-cache" / "cache.wav"
        intermediate.parent.mkdir(parents=True)
        intermediate.write_bytes(b"wav")
        stale_download = target / ".tools" / "models" / "model.bin.downloading"
        stale_download.parent.mkdir(parents=True)
        stale_download.write_bytes(b"partial")

        plan = owner.host_audio_asset_migration_plan(target_root=target, asset_root=assets)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["migrate_count"], 2)
        self.assertEqual(plan["prune_count"], 2)

        refused = owner.host_audio_asset_migration_apply(confirm="", target_root=target, asset_root=assets)
        self.assertFalse(refused["ok"])
        self.assertTrue(gui_result.exists())

        applied = owner.host_audio_asset_migration_apply(
            confirm=owner.HOST_AUDIO_MIGRATION_CONFIRM,
            target_root=target,
            asset_root=assets,
        )
        self.assertTrue(applied["ok"], applied)
        self.assertFalse(gui_result.exists())
        self.assertFalse(transcript.exists())
        self.assertFalse(intermediate.exists())
        self.assertFalse(stale_download.exists())
        self.assertEqual(
            (assets / "migrated-legacy-host" / ".tools" / "audio-work" / "gui-output" / "song" / "song.lrc").read_text(encoding="utf-8"),
            "[00:00.00]song\n",
        )


if __name__ == "__main__":
    unittest.main()
