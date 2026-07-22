from __future__ import annotations

import pickle
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pmb_compatibility as compat
import local_pmb_memory_platform as platform_runtime


class PmbCompatibilityTests(unittest.TestCase):
    def test_wsl_interop_environment_exports_pmb_identity_and_preserves_wslenv(self) -> None:
        env = platform_runtime.process_environment(
            {"WSLENV": "EXISTING/u:PMB_HOME/p"},
            pmb_home=Path("/mnt/c/Users/45543/Desktop/Codex资源库/memory/pmb/data"),
            workspace="mcsmanager",
            executable=Path("/mnt/c/runtime/python.exe"),
            platform_name="posix",
        )

        self.assertEqual(r"C:\Users\45543\Desktop\Codex资源库\memory\pmb\data", env["PMB_HOME"])
        self.assertEqual("mcsmanager", env["PMB_WORKSPACE"])
        self.assertIn("EXISTING/u", env["WSLENV"])
        self.assertNotIn("PMB_HOME/p", env["WSLENV"])
        for key in platform_runtime.PMB_WSLENV_KEYS:
            self.assertIn(key, env["WSLENV"].split(":"))

    def test_runtime_bridge_root_uses_existing_windows_pmb_venv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source" / "_bridge"
            compatibility = root / "compatibility"
            pmb_exe = compatibility / "_bridge" / "venvs" / "pmb-memory" / "Scripts" / "pmb.exe"
            pmb_exe.parent.mkdir(parents=True)
            pmb_exe.touch()
            selected = platform_runtime.runtime_bridge_root(
                source,
                compatibility_root=compatibility,
                platform_name="posix",
            )

        self.assertEqual(compatibility / "_bridge", selected)

    def test_package_patch_state_detects_vulnerable_and_fixed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            search_path = root / "search.py"
            workspace_path = root / "workspace.py"
            daemon_path = root / "daemon.py"
            search_path.write_text(compat.SEARCH_VULNERABLE, encoding="utf-8")
            workspace_path.write_text(compat.WORKSPACE_VULNERABLE, encoding="utf-8")
            daemon_path.write_text(compat.DAEMON_VULNERABLE, encoding="utf-8")
            metadata = {
                "version": "1.2.2",
                "search_path": str(search_path),
                "workspace_path": str(workspace_path),
                "daemon_path": str(daemon_path),
            }

            vulnerable = compat.package_patch_state(metadata)
            self.assertFalse(vulnerable["ok"])
            self.assertTrue(vulnerable["search"]["vulnerable_signature"])
            self.assertTrue(vulnerable["workspace"]["vulnerable_signature"])
            self.assertTrue(vulnerable["daemon"]["vulnerable_signature"])

            search_path.write_text(compat.SEARCH_FIXED, encoding="utf-8")
            workspace_path.write_text(compat.WORKSPACE_FIXED, encoding="utf-8")
            daemon_path.write_text(compat.DAEMON_FIXED, encoding="utf-8")
            fixed = compat.package_patch_state(metadata)
            self.assertTrue(fixed["ok"])

    def test_apply_package_fixes_preserves_health_without_renewing_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "search_path": root / "search.py",
                "workspace_path": root / "workspace.py",
                "daemon_path": root / "daemon.py",
            }
            paths["search_path"].write_text(compat.SEARCH_VULNERABLE, encoding="utf-8")
            paths["workspace_path"].write_text(compat.WORKSPACE_VULNERABLE, encoding="utf-8")
            paths["daemon_path"].write_text(compat.DAEMON_VULNERABLE, encoding="utf-8")
            metadata = {"ok": True, "version": "1.2.2", **{key: str(path) for key, path in paths.items()}}

            with patch("pmb_compatibility.package_metadata", return_value=metadata):
                result = compat.apply_package_fixes(Path("python"), apply=True)

            self.assertTrue(result["ok"])
            self.assertEqual({row["target"] for row in result["changes"]}, {"search", "workspace", "daemon"})
            daemon_text = paths["daemon_path"].read_text(encoding="utf-8")
            self.assertIn(compat.DAEMON_FIXED, daemon_text)
            health_guard = daemon_text.index('if request.method == "OPTIONS"')
            activity_update = daemon_text.index('_LAST_REQUEST["ts"] = time.time()')
            auth_check = daemon_text.index('got = request.headers.get("authorization", "")')
            self.assertLess(health_guard, activity_update)
            self.assertLess(activity_update, auth_check)

    def test_quick_index_state_requires_matching_unique_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            workspace_dir = home / "workspaces" / "test"
            workspace_dir.mkdir(parents=True)
            conn = sqlite3.connect(workspace_dir / "events.sqlite")
            conn.execute("CREATE TABLE events (ulid TEXT UNIQUE, archived_at REAL)")
            conn.executemany("INSERT INTO events (ulid, archived_at) VALUES (?, NULL)", [("a",), ("b",)])
            conn.commit()
            conn.close()
            with (workspace_dir / "bm25_index.pkl").open("wb") as handle:
                pickle.dump({"ulids": ["a", "b"], "tokens": [["a"], ["b"]]}, handle)

            self.assertTrue(compat.quick_index_state(Path(sys.executable), home, "test")["ok"])

            with (workspace_dir / "bm25_index.pkl").open("wb") as handle:
                pickle.dump({"ulids": ["a", "a"], "tokens": [["a"], ["a"]]}, handle)
            state = compat.quick_index_state(Path(sys.executable), home, "test")
            self.assertFalse(state["ok"])
            self.assertEqual(state["bm25"]["duplicate_ulids"], 1)

    @patch("pmb_compatibility._run_json")
    def test_quick_index_state_reads_events_with_the_windows_runtime(self, run_json) -> None:
        run_json.side_effect = [
            {"ok": True, "payload": {"count": 2, "unique_ulids": 2, "ok": True}},
            {
                "ok": True,
                "payload": {
                    "count": 2,
                    "unique_ulids": 2,
                    "token_rows": 2,
                    "duplicate_ulids": 0,
                    "ok": True,
                },
            },
        ]

        state = compat.quick_index_state(
            Path("/mnt/c/runtime/python.exe"),
            Path("/mnt/c/Users/45543/Desktop/Codex资源库/memory/pmb/data"),
            "test",
        )

        self.assertTrue(state["ok"])
        event_command = run_json.call_args_list[0].args[0]
        self.assertEqual(
            r"C:\Users\45543\Desktop\Codex资源库\memory\pmb\data\workspaces\test\events.sqlite",
            event_command[-1],
        )

    @patch("pmb_compatibility.workspace_env_state", return_value={"ok": True})
    @patch("pmb_compatibility.quick_index_state", return_value={"ok": True, "events": {"count": 2}, "bm25": {"count": 2}})
    @patch("pmb_compatibility.package_patch_state", return_value={"ok": True})
    @patch("pmb_compatibility.package_metadata", return_value={"ok": True, "version": "1.2.2"})
    def test_lightweight_doctor_skips_lance(
        self,
        _metadata,
        _patch_state,
        _quick_state,
        _workspace_state,
    ) -> None:
        report = compat.doctor(Path("python"), Path("home"), "test", full_lance=False)
        self.assertTrue(report["ok"])
        self.assertFalse(report["index"]["lance"]["checked"])


if __name__ == "__main__":
    unittest.main()
