#!/usr/bin/env python3

import unittest
from unittest.mock import patch

from maintenance_capability_registry import (
    contract_fingerprint,
    global_coverage,
    infer_system,
    normalize_source_state,
    normalize_maintenance_contract,
)


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

    def test_cross_platform_hardware_owners_are_hardware_capabilities(self) -> None:
        for path in ("_bridge/hardware_system_owner.py", "_bridge/wsl_hardware_owner.py"):
            with self.subTest(path=path):
                self.assertEqual(infer_system(path, "Cross-platform hardware projection"), "hardware")

    def test_read_only_capability_gets_safe_derived_maintenance_contract(self) -> None:
        row = {
            "capability_id": "workflow-health",
            "system": "workflow",
            "module_path": "_bridge/workflow_health.py",
            "actions": ["doctor", "validate", "apply"],
            "read_only_actions": ["doctor", "validate"],
            "parser_signature": "parser-v1",
        }
        contract = normalize_maintenance_contract(row)
        self.assertEqual(contract["automation_level"], "A0")
        self.assertEqual(contract["effect_class"], "observe")
        self.assertEqual(contract["automatic_actions"], ["doctor", "validate"])
        self.assertEqual(contract["reverse_validation"], ["workflow-health:validate"])
        self.assertTrue(contract["derived"])

    def test_mutating_only_legacy_capability_is_manual_and_cannot_auto_apply(self) -> None:
        row = {
            "capability_id": "workflow-writer",
            "system": "workflow",
            "module_path": "_bridge/workflow_writer.py",
            "actions": ["apply"],
            "read_only_actions": [],
            "parser_signature": "parser-v1",
        }
        contract = normalize_maintenance_contract(row)
        self.assertEqual(contract["automation_level"], "A3")
        self.assertEqual(contract["automatic_actions"], [])
        self.assertEqual(contract["result_policy"], "approval_required")

    def test_parameterized_read_only_actions_are_not_inferred_as_automatic(self) -> None:
        row = {
            "capability_id": "workflow-status",
            "system": "workflow",
            "module_path": "_bridge/workflow_status.py",
            "actions": ["status", "plan", "query"],
            "read_only_actions": ["status", "plan", "query"],
            "parser_signature": "",
        }
        contract = normalize_maintenance_contract(row)
        self.assertEqual(contract["automatic_actions"], [])

    def test_owner_maintenance_contract_overrides_defaults_without_weakening_actions(self) -> None:
        row = {
            "capability_id": "scheduler-health",
            "system": "scheduler",
            "module_path": "_bridge/scheduler_health.py",
            "actions": ["snapshot", "validate"],
            "read_only_actions": ["snapshot", "validate"],
            "parser_signature": "parser-v2",
        }
        contract = normalize_maintenance_contract(
            row,
            {
                "signals": ["scheduler.task_drift"],
                "freshness_ttl_seconds": 60,
                "estimated_cost_ms": 250,
                "independent_group": "scheduler-observe",
            },
        )
        self.assertEqual(contract["signals"], ["scheduler.task_drift"])
        self.assertEqual(contract["freshness_ttl_seconds"], 60)
        self.assertEqual(contract["automatic_actions"], ["snapshot", "validate"])
        self.assertFalse(contract["derived"])
        self.assertEqual(len(contract_fingerprint(row, contract)), 64)

    def test_malformed_numeric_owner_metadata_fails_closed_without_crashing_registry(self) -> None:
        row = {
            "capability_id": "broken-owner",
            "system": "workflow",
            "module_path": "_bridge/broken_owner.py",
            "actions": ["validate"],
            "read_only_actions": ["validate"],
        }

        contract = normalize_maintenance_contract(
            row,
            {
                "automation_level": "A0",
                "automatic_actions": ["validate"],
                "freshness_ttl_seconds": "soon",
                "estimated_cost_ms": -5,
            },
        )

        self.assertFalse(contract["contract_valid"])
        self.assertEqual(contract["automation_level"], "A4")
        self.assertEqual(contract["automatic_actions"], [])
        self.assertEqual(
            contract["contract_errors"],
            ["freshness_ttl_seconds_invalid_integer", "estimated_cost_ms_below_minimum"],
        )

    def test_global_coverage_uses_active_system_identities_not_fixed_counts(self) -> None:
        rows = [
            {
                "capability_id": "workflow-health",
                "system": "workflow",
                "module_path": "_bridge/workflow_health.py",
                "actions": ["validate"],
                "read_only_actions": ["validate"],
                "maintenance": {"automation_level": "A0"},
            },
            {
                "capability_id": "scheduler-health",
                "system": "scheduler",
                "module_path": "_bridge/scheduler_health.py",
                "actions": ["validate"],
                "read_only_actions": ["validate"],
                "maintenance": {"automation_level": "A0"},
            },
        ]
        coverage = global_coverage(rows, active_systems=["workflow", "scheduler", "future-system"])
        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["unmapped_systems"], ["future-system"])
        self.assertEqual(coverage["covered_system_count"], 2)
        self.assertEqual(coverage["active_system_count"], 3)

    def test_global_coverage_reports_member_disposition_without_second_member_catalog(self) -> None:
        rows = [
            {
                "capability_id": "scheduler-health",
                "system": "scheduler",
                "module_path": "_bridge/maintenance_scheduler_service.py",
                "maintenance": {"automatic_actions": ["validate"], "member_ids": []},
            }
        ]
        coverage = global_coverage(
            rows,
            active_systems=["scheduler"],
            active_members=[
                {
                    "member_id": "scheduler.maintenance",
                    "system": "scheduler",
                    "owner": "maintenance_scheduler_service",
                    "lifecycle": "active",
                },
                {
                    "member_id": "scheduler.future",
                    "system": "scheduler",
                    "owner": "future_owner",
                    "lifecycle": "active",
                },
            ],
        )

        self.assertTrue(coverage["ok"])
        self.assertEqual(coverage["direct_member_coverage_count"], 1)
        self.assertEqual(coverage["inherited_member_coverage_count"], 1)
        self.assertEqual(coverage["underutilized_members"], ["scheduler.future"])

    def test_doctor_does_not_invent_empty_coverage_blocker_when_fixture_omits_coverage(self) -> None:
        with patch.object(
            __import__("maintenance_capability_registry"),
            "metrics",
            return_value={
                "capability_count": 1,
                "compact_map_within_budget": True,
                "surface_shard_count": 1,
                "declared_surface_count": 1,
                "contract_projection_mismatches": [],
                "duplicate_contracts": [],
                "owner_command_contract_errors": [],
                "index_exists": True,
                "index_fresh": True,
            },
        ):
            from maintenance_capability_registry import doctor

            result = doctor()

        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])

    def test_source_state_is_stable_for_same_path_static_and_dynamic_records(self) -> None:
        records = [("owner.py", 1, 0), ("owner.py", 20, 99), ("surface.md", 10, 42)]
        self.assertEqual(normalize_source_state(records), normalize_source_state(list(reversed(records))))
        self.assertEqual(
            normalize_source_state(records),
            (("owner.py", 1, 0), ("owner.py", 20, 99), ("surface.md", 10, 42)),
        )


if __name__ == "__main__":
    unittest.main()
