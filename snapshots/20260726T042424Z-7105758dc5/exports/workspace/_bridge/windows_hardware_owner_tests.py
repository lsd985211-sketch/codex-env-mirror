from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import windows_hardware_owner as owner  # noqa: E402


def device(instance_id: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "instance_id": instance_id,
        "class": "Camera",
        "class_guid": "{camera}",
        "friendly_name": "Test Camera",
        "status": "OK",
        "problem_code": 0,
        "present": True,
        "parent_instance_id": "USB\\PARENT\\1",
        "child_instance_ids": [],
        "child_topology_known": True,
        "container_id": "{container}",
        "location_paths": ["PCIROOT#USB(1)"],
        "hardware_ids": ["USB\\VID_0001&PID_0002"],
        "compatible_ids": ["USB\\Class_0e"],
        "service": "usbvideo",
        "enumerator": "USB",
        "bus_type_guid": "{usb}",
        "driver_version": "1.0",
        "driver_inf": "usbvideo.inf",
        "safe_removal_required": False,
        "safe_removal_known": True,
        "removal_policy": 3,
    }
    row.update(changes)
    return row


def snapshot(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": f"{owner.SCHEMA}.snapshot",
        "ok": True,
        "read_only": True,
        "captured_at": "2026-07-17T00:00:00+00:00",
        "machine": {},
        "summary": owner.build_summary(rows),
        "problems": [item for item in rows if owner.is_problem(item)],
        "devices": rows,
        "safety": {
            "device_writes_supported": False,
            "driver_changes_supported": False,
            "service_changes_supported": False,
            "resident_watch_supported": False,
            "arbitrary_command_supported": False,
        },
    }


class WindowsHardwareOwnerTests(unittest.TestCase):
    def test_summary_covers_classes_enumerators_and_problem_codes(self) -> None:
        rows = [
            device("USB\\CAMERA\\1"),
            device("PCI\\DISPLAY\\1", **{"class": "Display", "enumerator": "PCI", "status": "Error", "problem_code": 28}),
        ]

        result = owner.build_summary(rows)

        self.assertEqual(result["device_count"], 2)
        self.assertEqual(result["problem_device_count"], 1)
        self.assertEqual(result["class_counts"], {"Camera": 1, "Display": 1})
        self.assertEqual(result["problem_code_counts"], {"28": 1})

    def test_exact_device_uses_exact_identity_and_fingerprint(self) -> None:
        row = device("PCI\\VEN_1234&DEV_5678\\1")
        result = owner.exact_device(row["instance_id"], query=lambda _: ({}, [row]))

        self.assertTrue(result["ok"])
        self.assertFalse(result["problem"])
        self.assertEqual(len(result["stable_fingerprint"]), 32)

    def test_instance_id_blocks_multiline_and_non_pnp_values(self) -> None:
        for value in ("", "USB", "USB\\GOOD\nBAD", "PCI\\" + "X" * 600):
            with self.subTest(value=value[:20]):
                with self.assertRaises(ValueError):
                    owner._validate_instance_id(value)

    def test_diff_preserves_added_removed_and_changed_identity(self) -> None:
        first = device("USB\\ONE\\1")
        changed = device("USB\\ONE\\1", status="Error", problem_code=10)
        added = device("PCI\\TWO\\1", **{"class": "Display", "enumerator": "PCI"})

        result = owner.diff_snapshots(snapshot([first]), snapshot([changed, added]))

        self.assertEqual(result["added_count"], 1)
        self.assertEqual(result["removed_count"], 0)
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["changed"][0]["changed_fields"], ["status", "problem_code"])

    def test_class_report_is_bounded_by_class_not_device_count(self) -> None:
        rows = [device(f"USB\\CAMERA\\{index}") for index in range(8)]
        result = owner.class_report(snapshot(rows))

        self.assertEqual(result["class_count"], 1)
        self.assertEqual(result["classes"][0]["device_count"], 8)
        self.assertEqual(len(result["classes"][0]["sample_instance_ids"]), 5)

    def test_default_snapshot_view_is_bounded_and_full_is_lossless(self) -> None:
        current = snapshot([device(f"USB\\CAMERA\\{index}") for index in range(40)])

        bounded = owner.snapshot_view(current, full=False)
        full = owner.snapshot_view(current, full=True)

        self.assertEqual(len(bounded["device_sample"]), 30)
        self.assertNotIn("devices", bounded)
        self.assertIs(full, current)

    def test_default_snapshot_view_preserves_query_layer_contract(self) -> None:
        current = owner.collect_snapshot(query=lambda _: ({}, [device("USB\\CAMERA\\1")]))

        bounded = owner.snapshot_view(current, full=False)

        self.assertEqual(bounded["detail_level"], "fast_inventory")
        self.assertEqual(bounded["topology_scope"], "exact_device_only")

    def test_fast_snapshot_does_not_bulk_query_expensive_pnp_properties(self) -> None:
        script = owner.SNAPSHOT_QUERY_SCRIPT.casefold()

        self.assertIn("win32_pnpentity", script)
        self.assertIn("win32_pnpsigneddriver", script)
        self.assertNotIn("get-pnpdeviceproperty", script)

    def test_exact_device_script_retains_topology_and_identity_properties(self) -> None:
        script = owner.DETAIL_QUERY_SCRIPT.casefold()

        self.assertIn("get-pnpdeviceproperty", script)
        self.assertIn("devpkey_device_parent", script)
        self.assertIn("devpkey_device_children", script)
        self.assertIn("devpkey_device_hardwareids", script)
        for token in owner.MUTATING_SCRIPT_TOKENS:
            self.assertNotIn(token, script)

    def test_snapshot_declares_inventory_and_exact_topology_boundary(self) -> None:
        result = owner.collect_snapshot(query=lambda _: ({}, [device("USB\\CAMERA\\1")]))

        self.assertEqual(result["detail_level"], "fast_inventory")
        self.assertEqual(result["topology_scope"], "exact_device_only")

    def test_query_selects_fast_and_exact_scripts_without_interpolation(self) -> None:
        raw = {"machine": {}, "devices": [device("USB\\CAMERA\\1")]}
        with patch.object(owner, "_run_powershell", return_value=raw) as run:
            machine, _ = owner.query_devices()
            self.assertEqual(run.call_args.args[0], owner.SNAPSHOT_QUERY_SCRIPT)
            self.assertEqual(machine["query_mode"], "fast_inventory")

        with patch.object(owner, "_run_powershell", return_value=raw) as run:
            machine, _ = owner.query_devices("USB\\CAMERA\\1")
            self.assertEqual(run.call_args.args[0], owner.DETAIL_QUERY_SCRIPT)
            self.assertEqual(run.call_args.kwargs["env"]["CODEX_HARDWARE_INSTANCE_ID"], "USB\\CAMERA\\1")
            self.assertEqual(machine["query_mode"], "exact_device")

    def test_event_query_is_fixed_and_bounded(self) -> None:
        script = owner._events_script(24, 100).casefold()

        self.assertIn("get-winevent", script)
        for token in owner.MUTATING_SCRIPT_TOKENS:
            self.assertNotIn(token, script)
        for hours, limit in ((0, 10), (721, 10), (1, 0), (1, 501)):
            with self.subTest(hours=hours, limit=limit):
                with self.assertRaises(ValueError):
                    owner._events_script(hours, limit)

    def test_full_result_refs_preserve_required_command_arguments(self) -> None:
        parser = owner.build_parser()
        device_args = parser.parse_args(["device", "--instance-id", "USB\\VID_0001&PID_0002\\1"])
        event_args = parser.parse_args(["events", "--hours", "3", "--limit", "7"])
        diff_args = parser.parse_args(["diff", "--before", "before file.json", "--after", "after.json"])

        self.assertIn("'USB\\VID_0001&PID_0002\\1'", owner._full_result_ref(device_args))
        self.assertIn("--hours 3 --limit 7", owner._full_result_ref(event_args))
        self.assertIn("'before file.json'", owner._full_result_ref(diff_args))

    def test_validate_accepts_supplied_read_only_snapshot(self) -> None:
        result = owner.validate(snapshot([device("USB\\CAMERA\\1")]))

        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])

    def test_live_validation_defers_outside_windows(self) -> None:
        with patch.object(owner.os, "name", "posix"):
            result = owner.validate()

        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["target_platform"], "windows_host")


if __name__ == "__main__":
    unittest.main()
