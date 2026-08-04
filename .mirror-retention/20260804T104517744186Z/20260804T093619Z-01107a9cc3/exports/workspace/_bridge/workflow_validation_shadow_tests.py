#!/usr/bin/env python3

import unittest

from workflow_validation_shadow import ValidationPlanShadow, signature_for_value


class ValidationPlanShadowTests(unittest.TestCase):
    def test_records_stable_identities_and_duplicate_sources_without_mutating_plans(self) -> None:
        shadow = ValidationPlanShadow(
            skill_context_signature="skill-v1",
            workflow_source_signature="workflow-v1",
        )
        first = {"domains": [{"key": "hardware"}]}
        second = {"domains": [{"key": "hardware"}]}

        self.assertIs(
            shadow.record_plan(
                first,
                message="  检查 USB   设备  ",
                detail="full",
                scenario_id="hardware.usb.full",
                consumer="hardware_routing_semantics",
            ),
            first,
        )
        shadow.record_plan(
            second,
            message="检查 USB 设备",
            detail="full",
            scenario_id="audio.hardware.full",
            consumer="audio_routing_semantics",
        )

        payload = {"ok": False, "checks": [{"name": "existing", "ok": False}]}
        observed = shadow.attach(payload)
        self.assertEqual(payload, {"ok": False, "checks": [{"name": "existing", "ok": False}]})
        self.assertFalse(observed["ok"])
        result = observed["validation_shadow"]
        self.assertEqual(result["plan_build_count"], 2)
        self.assertEqual(result["unique_plan_identity_count"], 1)
        self.assertEqual(result["duplicate_plan_build_count"], 1)
        self.assertEqual(result["identity_incomplete_count"], 0)
        self.assertEqual([item["scenario_id"] for item in result["manifest"]], ["hardware.usb.full", "audio.hardware.full"] )
        self.assertEqual(result["manifest"][0]["source_owner"], "workflow_validation")
        self.assertEqual(result["manifest"][0]["plan_identity"], result["manifest"][1]["plan_identity"] )
        self.assertTrue(result["read_only"])
        self.assertFalse(result["enforcement"])
        self.assertFalse(result["cache_enabled"])

    def test_semantic_inputs_change_identity_and_display_only_plan_does_not(self) -> None:
        shadow = ValidationPlanShadow("skill-v1", "workflow-v1")
        for index, (message, detail) in enumerate((("检查工具", "full"), ("检查工具", "micro"), ("检查记忆", "full"))):
            shadow.record_plan(
                {"display": index},
                message=message,
                detail=detail,
                scenario_id=f"scenario.{index}",
                consumer="test",
            )
        result = shadow.attach({"ok": True})["validation_shadow"]
        self.assertEqual(result["unique_plan_identity_count"], 3)

    def test_duplicate_scenario_id_and_missing_signatures_fail_closed(self) -> None:
        shadow = ValidationPlanShadow("skill-v1", "workflow-v1")
        shadow.record_plan({}, message="a", detail="full", scenario_id="same", consumer="one")
        with self.assertRaisesRegex(ValueError, "validation_shadow_scenario_id_duplicate"):
            shadow.record_plan({}, message="a", detail="full", scenario_id="same", consumer="one")

        incomplete = ValidationPlanShadow("", "workflow-v1")
        incomplete.record_plan({}, message="a", detail="full", scenario_id="a", consumer="one")
        result = incomplete.attach({"ok": True})["validation_shadow"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["identity_incomplete_count"], 1)
        self.assertEqual(result["manifest"][0]["plan_identity"], "")
        self.assertEqual(result["reason"], "validation_shadow_identity_incomplete")

    def test_signature_for_value_is_order_stable_and_content_sensitive(self) -> None:
        self.assertEqual(signature_for_value({"b": 2, "a": 1}), signature_for_value({"a": 1, "b": 2}))
        self.assertEqual(signature_for_value({"values": {"b", "a"}}), signature_for_value({"values": {"a", "b"}}))
        self.assertNotEqual(signature_for_value({"a": 1}), signature_for_value({"a": 2}))
        self.assertEqual(signature_for_value(object()), "")


if __name__ == "__main__":
    unittest.main()
