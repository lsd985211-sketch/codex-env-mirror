from __future__ import annotations

import unittest

import tool_exposure_doctor as doctor


class HubManagedExposureTests(unittest.TestCase):
    def test_native_app_tool_discovery_requires_current_schema_not_persisted_metadata(self) -> None:
        surface = doctor.native_app_tool_discovery()
        self.assertEqual(surface["authority"], "current exposed Codex App tool schema")
        self.assertIn("does not prove", surface["persistence_rule"])
        self.assertIn("list_threads", surface["coordination_example"])

        payload = doctor.doctor(
            {
                "mcp": [],
                "thread_surface": {"state": "thread_dynamic_tools_empty"},
                "native_app_tool_surface": surface,
                "current_turn_tool_observations": {},
            }
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["issues"][0]["code"],
            "codex_thread_dynamic_tools_empty_native_surface_check_required",
        )
        self.assertEqual(
            payload["issues"][0]["detail"]["native_app_tool_surface"]["authority"],
            "current exposed Codex App tool schema",
        )

    def test_hub_managed_profile_does_not_require_desktop_config(self) -> None:
        row = {
            "name": "github",
            "state": "hub_managed",
            "hub_managed": True,
            "configured": False,
            "cli_visible": False,
            "usable_state": "hub_route_probe_required",
            "current_turn": {"state": "unverified", "callable": None},
            "circuit_breaker": {"tripped": False},
        }
        payload = doctor.doctor({"mcp": [row]})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issues"], [])

    def test_hub_managed_exposure_marks_config_as_not_required(self) -> None:
        layers = doctor.exposure_layers_for_row(
            {"configured": False, "hub_managed": True, "cli_visible": False},
            {"state": "unverified", "callable": None},
        )
        self.assertTrue(layers["config_ok"])
        self.assertFalse(layers["config_required"])
        self.assertTrue(layers["hub_route_expected"])

    def test_hub_managed_profile_waits_for_a_hub_probe(self) -> None:
        self.assertEqual(
            doctor.usable_state_for_row(
                {"hub_managed": True, "state": "hub_managed", "cli_visible": False},
                {"state": "unverified", "callable": None},
            ),
            "hub_route_probe_required",
        )


if __name__ == "__main__":
    unittest.main()
