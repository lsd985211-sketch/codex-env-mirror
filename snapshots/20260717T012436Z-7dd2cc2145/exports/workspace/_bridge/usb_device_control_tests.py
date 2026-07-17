from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import usb_device_control as control  # noqa: E402


def camera(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "instance_id": "USB\\VID_30C9&PID_000E&MI_00\\TEST",
        "class": "Camera",
        "friendly_name": "Test USB Camera",
        "status": "OK",
        "problem_code": 0,
        "present": True,
        "parent_instance_id": "USB\\VID_30C9&PID_000E\\PARENT",
        "container_id": "{camera}",
        "location_paths": ["PCIROOT#USBROOT#USB(6)"],
        "hardware_ids": ["USB\\VID_30C9&PID_000E&MI_00"],
        "compatible_ids": ["USB\\Class_0e&SubClass_01&Prot_00"],
        "child_instance_ids": [],
        "driver_version": "1.2.3",
        "driver_inf": "usbvideo.inf",
    }
    row.update(changes)
    return row


def command_ok(arguments: list[str]) -> dict[str, object]:
    return {"returncode": 0, "command": ["pnputil.exe", *arguments], "stdout_tail": "", "stderr_tail": ""}


class UsbDeviceControlTests(unittest.TestCase):
    def test_rescan_plan_is_deterministic_and_requires_exact_confirmation(self) -> None:
        first = control.build_plan("rescan", admin=True)
        second = control.build_plan("rescan", admin=False)

        self.assertTrue(first["ok"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["confirmation_required"], "RESCAN-PNP-DEVICES")
        self.assertEqual(first["command_contract"], ["/scan-devices"])

    def test_restart_plan_accepts_noncritical_leaf(self) -> None:
        result = control.build_plan("restart", camera()["instance_id"], query=lambda _: camera(), admin=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["risk"], "L2")
        self.assertEqual(len(result["target_fingerprint"]), 32)
        self.assertEqual(result["confirmation_required"], f"RESTART-USB:{result['plan_id']}")

    def test_core_usb_and_input_devices_are_always_blocked(self) -> None:
        rows = [
            camera(**{"class": "USB", "friendly_name": "USB Root Hub (USB 3.0)"}),
            camera(**{"class": "Keyboard", "friendly_name": "HID Keyboard Device"}),
            camera(**{"class": "DiskDrive", "friendly_name": "USB Storage Device"}),
            camera(compatible_ids=["USB\\Class_08&SubClass_06&Prot_50"]),
            camera(child_instance_ids=["HID\\VID_30C9&PID_000E\\CHILD"]),
            camera(**{"class": "SoftwareDevice", "friendly_name": "Unknown USB Function"}),
        ]

        for row in rows:
            with self.subTest(device_class=row["class"]):
                result = control.build_plan("restart", row["instance_id"], query=lambda _, value=row: value, admin=True)
                self.assertFalse(result["ok"])
                self.assertTrue(result["blockers"])

    def test_disable_requires_tight_class_allowlist_and_healthy_state(self) -> None:
        serial = camera(**{"class": "Ports", "friendly_name": "USB Serial Port"})
        allowed = control.build_plan("disable", serial["instance_id"], query=lambda _: serial, admin=True)
        unhealthy = control.build_plan("disable", serial["instance_id"], query=lambda _: {**serial, "status": "Error", "problem_code": 10}, admin=True)
        generic = control.build_plan("disable", camera()["instance_id"], query=lambda _: camera(**{"class": "SoftwareDevice"}), admin=True)

        self.assertTrue(allowed["ok"])
        self.assertTrue(allowed["rollback"]["available"])
        self.assertFalse(unhealthy["ok"])
        self.assertFalse(generic["ok"])

    def test_apply_rejects_confirmation_and_fingerprint_before_execution(self) -> None:
        calls: list[list[str]] = []
        plan = control.build_plan("restart", camera()["instance_id"], query=lambda _: camera(), admin=True)

        result = control.apply_control(
            action="restart",
            instance_id=camera()["instance_id"],
            expected_fingerprint=plan["target_fingerprint"],
            confirm="WRONG",
            query=lambda _: camera(),
            execute=lambda args: calls.append(args) or command_ok(args),
            mutex_factory=contextlib.nullcontext,
            admin=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "confirmation_mismatch")
        self.assertEqual(calls, [])

    def test_restart_apply_uses_fixed_vector_and_post_state_acceptance(self) -> None:
        before = camera()
        queries = iter([before, before, before])
        plan = control.build_plan("restart", before["instance_id"], query=lambda _: before, admin=True)
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = control.apply_control(
                action="restart",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(queries),
                execute=lambda args: calls.append(args) or command_ok(args),
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=Path(temp_dir),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(calls, [["/restart-device", before["instance_id"]]])
            self.assertTrue((Path(temp_dir) / f"{result['operation_id']}.json").is_file())

    def test_apply_rechecks_fingerprint_inside_mutex_before_execution(self) -> None:
        before = camera()
        replaced = camera(driver_version="9.9.9")
        plan = control.build_plan("restart", before["instance_id"], query=lambda _: before, admin=True)
        queries = iter([before, replaced])
        calls: list[list[str]] = []

        result = control.apply_control(
            action="restart",
            instance_id=before["instance_id"],
            expected_fingerprint=plan["target_fingerprint"],
            confirm=plan["confirmation_required"],
            query=lambda _: next(queries),
            execute=lambda args: calls.append(args) or command_ok(args),
            mutex_factory=contextlib.nullcontext,
            admin=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "target_fingerprint_changed_before_execution")
        self.assertEqual(calls, [])

    def test_disable_rechecks_dynamic_policy_inside_mutex(self) -> None:
        before = camera(**{"class": "Ports", "friendly_name": "USB Serial Port"})
        unhealthy = {**before, "status": "Error", "problem_code": 10}
        plan = control.build_plan("disable", before["instance_id"], query=lambda _: before, admin=True)
        queries = iter([before, unhealthy])
        calls: list[list[str]] = []

        result = control.apply_control(
            action="disable",
            instance_id=before["instance_id"],
            expected_fingerprint=plan["target_fingerprint"],
            confirm=plan["confirmation_required"],
            query=lambda _: next(queries),
            execute=lambda args: calls.append(args) or command_ok(args),
            mutex_factory=contextlib.nullcontext,
            admin=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "target_policy_changed_before_execution")
        self.assertEqual(calls, [])

    def test_pending_receipt_is_durable_before_command_execution(self) -> None:
        before = camera()
        plan = control.build_plan("restart", before["instance_id"], query=lambda _: before, admin=True)
        queries = iter([before, before, before])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def execute(arguments: list[str]) -> dict[str, object]:
                paths = list(root.glob("*.json"))
                self.assertEqual(len(paths), 1)
                pending = json.loads(paths[0].read_text(encoding="utf-8"))
                self.assertEqual(pending["execution"]["status"], "pending")
                self.assertFalse(pending["applied"])
                return command_ok(arguments)

            result = control.apply_control(
                action="restart",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(queries),
                execute=execute,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(control.doctor(receipt_root=root)["ok"])

    def test_failed_restart_attempts_bounded_enable_recovery(self) -> None:
        before = camera()
        disabled = camera(status="Error", problem_code=22)
        queries = iter([before, before, disabled, before])
        plan = control.build_plan("restart", before["instance_id"], query=lambda _: before, admin=True)
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = control.apply_control(
                action="restart",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(queries),
                execute=lambda args: calls.append(args) or command_ok(args),
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=Path(temp_dir),
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["recovery"]["attempted"])
        self.assertTrue(result["recovery"]["accepted"])
        self.assertEqual(calls[-1], ["/enable-device", before["instance_id"]])

    def test_query_failure_does_not_trigger_blind_enable_recovery(self) -> None:
        before = camera()
        plan = control.build_plan("restart", before["instance_id"], query=lambda _: before, admin=True)
        query_count = 0
        calls: list[list[str]] = []

        def query(_: str) -> dict[str, object]:
            nonlocal query_count
            query_count += 1
            if query_count == 3:
                raise control.ControlError("simulated post-state query failure")
            return before

        with tempfile.TemporaryDirectory() as temp_dir:
            result = control.apply_control(
                action="restart",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=query,
                execute=lambda args: calls.append(args) or command_ok(args),
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=Path(temp_dir),
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["recovery"]["attempted"])
        self.assertEqual(calls, [["/restart-device", before["instance_id"]]])

    def test_disable_receipt_is_only_authority_for_rollback(self) -> None:
        before = camera(**{"class": "Ports", "friendly_name": "USB Serial Port"})
        disabled = {**before, "status": "Error", "problem_code": 22}
        plan = control.build_plan("disable", before["instance_id"], query=lambda _: before, admin=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apply_queries = iter([before, before, disabled])
            applied = control.apply_control(
                action="disable",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(apply_queries),
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )
            rollback_queries = iter([disabled, before])
            rolled_back = control.rollback_control(
                operation_id=applied["operation_id"],
                confirm=applied["rollback"]["confirmation_required"],
                query=lambda _: next(rollback_queries),
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )
            repeated = control.rollback_control(
                operation_id=applied["operation_id"],
                confirm=applied["rollback"]["confirmation_required"],
                query=lambda _: before,
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )

        self.assertTrue(applied["ok"])
        self.assertTrue(rolled_back["ok"])
        self.assertEqual(rolled_back["execution"]["command"][1], "/enable-device")
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["reason"], "rollback_not_available")

    def test_failed_rollback_remains_available_for_retry(self) -> None:
        before = camera(**{"class": "Ports", "friendly_name": "USB Serial Port"})
        disabled = {**before, "status": "Error", "problem_code": 22}
        plan = control.build_plan("disable", before["instance_id"], query=lambda _: before, admin=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apply_queries = iter([before, before, disabled])
            applied = control.apply_control(
                action="disable",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(apply_queries),
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )
            first_queries = iter([disabled, disabled])
            failed = control.rollback_control(
                operation_id=applied["operation_id"],
                confirm=applied["rollback"]["confirmation_required"],
                query=lambda _: next(first_queries),
                execute=lambda args: {**command_ok(args), "returncode": 1},
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )
            second_queries = iter([disabled, before])
            retried = control.rollback_control(
                operation_id=applied["operation_id"],
                confirm=applied["rollback"]["confirmation_required"],
                query=lambda _: next(second_queries),
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )

        self.assertFalse(failed["ok"])
        self.assertTrue(retried["ok"])

    def test_rollback_rereads_receipt_after_mutex_acquisition(self) -> None:
        before = camera(**{"class": "Ports", "friendly_name": "USB Serial Port"})
        disabled = {**before, "status": "Error", "problem_code": 22}
        plan = control.build_plan("disable", before["instance_id"], query=lambda _: before, admin=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apply_queries = iter([before, before, disabled])
            applied = control.apply_control(
                action="disable",
                instance_id=before["instance_id"],
                expected_fingerprint=plan["target_fingerprint"],
                confirm=plan["confirmation_required"],
                query=lambda _: next(apply_queries),
                execute=command_ok,
                mutex_factory=contextlib.nullcontext,
                admin=True,
                receipt_root=root,
            )
            receipt_path = root / f"{applied['operation_id']}.json"
            calls: list[list[str]] = []

            @contextlib.contextmanager
            def consume_before_lock_yields():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt["rollback"]["status"] = "completed"
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                yield

            replay = control.rollback_control(
                operation_id=applied["operation_id"],
                confirm=applied["rollback"]["confirmation_required"],
                query=lambda _: disabled,
                execute=lambda args: calls.append(args) or command_ok(args),
                mutex_factory=consume_before_lock_yields,
                admin=True,
                receipt_root=root,
            )

        self.assertFalse(replay["ok"])
        self.assertEqual(replay["reason"], "rollback_not_available")
        self.assertEqual(calls, [])

    def test_instance_id_validation_blocks_injection_and_non_usb_targets(self) -> None:
        for value in ("USB\\GOOD\n/force", "PCI\\VEN_8086", "", "USB\\" + "X" * 600):
            with self.subTest(value=value[:30]):
                with self.assertRaises(ValueError):
                    control._validate_instance_id(value)

    def test_command_contract_has_no_driver_remove_force_or_reboot(self) -> None:
        text = json_text = str(control.ALLOWED_COMMANDS).casefold()
        self.assertTrue(text and json_text)
        for token in ("remove", "delete", "add-driver", "force", "reboot", "flash", "format", "eject"):
            self.assertNotIn(token, text)

    def test_validate_is_read_only(self) -> None:
        result = control.validate()

        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])


if __name__ == "__main__":
    unittest.main()
