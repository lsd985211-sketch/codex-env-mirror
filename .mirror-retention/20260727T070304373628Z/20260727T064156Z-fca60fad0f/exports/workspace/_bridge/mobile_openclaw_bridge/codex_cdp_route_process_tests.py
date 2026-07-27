from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

import codex_cdp_route_process as process_owner  # noqa: E402
import codex_cdp_route  # noqa: E402


class FakeProcess:
    pid = 4321


class CodexCdpRouteProcessTests(unittest.TestCase):
    def test_detached_launch_preserves_port_and_does_not_wait(self) -> None:
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            return FakeProcess()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "start.ps1"
            script.write_text("Write-Output ok\n", encoding="utf-8")
            result = process_owner.launch_start_script_detached(
                script,
                port=9231,
                cwd=root,
                popen_factory=fake_popen,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["launched"])
        self.assertEqual(result["pid"], 4321)
        self.assertEqual(captured["env"]["CODEX_CDP_PORT"], "9231")
        self.assertNotIn("timeout", captured)
        self.assertIsNotNone(captured["stdout"])
        self.assertIsNotNone(captured["stderr"])

    def test_missing_script_is_not_submitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = process_owner.launch_start_script_detached(root / "missing.ps1", port=9231, cwd=root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["launched"])

    def test_route_returns_start_in_progress_without_owning_launcher_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "start.ps1"
            script.write_text("Write-Output ok\n", encoding="utf-8")
            settings = {
                "host": "localhost",
                "port": 9231,
                "probe_timeout": 0.2,
                "start_timeout": 10,
                "start_scripts": [script],
            }
            launch_receipt = {"ok": True, "launched": True, "pid": 4321, "reason": "governed_launcher_submitted"}
            with (
                mock.patch.object(codex_cdp_route, "codex_cdp_config", return_value=settings),
                mock.patch.object(codex_cdp_route, "tcp_check", return_value={"ok": False}),
                mock.patch.object(
                    codex_cdp_route,
                    "os_port_listener_state",
                    return_value={"live_count": 0, "stale_count": 0},
                ),
                mock.patch.object(codex_cdp_route, "launch_start_script_detached", return_value=launch_receipt),
                mock.patch.object(codex_cdp_route.time, "time", side_effect=[0.0, 11.0]),
            ):
                result = codex_cdp_route.ensure_codex_cdp({"trigger": {}})
        self.assertFalse(result["ok"])
        self.assertTrue(result["started"])
        self.assertEqual(result["reason"], "codex_cdp_start_in_progress")
        self.assertEqual(result["launches"][0]["pid"], 4321)


if __name__ == "__main__":
    unittest.main()
