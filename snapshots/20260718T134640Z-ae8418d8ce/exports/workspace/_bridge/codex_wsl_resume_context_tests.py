from __future__ import annotations

import unittest
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

try:
    from codex_wsl_resume_context import project_queued_follow_up_contexts
except ModuleNotFoundError:
    from _bridge.codex_wsl_resume_context import project_queued_follow_up_contexts


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


if __name__ == "__main__":
    unittest.main()
