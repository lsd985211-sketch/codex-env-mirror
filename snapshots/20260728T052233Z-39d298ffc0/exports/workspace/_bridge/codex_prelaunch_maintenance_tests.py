from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

try:
    import codex_prelaunch_maintenance as prelaunch
except ModuleNotFoundError:
    from _bridge import codex_prelaunch_maintenance as prelaunch


class CodexPrelaunchMaintenanceTests(unittest.TestCase):
    def _script(self, root: Path, body: str) -> Path:
        script = root / "owner.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        return script

    def test_successful_owner_receipt_is_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._script(
                root,
                """
                import json
                print(json.dumps({"schema": "owner.v1", "ok": True, "result": {"applied": True, "reason": "done"}}))
                """,
            )
            result = prelaunch.run_prelaunch_maintenance(
                workspace=root, python_executable=sys.executable, maintenance_script=script
            )
        self.assertTrue(result["startup_permitted"])
        self.assertTrue(result["maintenance_ok"])
        self.assertTrue(result["applied"])
        self.assertEqual(result["reason"], "done")

    def test_timeout_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._script(root, "import time\ntime.sleep(10)\n")
            result = prelaunch.run_prelaunch_maintenance(
                workspace=root,
                timeout_seconds=1,
                python_executable=sys.executable,
                maintenance_script=script,
            )
        self.assertTrue(result["startup_permitted"])
        self.assertFalse(result["maintenance_ok"])
        self.assertEqual(result["outcome"], "maintenance_timed_out")
        self.assertTrue(result["child_exit_confirmed"])

    def test_lock_cleanup_requires_matching_child_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            lock_path = workspace / "_bridge" / "runtime" / "codex_session_store" / "auto_maintain.lock.json"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(json.dumps({"pid": 123}), encoding="utf-8")
            self.assertFalse(prelaunch._clear_dead_child_lock(workspace, 456))
            self.assertTrue(lock_path.exists())
            self.assertTrue(prelaunch._clear_dead_child_lock(workspace, 123))
            self.assertFalse(lock_path.exists())

    def test_invalid_json_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._script(root, "print('not-json')\n")
            result = prelaunch.run_prelaunch_maintenance(
                workspace=root, python_executable=sys.executable, maintenance_script=script
            )
        self.assertTrue(result["startup_permitted"])
        self.assertEqual(result["outcome"], "invalid_owner_receipt")

    def test_nonzero_owner_exit_is_fail_open_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._script(root, "import sys\nprint('failure', file=sys.stderr)\nraise SystemExit(7)\n")
            result = prelaunch.run_prelaunch_maintenance(
                workspace=root, python_executable=sys.executable, maintenance_script=script
            )
        self.assertTrue(result["startup_permitted"])
        self.assertEqual(result["reason"], "maintenance_exit_7")
        self.assertLessEqual(len(result.get("detail", "")), prelaunch.MAX_DETAIL_CHARS)

    def test_missing_owner_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = prelaunch.run_prelaunch_maintenance(workspace=root, maintenance_script=root / "missing.py")
        self.assertTrue(result["startup_permitted"])
        self.assertEqual(result["reason"], "missing_session_store_doctor")

    def test_validate_contract(self) -> None:
        payload = prelaunch.validate()
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["checks"]["bounded_timeout"], 180)
        self.assertTrue(payload["checks"]["fail_open_receipt"])
        self.assertTrue(payload["checks"]["hidden_child_on_windows"])


if __name__ == "__main__":
    unittest.main()
