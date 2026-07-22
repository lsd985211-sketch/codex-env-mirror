from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import global_coherence_doctor as doctor


class PlatformScopedOwnerHealthTests(unittest.TestCase):
    def test_windows_owner_is_deferred_from_wsl(self) -> None:
        registry = {
            "contracts": {
                "hardware": {
                    "health_commands": [
                        {
                            "name": "usb_device_owner",
                            "args": ["_bridge/usb_device_owner.py", "validate"],
                            "platform_scope": "windows_host",
                        }
                    ]
                }
            }
        }
        with patch.object(doctor, "execution_platform_scope", return_value="wsl"):
            rows = doctor.owner_health_snapshot(registry)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])
        self.assertTrue(rows[0]["deferred"])
        self.assertEqual(rows[0]["owner_status"], "deferred_to_platform_owner")
        self.assertEqual(rows[0]["platform_scope"], "windows_host")

    def test_windows_owner_runs_on_windows_host(self) -> None:
        registry = {
            "contracts": {
                "hardware": {
                    "health_commands": [
                        {
                            "name": "windows_hardware_owner",
                            "args": ["_bridge/windows_hardware_owner.py", "validate"],
                            "platform_scope": "windows_host",
                        }
                    ]
                }
            }
        }
        with patch.object(doctor, "execution_platform_scope", return_value="windows_host"), patch.object(
            doctor, "run_json", return_value={"ok": True, "schema": "windows_hardware_owner.v1.validate"}
        ) as run:
            rows = doctor.owner_health_snapshot(registry)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[0]["deferred"])
        run.assert_called_once()

    def test_platform_scope_match_is_explicit(self) -> None:
        self.assertTrue(doctor.platform_scope_matches("all", "wsl"))
        self.assertTrue(doctor.platform_scope_matches("windows_host", "windows_host"))
        self.assertFalse(doctor.platform_scope_matches("windows_host", "wsl"))

    def test_full_result_artifact_is_atomically_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(doctor, "RUNTIME_DIR", Path(temp_dir)):
            path = Path(doctor.persist_full_result("validate", {"ok": True, "value": "测试"}))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], "测试")
            self.assertFalse(list(path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
