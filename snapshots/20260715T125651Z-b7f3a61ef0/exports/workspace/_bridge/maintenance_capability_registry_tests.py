#!/usr/bin/env python3

import unittest

from maintenance_capability_registry import infer_system


class MaintenanceCapabilityRegistryTests(unittest.TestCase):
    def test_codex_environment_mirror_is_backup_capability(self) -> None:
        self.assertEqual(infer_system("_bridge/codex_environment_mirror.py", "Unified recovery mirror adapter"), "backup")


if __name__ == "__main__":
    unittest.main()
