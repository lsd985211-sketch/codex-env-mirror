from __future__ import annotations

import unittest
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

try:
    from codex_wsl_resume_context import (
        LEGACY_WSL_DESKTOP_PROJECT_ID,
        LEGACY_WSL_DESKTOP_PROJECT_ROOT,
        WSL_DESKTOP_PROJECT_ID,
        WSL_DESKTOP_PROJECT_NAME,
        WSL_DESKTOP_PROJECT_ROOT,
        WSL_WORKSPACE_ROOT,
        ensure_wsl_desktop_project,
        project_queued_follow_up_contexts,
        project_thread_visibility,
        windows_context_path_to_wsl,
    )
except ModuleNotFoundError:
    from _bridge.codex_wsl_resume_context import (
        LEGACY_WSL_DESKTOP_PROJECT_ID,
        LEGACY_WSL_DESKTOP_PROJECT_ROOT,
        WSL_DESKTOP_PROJECT_ID,
        WSL_DESKTOP_PROJECT_NAME,
        WSL_DESKTOP_PROJECT_ROOT,
        WSL_WORKSPACE_ROOT,
        ensure_wsl_desktop_project,
        project_queued_follow_up_contexts,
        project_thread_visibility,
        windows_context_path_to_wsl,
    )


BROKEN_CWD = "/mnt/c/Program Files/WindowsApps/OpenAI.Codex/app/resources/C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager"


class CodexWslResumeContextTests(unittest.TestCase):
    def test_projects_queued_follow_up_paths_without_touching_desktop_roots(self) -> None:
        state = {
            "active-workspace-roots": [r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"],
            "queued-follow-ups": {
                "thread-1": [
                    {
                        "cwd": BROKEN_CWD,
                        "context": {
                            "cwd": BROKEN_CWD,
                            "workspaceRoots": [BROKEN_CWD, "W:\\\\"],
                        },
                    }
                ]
            },
        }

        receipt = project_queued_follow_up_contexts(state)

        self.assertTrue(receipt["changed"])
        self.assertEqual(4, receipt["changed_field_count"])
        entry = state["queued-follow-ups"]["thread-1"][0]
        expected_workspace = "/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager"
        expected_wsl_root = "/home/codexlab/work/codex-workspace"
        self.assertEqual(expected_workspace, entry["cwd"])
        self.assertEqual(expected_workspace, entry["context"]["cwd"])
        self.assertEqual([expected_workspace, expected_wsl_root], entry["context"]["workspaceRoots"])
        self.assertEqual([r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"], state["active-workspace-roots"])

    def test_no_queued_follow_ups_is_a_noop(self) -> None:
        state = {"queued-follow-ups": {}}

        receipt = project_queued_follow_up_contexts(state)

        self.assertFalse(receipt["changed"])
        self.assertEqual(0, receipt["changed_field_count"])

    def test_translates_desktop_wsl_unc_root_to_linux_git_root(self) -> None:
        self.assertEqual(WSL_WORKSPACE_ROOT, windows_context_path_to_wsl(WSL_DESKTOP_PROJECT_ROOT))
        self.assertTrue(WSL_DESKTOP_PROJECT_ROOT.startswith("\\\\wsl.localhost\\"))
        self.assertFalse(WSL_DESKTOP_PROJECT_ROOT.endswith(r"\workspace"))

    def test_adds_only_unassigned_tasks_to_projectless_index(self) -> None:
        state = {
            "projectless-thread-ids": ["existing"],
            "thread-project-assignments": {"assigned": {"projectId": "project"}},
        }

        receipt = project_thread_visibility(state, ["existing", "assigned", "imported", "imported"])

        self.assertTrue(receipt["changed"])
        self.assertEqual(1, receipt["added_count"])
        self.assertEqual(["existing", "imported"], state["projectless-thread-ids"])
        self.assertEqual({"assigned": {"projectId": "project"}}, state["thread-project-assignments"])

    def test_malformed_projectless_index_is_preserved(self) -> None:
        state = {"projectless-thread-ids": {"unexpected": True}}

        receipt = project_thread_visibility(state, ["imported"])

        self.assertFalse(receipt["changed"])
        self.assertEqual("invalid_projectless_index", receipt["status"])
        self.assertEqual({"unexpected": True}, state["projectless-thread-ids"])

    def test_assigns_projectless_tasks_to_existing_local_projects_by_cwd(self) -> None:
        main_root = r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"
        mc_root = r"C:\Users\45543\Documents\mc"
        state = {
            "local-projects": {
                "main-project": {"id": "main-project", "name": "主项目", "rootPaths": [main_root]},
                "mc-project": {"id": "mc-project", "name": "mc", "rootPaths": [mc_root]},
            },
            "projectless-thread-ids": ["main-thread", "mc-thread", "external-thread"],
            "thread-project-assignments": {
                "existing-thread": {"projectKind": "local", "projectId": "preserved", "cwd": "W:\\"},
            },
        }
        thread_cwds = {
            "main-thread": rf"\\?\{main_root}\_bridge",
            "mc-thread": mc_root,
            "external-thread": r"C:\Users\45543\Documents\Codex\scratch",
            "existing-thread": main_root,
        }

        receipt = project_thread_visibility(
            state,
            ["main-thread", "mc-thread", "external-thread", "existing-thread"],
            thread_cwds=thread_cwds,
        )

        self.assertTrue(receipt["changed"])
        self.assertEqual(2, receipt["assigned_count"])
        self.assertEqual(["external-thread"], state["projectless-thread-ids"])
        assignments = state["thread-project-assignments"]
        self.assertEqual("main-project", assignments["main-thread"]["projectId"])
        self.assertEqual("mc-project", assignments["mc-thread"]["projectId"])
        self.assertEqual("preserved", assignments["existing-thread"]["projectId"])
        self.assertEqual(rf"\\?\{main_root}\_bridge", assignments["main-thread"]["cwd"])

    def test_prefers_longest_project_root_for_wsl_mount_cwd(self) -> None:
        state = {
            "local-projects": {
                "parent-project": {
                    "id": "parent-project",
                    "rootPaths": [r"C:\Users\45543\Downloads"],
                },
                "nested-project": {
                    "id": "nested-project",
                    "rootPaths": [r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager"],
                },
            },
            "projectless-thread-ids": ["nested-thread"],
            "thread-project-assignments": {},
        }

        receipt = project_thread_visibility(
            state,
            ["nested-thread"],
            thread_cwds={
                "nested-thread": "/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_bridge"
            },
        )

        self.assertEqual(1, receipt["assigned_count"])
        self.assertEqual(
            "nested-project",
            state["thread-project-assignments"]["nested-thread"]["projectId"],
        )
        self.assertEqual([], state["projectless-thread-ids"])

    def test_registers_wsl_desktop_project_without_changing_selection(self) -> None:
        state = {
            "local-projects": {},
            "electron-saved-workspace-roots": [],
            "project-order": [],
            "electron-workspace-root-labels": {},
            "selected-project": {"type": "local", "projectId": "existing"},
        }

        receipt = ensure_wsl_desktop_project(state, now_ms=123)

        self.assertTrue(receipt["changed"])
        self.assertEqual(WSL_DESKTOP_PROJECT_ID, receipt["project_id"])
        self.assertEqual(
            {
                "id": WSL_DESKTOP_PROJECT_ID,
                "name": WSL_DESKTOP_PROJECT_NAME,
                "rootPaths": [WSL_DESKTOP_PROJECT_ROOT],
                "createdAt": 123,
                "updatedAt": 123,
            },
            state["local-projects"][WSL_DESKTOP_PROJECT_ID],
        )
        self.assertEqual([], state["electron-saved-workspace-roots"])
        self.assertEqual([WSL_DESKTOP_PROJECT_ID], state["project-order"])
        self.assertEqual({}, state["electron-workspace-root-labels"])
        self.assertEqual({"type": "local", "projectId": "existing"}, state["selected-project"])

    def test_wsl_desktop_project_registration_is_idempotent(self) -> None:
        state = {
            "local-projects": {
                WSL_DESKTOP_PROJECT_ID: {
                    "id": WSL_DESKTOP_PROJECT_ID,
                    "name": WSL_DESKTOP_PROJECT_NAME,
                    "rootPaths": [WSL_DESKTOP_PROJECT_ROOT],
                    "createdAt": 123,
                    "updatedAt": 456,
                }
            },
            "project-order": [WSL_DESKTOP_PROJECT_ID],
        }

        receipt = ensure_wsl_desktop_project(state, now_ms=456)

        self.assertFalse(receipt["changed"])

    def test_adopts_project_id_created_by_desktop_for_the_same_unc_root(self) -> None:
        desktop_project_id = "a58b0ff0-2d51-43b5-878b-7105810770ec"
        state = {
            "local-projects": {
                desktop_project_id: {
                    "id": desktop_project_id,
                    "name": WSL_DESKTOP_PROJECT_NAME,
                    "rootPaths": [WSL_DESKTOP_PROJECT_ROOT],
                    "createdAt": 123,
                    "updatedAt": 456,
                }
            },
            "project-order": [desktop_project_id],
        }

        receipt = ensure_wsl_desktop_project(state, now_ms=456)

        self.assertFalse(receipt["changed"])
        self.assertEqual(desktop_project_id, receipt["project_id"])
        self.assertNotIn(WSL_DESKTOP_PROJECT_ID, state["local-projects"])

    def test_migrates_legacy_posix_subdirectory_project_without_ghost_references(self) -> None:
        state = {
            "local-projects": {
                LEGACY_WSL_DESKTOP_PROJECT_ID: {
                    "id": LEGACY_WSL_DESKTOP_PROJECT_ID,
                    "name": WSL_DESKTOP_PROJECT_NAME,
                    "rootPaths": [LEGACY_WSL_DESKTOP_PROJECT_ROOT],
                    "createdAt": 123,
                    "updatedAt": 456,
                }
            },
            "electron-saved-workspace-roots": [LEGACY_WSL_DESKTOP_PROJECT_ROOT],
            "project-order": [LEGACY_WSL_DESKTOP_PROJECT_ID],
            "electron-workspace-root-labels": {
                LEGACY_WSL_DESKTOP_PROJECT_ROOT: WSL_DESKTOP_PROJECT_NAME
            },
            f"electron-persisted-atom-state.sidebar-project-expanded-v1-codex:{LEGACY_WSL_DESKTOP_PROJECT_ID}": True,
            "selected-project": {"type": "local", "projectId": LEGACY_WSL_DESKTOP_PROJECT_ID},
            "thread-project-assignments": {
                "wsl-thread": {"projectKind": "local", "projectId": LEGACY_WSL_DESKTOP_PROJECT_ID}
            },
        }

        receipt = ensure_wsl_desktop_project(state, now_ms=789)

        self.assertTrue(receipt["changed"])
        self.assertEqual(1, receipt["removed_project_count"])
        self.assertEqual(1, receipt["migrated_assignment_count"])
        self.assertNotIn(LEGACY_WSL_DESKTOP_PROJECT_ID, state["local-projects"])
        self.assertEqual(WSL_DESKTOP_PROJECT_ID, state["selected-project"]["projectId"])
        self.assertEqual(
            WSL_DESKTOP_PROJECT_ID,
            state["thread-project-assignments"]["wsl-thread"]["projectId"],
        )
        self.assertEqual([], state["electron-saved-workspace-roots"])
        self.assertEqual([WSL_DESKTOP_PROJECT_ID], state["project-order"])
        self.assertNotIn(LEGACY_WSL_DESKTOP_PROJECT_ROOT, state["electron-workspace-root-labels"])

    def test_assigns_wsl_workspace_thread_to_registered_project(self) -> None:
        state = {
            "local-projects": {
                WSL_DESKTOP_PROJECT_ID: {
                    "id": WSL_DESKTOP_PROJECT_ID,
                    "name": WSL_DESKTOP_PROJECT_NAME,
                    "rootPaths": [WSL_DESKTOP_PROJECT_ROOT],
                }
            },
            "projectless-thread-ids": ["wsl-thread"],
            "thread-project-assignments": {},
        }

        receipt = project_thread_visibility(
            state,
            ["wsl-thread"],
            thread_cwds={"wsl-thread": WSL_WORKSPACE_ROOT + "/workspace/_bridge"},
        )

        self.assertTrue(receipt["changed"])
        self.assertEqual(1, receipt["assigned_count"])
        self.assertEqual(WSL_DESKTOP_PROJECT_ID, state["thread-project-assignments"]["wsl-thread"]["projectId"])
        self.assertEqual([], state["projectless-thread-ids"])


if __name__ == "__main__":
    unittest.main()
