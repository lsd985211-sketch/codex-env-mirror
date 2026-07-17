#!/usr/bin/env python3

import unittest

from maintenance_capability_registry import infer_system


class MaintenanceCapabilityRegistryTests(unittest.TestCase):
    def test_codex_environment_mirror_is_backup_capability(self) -> None:
        self.assertEqual(infer_system("_bridge/codex_environment_mirror.py", "Unified recovery mirror adapter"), "backup")

    def test_plugin_runtime_doctor_is_startup_capability(self) -> None:
        self.assertEqual(
            infer_system("_bridge/codex_plugin_runtime_doctor.py", "package publisher owns the native addon"),
            "startup",
        )

    def test_shared_process_liveness_is_startup_capability(self) -> None:
        self.assertEqual(
            infer_system("_bridge/shared/process_liveness.py", "network lease and launcher helper"),
            "startup",
        )


if __name__ == "__main__":
    unittest.main()
