from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_state_repair as repairer  # noqa: E402


GLOBAL_TEXT = 'model = "gpt-test"\n'
PROJECT_TEXT = 'sandbox_mode = "danger-full-access"\n'
STATE_TEXT = '{"keep":true}'


class CodexStateRepairWriteTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        global_config = root / "global-config.toml"
        project_config = root / "project-config.toml"
        global_state = root / "global-state.json"
        baseline_path = root / "baseline.json"
        backup_root = root / "backups"
        global_config.write_text(GLOBAL_TEXT, encoding="utf-8", newline="\n")
        project_config.write_text(PROJECT_TEXT, encoding="utf-8", newline="\n")
        global_state.write_text(STATE_TEXT, encoding="utf-8", newline="\n")
        baseline = {
            "global_config": str(global_config),
            "project_config": str(project_config),
            "global_state": str(global_state),
            "expected_mcp": {},
            "decommissioned_mcp": {},
            "expected_plugins": [],
            "expected_marketplaces": {},
            "global_required_values": {},
            "project_required_values": {},
            "global_state_required": {},
        }
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return global_config, project_config, global_state, baseline_path, backup_root

    def run_repair(self, baseline_path: Path, backup_root: Path) -> dict:
        with (
            mock.patch.object(repairer, "BASELINE_PATH", baseline_path),
            mock.patch.object(repairer, "BACKUP_ROOT", backup_root),
            mock.patch.object(repairer, "HUB_MANAGED_MCP_NAMES", frozenset()),
            mock.patch.object(repairer, "refresh_runtime_pointers", return_value=[]),
        ):
            return repairer.repair(dry_run=False, runtime_validation=False)

    @staticmethod
    def set_old_timestamp(*paths: Path) -> dict[Path, int]:
        old_ns = 1_700_000_000_000_000_000
        for path in paths:
            os.utime(path, ns=(old_ns, old_ns))
        return {path: path.stat().st_mtime_ns for path in paths}

    def test_no_drift_creates_no_backup_and_touches_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            tracked = files[:4]
            before = self.set_old_timestamp(*tracked)

            result = self.run_repair(files[3], files[4])

            self.assertEqual([], result["written"])
            self.assertIsNone(result["backup_dir"])
            self.assertFalse(files[4].exists())
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in tracked})

    def test_global_config_drift_writes_only_global_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["global_required_values"] = {"sandbox_mode": "danger-full-access"}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[1], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_config"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[1], files[2], files[3])})

            backup_names = {path.name for path in Path(result["backup_dir"]).iterdir()}
            self.assertIn("global_global-config.toml", backup_names)
            self.assertNotIn("project_project-config.toml", backup_names)
            self.assertNotIn("global_state_global-state.json", backup_names)

    def test_wsl_runtime_projection_is_skipped_when_desktop_wsl_is_disabled(self) -> None:
        result = repairer.ensure_wsl_runtime_projection(enabled=False, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertEqual("not_required", result["status"])

    def test_wsl_runtime_projection_consumes_owner_receipt(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": True,
                "session_projection": {
                    "ok": True,
                    "status": "projected",
                    "source_count": 3,
                    "projected_count": 3,
                },
                "state_projection": {
                    "ok": True,
                    "status": "ready",
                    "source_rejected_row_count": 0,
                    "source_missing_row_count": 0,
                },
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(repairer.subprocess, "run", return_value=completed) as run,
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("applied", result["status"])
        self.assertEqual("apply", run.call_args.args[0][-1])

    def test_wsl_runtime_projection_rejects_incomplete_session_receipt(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "changed": True,
                "session_projection": {
                    "ok": True,
                    "status": "projected",
                    "source_count": 3,
                    "projected_count": 2,
                },
                "state_projection": {
                    "ok": True,
                    "status": "ready",
                    "source_rejected_row_count": 0,
                    "source_missing_row_count": 0,
                },
            }),
            stderr="",
        )
        with (
            mock.patch.object(repairer, "codex_desktop_running", return_value=False),
            mock.patch.object(repairer.shutil, "which", return_value="wsl.exe"),
            mock.patch.object(repairer.subprocess, "run", return_value=completed),
        ):
            result = repairer.ensure_wsl_runtime_projection(enabled=True, dry_run=False)

        self.assertFalse(result["ok"])
        self.assertFalse(result["ready"])
        self.assertEqual("owner_incomplete", result["status"])

    def test_project_config_drift_writes_only_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["project_required_values"] = {"approval_policy": "never"}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[0], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["project_config"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[0], files[2], files[3])})

    def test_global_state_drift_writes_only_global_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            baseline["global_state_required"] = {"desktop.show-context-window-usage": True}
            files[3].write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            before = self.set_old_timestamp(files[0], files[1], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_state"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[0], files[1], files[3])})
            state = json.loads(files[2].read_text(encoding="utf-8"))
            self.assertTrue(state["desktop"]["show-context-window-usage"])

    def test_runtime_pointer_drift_writes_only_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            before = self.set_old_timestamp(files[0], files[1], files[2])

            def update_pointer(baseline: dict) -> list[str]:
                baseline["runtime_pointer"] = "new-runtime"
                return ["baseline_runtime_pointer_set"]

            with (
                mock.patch.object(repairer, "BASELINE_PATH", files[3]),
                mock.patch.object(repairer, "BACKUP_ROOT", files[4]),
                mock.patch.object(repairer, "HUB_MANAGED_MCP_NAMES", frozenset()),
                mock.patch.object(repairer, "refresh_runtime_pointers", side_effect=update_pointer),
            ):
                result = repairer.repair(dry_run=False, runtime_validation=False)

            self.assertEqual(["baseline"], result["written"])
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in files[:3]})
            baseline = json.loads(files[3].read_text(encoding="utf-8"))
            self.assertEqual("new-runtime", baseline["runtime_pointer"])

    def test_bom_removal_is_a_real_global_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            files = self.make_fixture(Path(raw))
            files[0].write_bytes(b"\xef\xbb\xbf" + GLOBAL_TEXT.encode("utf-8"))
            before = self.set_old_timestamp(files[1], files[2], files[3])

            result = self.run_repair(files[3], files[4])

            self.assertEqual(["global_config"], result["written"])
            self.assertIn("global_config_remove_bom", result["changed"])
            self.assertFalse(files[0].read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in (files[1], files[2], files[3])})

    def test_wsl_resume_context_projection_defers_while_desktop_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state_path = Path(raw) / "global-state.json"
            state_path.write_text('{"electron-persisted-atom-state":{"queued-follow-ups":{}}}', encoding="utf-8")
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=True),
                mock.patch.object(repairer, "backup_files") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["ready"])
            self.assertEqual("deferred_desktop_running", result["status"])
            backup.assert_not_called()

    def test_wsl_resume_context_projection_repairs_after_restart_boundary(self) -> None:
        broken = "/mnt/c/Program Files/WindowsApps/OpenAI.Codex/app/resources/C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "queued-follow-ups": {
                        "thread": [{"cwd": broken, "context": {"cwd": broken, "workspaceRoots": [broken]}}]
                    }
                }),
                encoding="utf-8",
            )
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["ready"])
            self.assertTrue(result["changed"])
            self.assertEqual("applied", result["status"])
            backup.assert_called_once()
            entry = json.loads(state_path.read_text(encoding="utf-8"))["queued-follow-ups"]["thread"][0]
            expected = "/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager"
            self.assertEqual(expected, entry["cwd"])
            self.assertEqual(expected, entry["context"]["cwd"])
            self.assertEqual([expected], entry["context"]["workspaceRoots"])

    def test_wsl_resume_projection_indexes_top_level_tasks_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "projectless-thread-ids": ["existing"],
                    "thread-project-assignments": {"assigned": {"projectId": "project"}},
                }),
                encoding="utf-8",
            )
            thread_state = root / "state_5.sqlite"
            visible_session = root / "visible.jsonl"
            assigned_session = root / "assigned.jsonl"
            subagent_session = root / "subagent.jsonl"
            archived_session = root / "archived.jsonl"
            no_user_session = root / "no-user.jsonl"
            for path in (visible_session, assigned_session, subagent_session, archived_session, no_user_session):
                path.write_text("{}\n", encoding="utf-8")
            connection = sqlite3.connect(thread_state)
            connection.execute(
                "CREATE TABLE threads (id TEXT, source TEXT, rollout_path TEXT, archived INTEGER, has_user_event INTEGER, updated_at_ms INTEGER)"
            )
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("visible", "vscode", str(visible_session), 0, 1, 6),
                    ("assigned", "vscode", str(assigned_session), 0, 1, 5),
                    ("subagent", '{"subagent":{"thread_spawn":{}}}', str(subagent_session), 0, 1, 4),
                    ("archived", "vscode", str(archived_session), 1, 1, 3),
                    ("no-user-event", "vscode", str(no_user_session), 0, 0, 2),
                    ("missing-session", "vscode", str(root / "missing.jsonl"), 0, 0, 1),
                ],
            )
            connection.commit()
            connection.close()
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                    thread_state_path=thread_state,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["task_index_ok"])
            self.assertEqual(3, result["eligible_task_count"])
            self.assertEqual(2, result["indexed_task_count"])
            backup.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(["existing", "visible", "no-user-event"], state["projectless-thread-ids"])
            self.assertEqual({"assigned": {"projectId": "project"}}, state["thread-project-assignments"])

    def test_wsl_resume_projection_reconstructs_project_assignment_from_thread_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project_root = r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
            state_path = root / "global-state.json"
            state_path.write_text(
                json.dumps({
                    "local-projects": {
                        "main-project": {
                            "id": "main-project",
                            "name": "主项目",
                            "rootPaths": [project_root],
                        }
                    },
                    "projectless-thread-ids": ["imported"],
                    "thread-project-assignments": {},
                }),
                encoding="utf-8",
            )
            session = root / "imported.jsonl"
            session.write_text("{}\n", encoding="utf-8")
            thread_state = root / "state_5.sqlite"
            connection = sqlite3.connect(thread_state)
            connection.execute(
                "CREATE TABLE threads (id TEXT, source TEXT, rollout_path TEXT, cwd TEXT, archived INTEGER, updated_at_ms INTEGER)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                ("imported", "vscode", str(session), project_root + r"\_bridge", 0, 1),
            )
            connection.commit()
            connection.close()
            with (
                mock.patch.object(repairer, "codex_desktop_running", return_value=False),
                mock.patch.object(repairer, "backup_files", return_value=root / "backup") as backup,
            ):
                result = repairer.ensure_wsl_resume_context_projection(
                    enabled=True,
                    dry_run=False,
                    global_state_path=state_path,
                    thread_state_path=thread_state,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(1, result["assigned_task_count"])
            self.assertEqual(1, result["removed_projectless_task_count"])
            backup.assert_called_once()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([], state["projectless-thread-ids"])
            assignment = state["thread-project-assignments"]["imported"]
            self.assertEqual("main-project", assignment["projectId"])
            self.assertEqual(project_root + r"\_bridge", assignment["cwd"])


if __name__ == "__main__":
    unittest.main()
