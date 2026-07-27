from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import wsl_hardware_owner as owner  # noqa: E402


class WslHardwareOwnerTests(unittest.TestCase):
    def test_usb_parser_keeps_bus_identity(self) -> None:
        with patch.object(owner, "run_fixed", return_value={"ok": True, "stdout": "Bus 001 Device 004: ID 12d1:107e HUAWEI Phone\n", "stderr": ""}):
            result = owner.collect_usb("/usr/bin/lsusb")
        self.assertEqual(result["devices"][0]["vid"], "12d1")
        self.assertEqual(result["devices"][0]["pid"], "107e")

    def test_gpu_parser_preserves_projection_fields(self) -> None:
        row = "NVIDIA GeForce RTX 3060 Laptop GPU, GPU-1, 610.62, 6144, 8.6\n"
        with patch.object(owner, "run_fixed", return_value={"ok": True, "stdout": row, "stderr": ""}):
            result = owner.collect_gpu("/usr/lib/wsl/lib/nvidia-smi")
        self.assertEqual(result["gpus"][0]["compute_capability"], "8.6")
        self.assertEqual(result["gpus"][0]["memory_total_mib"], "6144")

    def test_snapshot_distinguishes_projection_from_host_truth(self) -> None:
        tools = {
            "lsblk": {"required": True, "available": True, "path": "lsblk"},
            "udevadm": {"required": True, "available": True, "path": "udevadm"},
            "lsusb": {"required": False, "available": False, "path": ""},
            "lspci": {"required": False, "available": False, "path": ""},
            "nvidia-smi": {"required": False, "available": False, "path": ""},
        }
        with patch.object(owner, "is_wsl", return_value=True), patch.object(owner, "collect_block", return_value={"ok": True, "devices": []}):
            result = owner.collect_snapshot(tools=tools)
        self.assertTrue(result["ok"])
        self.assertFalse(result["authority"]["host_hardware_truth"])
        self.assertTrue(result["authority"]["linux_visible_projection_only"])

    def test_optional_tools_do_not_fail_validation(self) -> None:
        snapshot = {
            "platform": {"is_wsl": True},
            "tools": {
                "lsblk": {"required": True, "available": True},
                "udevadm": {"required": True, "available": True},
                "lsusb": {"required": False, "available": False},
            },
            "block": {"ok": True},
            "authority": {"host_hardware_truth": False},
            "safety": {"device_writes_supported": False},
        }
        self.assertTrue(owner.validate(snapshot)["ok"])


if __name__ == "__main__":
    unittest.main()
