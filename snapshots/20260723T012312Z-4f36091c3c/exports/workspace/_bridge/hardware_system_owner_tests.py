from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import hardware_system_owner as owner  # noqa: E402


class HardwareSystemOwnerTests(unittest.TestCase):
    def test_windows_request_defers_from_wsl(self) -> None:
        with patch.object(owner, "current_platform", return_value="wsl_host"):
            result = owner.platform_snapshot("windows_host")
        self.assertTrue(result["deferred"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["owner_command"], "python _bridge/windows_hardware_owner.py snapshot")

    def test_current_wsl_snapshot_consumes_owner_result(self) -> None:
        expected = {"ok": True, "schema": "wsl_hardware_owner.v1.snapshot"}
        with patch.object(owner, "current_platform", return_value="wsl_host"):
            result = owner.platform_snapshot("wsl_host", wsl_snapshot=lambda: expected)
        self.assertIs(result, expected)

    def test_all_requires_both_platform_receipts(self) -> None:
        with patch.object(owner, "current_platform", return_value="wsl_host"), patch.object(owner.wsl_hardware_owner, "collect_snapshot", return_value={"ok": True}):
            result = owner.snapshot("all")
        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["pending_platforms"], ["windows_host"])

    def test_facade_keeps_control_with_control_owner(self) -> None:
        routes = owner.capability_map()
        self.assertEqual(routes["authority"]["usb_control"], "usb_device_control")
        self.assertNotEqual(routes["authority"]["usb_control"], "hardware_system_owner")


if __name__ == "__main__":
    unittest.main()
