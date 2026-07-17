from __future__ import annotations

import unittest
import sys
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import usb_device_owner as owner  # noqa: E402


def device(instance_id: str, **values: object) -> dict[str, object]:
    row: dict[str, object] = {
        "instance_id": instance_id,
        "class": "USB",
        "friendly_name": instance_id,
        "status": "OK",
        "problem_code": 0,
        "parent_instance_id": "",
        "container_id": "",
        "location_paths": [],
        "hardware_ids": [],
        "compatible_ids": [],
        "driver": {},
    }
    row.update(values)
    return row


class UsbDeviceOwnerTests(unittest.TestCase):
    def test_selects_usb_hid_descendants_and_container_siblings(self) -> None:
        rows = [
            device("PCI\\HOST", **{"class": "USB"}),
            device("USB\\VID_1234&PID_5678\\A", parent_instance_id="PCI\\HOST", container_id="C1"),
            device("HID\\VID_1234&PID_5678\\B", **{"class": "HIDClass"}, parent_instance_id="USB\\VID_1234&PID_5678\\A", container_id="C1"),
            device("BTH\\RADIO", **{"class": "Bluetooth"}, container_id="C1"),
            device("PCI\\UNRELATED", **{"class": "System"}),
        ]

        result = owner.select_usb_related(rows)
        ids = {row["instance_id"] for row in result}

        self.assertEqual(ids, {"PCI\\HOST", "USB\\VID_1234&PID_5678\\A", "HID\\VID_1234&PID_5678\\B", "BTH\\RADIO"})
        usb = next(row for row in result if row["instance_id"].startswith("USB"))
        self.assertEqual(usb["vid"], "1234")
        self.assertEqual(usb["pid"], "5678")
        self.assertEqual(usb["stable_identity"]["container_id"], "C1")

    def test_placeholder_container_does_not_join_unrelated_devices(self) -> None:
        placeholder = "{00000000-0000-0000-FFFF-FFFFFFFFFFFF}"
        rows = [
            device("USB\\ROOT", container_id=placeholder),
            device("ACPI\\I2C-HID", **{"class": "HIDClass"}, container_id=placeholder),
            device("ACPI\\CPU", **{"class": "Processor"}, container_id=placeholder),
        ]

        result = owner.select_usb_related(rows)

        self.assertEqual([row["instance_id"] for row in result], ["USB\\ROOT"])

    def test_diff_preserves_device_identity_and_changed_fields(self) -> None:
        before = {"captured_at": "before", "devices": [device("USB\\A"), device("USB\\OLD")]}
        after = {"captured_at": "after", "devices": [device("USB\\A", status="Error", problem_code=10), device("USB\\NEW")]}

        result = owner.diff_snapshots(before, after)

        self.assertEqual(result["summary"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(result["added"][0]["instance_id"], "USB\\NEW")
        self.assertEqual(result["removed"][0]["instance_id"], "USB\\OLD")
        self.assertIn("problem_code", result["changed"][0]["changes"])

    def test_events_clamp_inputs_and_truncate_messages(self) -> None:
        raw = {
            "captured_at": "now",
            "log_states": [{"log_name": owner.USB_EVENT_LOGS[0], "enabled": True}],
            "events": [{"event_id": 1, "message": "x" * 5000}],
        }
        with patch.object(owner, "_run_powershell", return_value=raw) as run:
            result = owner.collect_events(hours=99999, limit=99999)

        self.assertEqual(result["hours"], 24 * 30)
        self.assertEqual(result["limit"], 500)
        self.assertEqual(len(result["events"][0]["message"]), 1200)
        self.assertIn("-720", run.call_args.args[0])
        self.assertIn("-First 500", run.call_args.args[0])

    def test_watch_is_bounded_and_reports_hotplug(self) -> None:
        samples = iter(
            [
                {"usb\\a": {"instance_id": "USB\\A", "status": "OK"}},
                {
                    "usb\\a": {"instance_id": "USB\\A", "status": "OK"},
                    "usb\\b": {"instance_id": "USB\\B", "status": "OK"},
                },
            ]
        )
        ticks = iter([0.0, 0.0, 1.0, 1.0])

        result = owner.watch_devices(
            duration=1,
            interval=1,
            collector=lambda: next(samples),
            sleeper=lambda _: None,
            clock=lambda: next(ticks),
        )

        self.assertEqual(result["duration_seconds"], 1.0)
        self.assertEqual(result["change_count"], 1)
        self.assertEqual(result["changes"][0]["change"], "added")
        self.assertFalse(result["changes_truncated"])

    def test_adb_parser_accepts_only_bounded_known_fields(self) -> None:
        payload = "SERIAL1\tdevice product:p model:m device:d transport_id:2 unknown:drop\nSERIAL2\tunauthorized\n"

        result = owner._parse_adb_devices(payload)

        self.assertEqual(result[0], {"serial": "SERIAL1", "state": "device", "product": "p", "model": "m", "device": "d", "transport_id": "2"})
        self.assertEqual(result[1], {"serial": "SERIAL2", "state": "unauthorized"})

    def test_android_status_never_starts_adb_server(self) -> None:
        tools = {
            "adb": {"available": True, "path": "C:\\sdk\\adb.exe"},
            "fastboot": {"available": True, "path": "C:\\sdk\\fastboot.exe"},
        }
        with (
            patch.object(owner, "tool_inventory", return_value=tools),
            patch.object(Path, "is_file", return_value=True),
            patch.object(owner, "_adb_server_query", return_value=(False, "")) as adb_query,
            patch.object(owner, "_run_fixed", side_effect=["Android Debug Bridge version 1", ""] ) as run,
        ):
            result = owner.android_status()

        self.assertFalse(result["adb"]["server_running"])
        self.assertFalse(result["adb"]["server_started_by_owner"])
        adb_query.assert_called_once_with("host:devices-l")
        self.assertEqual(run.call_args_list[0].args[0], ["C:\\sdk\\adb.exe", "version"])
        self.assertEqual(run.call_args_list[1].args[0], ["C:\\sdk\\fastboot.exe", "devices"])

    def test_external_action_vectors_exclude_mutating_commands(self) -> None:
        text = " ".join(" ".join(value) for value in owner.ALLOWED_EXTERNAL_ACTIONS.values()).casefold()

        for token in owner.MUTATING_TOKENS:
            self.assertNotIn(token, text)
        self.assertNotIn("adb.exe shell", text)
        self.assertNotIn("flash", text)

    def test_dependency_contract_declares_every_required_package(self) -> None:
        declared = owner.REQUIREMENTS_PATH.read_text(encoding="utf-8").casefold()

        for name, version in owner.REQUIRED_PACKAGES.items():
            self.assertIn(f"{name}=={version}".casefold(), declared)

    def test_doctor_reports_present_device_problem(self) -> None:
        snapshot = {
            "summary": {"usb_related_count": 2},
            "devices": [device("PCI\\HOST", **{"class": "USB"}), device("USB\\BAD", status="Error", problem_code=43)],
            "optional_backends": {},
        }

        result = owner.doctor(snapshot)

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "present_usb_device_problem")

    def test_stopped_smart_card_service_is_not_a_package_failure(self) -> None:
        fake_failures = {
            "pyserial": {"ok": True},
            "hidapi": {"ok": True},
            "pyusb": {"ok": True},
            "fido2": {"ok": True},
            "pyscard": {"ok": True, "available": False, "reason": "smart_card_resource_manager_not_running"},
        }
        snapshot = {"summary": {}, "devices": [device("PCI\\HOST", **{"class": "USB"})], "optional_backends": fake_failures}

        result = owner.doctor(snapshot)

        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])


if __name__ == "__main__":
    unittest.main()
