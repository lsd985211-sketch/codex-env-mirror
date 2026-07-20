from __future__ import annotations

import unittest

import tool_exposure_doctor as doctor


class HubManagedExposureTests(unittest.TestCase):
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
