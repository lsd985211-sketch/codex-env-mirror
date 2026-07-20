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

    def test_desktop_protocol_compatibility_is_startup_capability(self) -> None:
        self.assertEqual(
            infer_system(
                "_bridge/codex_desktop_protocol_compatibility.py",
                "Vendor protocol migration remains pending.",
            ),
            "startup",
        )

    def test_music_library_owner_is_audio_capability(self) -> None:
        self.assertEqual(
            infer_system("_bridge/music_library_owner.py", "USB-aware music library organization"),
            "audio",
        )

    def test_audio_toolkit_is_audio_capability(self) -> None:
        self.assertEqual(
            infer_system("_bridge/audio_toolkit/audio_toolkit.py", "Audio inspection and transformation toolkit"),
            "audio",
        )


if __name__ == "__main__":
    unittest.main()
