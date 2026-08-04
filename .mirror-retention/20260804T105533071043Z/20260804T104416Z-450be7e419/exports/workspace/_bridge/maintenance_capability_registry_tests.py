#!/usr/bin/env python3

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maintenance_capability_registry import (
    _path_state,
    create_registry_read_view,
    create_registry_snapshot,
    contract_fingerprint,
    global_coverage,
    infer_system,
    normalize_source_state,
    normalize_maintenance_contract,
    reconcile_projections,
    query_registry,
    query_registry_batch,
    query_row_summary,
)
from codex_config_guard import command_contract as codex_config_command_contract


class MaintenanceCapabilityRegistryTests(unittest.TestCase):
    def test_query_summary_preserves_owner_contract_evidence_without_command_body(self) -> None:
        row = {
            "capability_id": "ocr-runtime-owner",
            "system": "workflow",
            "module_path": "_bridge/ocr_runtime_owner.py",
            "surface": "OCR runtime",
            "owns": "status and health",
            "usual_entry": "python owner.py status",
            "actions": ["status", "validate"],
            "script_exists": True,
            "source_path": "_bridge/docs/maintenance_surfaces/workflow.md",
            "source_line": 42,
            "command_contract_source": "owner",
            "command_contract_error": "",
            "contract_fingerprint": "a" * 64,
            "action_commands": {"status": ["secret", "argument"]},
            "authorization_token": "must-not-project",
            "maintenance": {
                "risk_class": "read_only",
                "effect_class": "observe",
                "automation_level": "A0",
                "automatic_actions": ["validate"],
                "reverse_validation": ["ocr-runtime-owner:validate"],
                "contract_valid": True,
                "contract_errors": [],
            },
        }

        summary = query_row_summary(row)

        self.assertTrue(summary["candidate"]["admitted"])
        self.assertIsNone(summary["candidate"]["callable"])
        self.assertEqual(summary["contract_projection"]["contract_signature"], "a" * 64)
        self.assertEqual(summary["candidate"]["risk_class"], "read_only")
        self.assertEqual(summary["candidate"]["validation_ref"], "ocr-runtime-owner:validate")
        self.assertNotIn("action_commands", summary)
        self.assertNotIn("authorization_token", summary)

    def test_missing_or_drifted_owner_contract_fails_closed_in_summary(self) -> None:
        summary = query_row_summary(
            {
                "capability_id": "broken-owner",
                "system": "workflow",
                "module_path": "_bridge/broken_owner.py",
                "script_exists": True,
                "command_contract_source": "maintenance_surface",
                "command_contract_error": "owner_contract_command_failed",
                "contract_fingerprint": "",
                "maintenance": {"contract_valid": False, "contract_errors": ["signature_drift"]},
            }
        )

        self.assertFalse(summary["candidate"]["admitted"])
        self.assertEqual(summary["candidate"]["admission_state"], "observed_candidate")
        self.assertIn("valid_contract", summary["candidate"]["missing_requirements"])
        self.assertIn("owner_command_contract", summary["candidate"]["missing_requirements"])
        self.assertIn("owner_contract_unreadable", summary["candidate"]["risk_hints"])
    def test_batch_deduplicates_queries_and_scans_source_once(self) -> None:
        module = __import__("maintenance_capability_registry")
        module._cached_source_signature.cache_clear()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            module, "INDEX_PATH", Path(directory) / "missing.sqlite"
        ), patch.object(module, "_source_state", wraps=module._source_state) as source_state:
            batch = query_registry_batch(
                [
                    {"system": "resource", "term": "request handoff", "limit": 20},
                    {"system": "resource", "term": "request handoff", "limit": 20},
                ]
            )

        self.assertTrue(batch["ok"], batch)
        self.assertEqual(source_state.call_count, 1)
        self.assertEqual(batch["query_count"], 2)
        self.assertEqual(batch["unique_query_count"], 1)
        self.assertEqual(batch["deduplicated_count"], 1)
        self.assertEqual(batch["source_scan_count"], 1)
        self.assertEqual(batch["results"][0], batch["results"][1])
        self.assertEqual(batch["read_view"]["index_status"], "index_missing")
        module._cached_source_signature.cache_clear()

    def test_read_view_changes_when_source_content_changes(self) -> None:
        module = __import__("maintenance_capability_registry")
        module._cached_source_signature.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "surface.md"
            source.write_text("first", encoding="utf-8")
            with patch.object(module, "INDEX_PATH", Path(directory) / "missing.sqlite"), patch.object(
                module, "_source_state", side_effect=lambda: (module._path_state(source),)
            ), patch.object(module, "parse_surface_map", return_value=[]):
                first = create_registry_read_view()
                source.write_text("second", encoding="utf-8")
                second = create_registry_read_view()

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertNotEqual(first.source_signature, second.source_signature)
        module._cached_source_signature.cache_clear()

    def test_source_scan_failure_fails_closed(self) -> None:
        module = __import__("maintenance_capability_registry")
        with patch.object(module, "_source_state", side_effect=OSError("unreadable source")):
            batch = query_registry_batch([{"system": "workflow"}])

        self.assertFalse(batch["ok"])
        self.assertEqual(batch["reason"], "source_state_unavailable")
        self.assertEqual(batch["results"], [])

    def test_snapshot_rejects_source_change_during_row_freeze(self) -> None:
        module = __import__("maintenance_capability_registry")
        first = (("source", 1, "a"),)
        second = (("source", 1, "b"),)
        with patch.object(module, "_source_state", side_effect=[first, second]), patch.object(
            module, "parse_surface_map", return_value=[]
        ):
            frozen = create_registry_snapshot()
        self.assertFalse(frozen.ok)
        self.assertEqual("source_changed_during_snapshot", frozen.reason)
        self.assertFalse(frozen.source_stable)

    def test_snapshot_queries_do_not_rescan_or_reload_live_rows(self) -> None:
        module = __import__("maintenance_capability_registry")
        with patch.object(module, "INDEX_PATH", Path("/missing/index.sqlite")):
            frozen = create_registry_snapshot()
        self.assertTrue(frozen.ok, frozen.summary())
        with patch.object(module, "_source_state", side_effect=AssertionError("unexpected rescan")), patch.object(
            module, "_source_rows", side_effect=AssertionError("unexpected live rows")
        ):
            first = query_registry_batch([{"system": "resource", "term": "request"}], snapshot=frozen)
            first["results"][0]["items"].clear()
            second = query_registry_batch([{"system": "resource", "term": "request"}], snapshot=frozen)
        self.assertTrue(second["results"][0]["items"])
        self.assertEqual(0, second["source_scan_count"])
        self.assertEqual(2, second["snapshot"]["source_scan_count"])

    def test_invalid_snapshot_falls_back_to_live_read(self) -> None:
        module = __import__("maintenance_capability_registry")
        frozen = create_registry_snapshot()
        object.__setattr__(frozen, "ok", False)
        with patch.object(module, "create_registry_read_view", wraps=module.create_registry_read_view) as live:
            result = query_registry_batch([{"system": "workflow"}], snapshot=frozen)
        self.assertTrue(result["ok"])
        self.assertEqual(1, live.call_count)

    def test_single_query_is_the_batch_compatibility_projection(self) -> None:
        module = __import__("maintenance_capability_registry")
        with patch.object(module, "query_registry_batch", wraps=module.query_registry_batch) as batch_query:
            result = query_registry(system="resource", term="request", limit=3)

        self.assertEqual(batch_query.call_count, 1)
        self.assertEqual(result["filters"], {"system": "resource", "term": "request", "action": ""})
        self.assertIn("read_view", result)

    def test_stale_resource_index_uses_tokenized_source_query(self) -> None:
        module = __import__("maintenance_capability_registry")
        module._cached_source_rows.cache_clear()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            module, "INDEX_PATH", Path(directory) / "missing.sqlite"
        ):
            result = query_registry(
                system="resource",
                term="request handoff consume retry",
                limit=20,
            )
        self.assertTrue(result["ok"])
        self.assertGreater(result["returned"], 0)
        self.assertEqual(result["returned"], len(result["items"]))
        self.assertNotIn("compression_blocked", result)
        self.assertEqual(result["source"], "maintenance_surface_shard_fallback")
        self.assertEqual(result["authority_status"], "source_shard_authoritative")
        self.assertTrue(result["derived_refresh_recommended"])
        self.assertEqual(result["query_tokens"], ["request", "handoff", "consume", "retry"])
        module._cached_source_rows.cache_clear()

    def test_codex_config_owner_contract_binds_startup_core_member(self) -> None:
        contract = codex_config_command_contract()

        self.assertTrue(contract["ok"])
        self.assertIn("codex-startup-and-provider.core", contract["maintenance"]["member_ids"])
        self.assertEqual(contract["maintenance"]["automation_level"], "A0")
        self.assertEqual(contract["maintenance"]["automatic_actions"], ["snapshot", "doctor", "validate", "metrics"])

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
                "timeout_seconds": 45,
                "independent_group": "scheduler-observe",
            },
        )
        self.assertEqual(contract["signals"], ["scheduler.task_drift"])
        self.assertEqual(contract["freshness_ttl_seconds"], 60)
        self.assertEqual(contract["timeout_seconds"], 45)
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
                "timeout_seconds": 0,
            },
        )

        self.assertFalse(contract["contract_valid"])
        self.assertEqual(contract["automation_level"], "A4")
        self.assertEqual(contract["automatic_actions"], [])
        self.assertEqual(
            contract["contract_errors"],
            [
                "freshness_ttl_seconds_invalid_integer",
                "estimated_cost_ms_below_minimum",
                "timeout_seconds_below_minimum",
            ],
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
        records = [("owner.py", 1, "exists"), ("owner.py", 20, "digest"), ("surface.md", 10, "surface")]
        self.assertEqual(normalize_source_state(records), normalize_source_state(list(reversed(records))))
        self.assertEqual(
            normalize_source_state(records),
            (("owner.py", 1, "exists"), ("owner.py", 20, "digest"), ("surface.md", 10, "surface")),
        )

    def test_path_state_uses_content_identity_instead_of_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "surface.md"
            path.write_text("same content", encoding="utf-8")
            before = _path_state(path)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            self.assertNotEqual(stat.st_mtime_ns, path.stat().st_mtime_ns)
            self.assertEqual(before, _path_state(path))
            path.write_text("changed content", encoding="utf-8")
            self.assertNotEqual(before, _path_state(path))

    def test_reconcile_projects_authority_counts_and_rebuilds_index(self) -> None:
        module = __import__("maintenance_capability_registry")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            shards = docs / "maintenance_surfaces"
            runtime = root / "runtime"
            shards.mkdir(parents=True)
            runtime.mkdir()
            shard = shards / "workflow.md"
            shard.write_text(
                "# workflow\n\n| Surface | Owns | Non-goals | Entry |\n"
                "| --- | --- | --- | --- |\n"
                "| `owner.py` | Owns | None | `validate` |\n"
                "| `peer.py` | Owns | None | `snapshot` |\n",
                encoding="utf-8",
            )
            surface_index = shards / "index.json"
            surface_index.write_text(
                json.dumps(
                    {
                        "schema": "maintenance_surface_index.v1",
                        "systems": [
                            {
                                "system": "workflow",
                                "path": "maintenance_surfaces/workflow.md",
                                "terms": ["workflow"],
                                "contract_count": 1,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            surface_map = docs / "maintenance_surface_map.md"
            surface_map.write_text(
                "| System | Contract shard | Contracts |\n"
                "| --- | --- | ---: |\n"
                "| `workflow` | [`maintenance_surfaces/workflow.md`](maintenance_surfaces/workflow.md) | 1 |\n",
                encoding="utf-8",
            )
            with (
                patch.object(module, "ROOT", root),
                patch.object(module, "BRIDGE", root),
                patch.object(module, "MAP_PATH", surface_map),
                patch.object(module, "SURFACE_DIR", shards),
                patch.object(module, "SURFACE_INDEX_PATH", surface_index),
                patch.object(module, "INDEX_PATH", runtime / "maintenance.sqlite"),
            ):
                result = reconcile_projections(apply=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["projection_mismatches_after"], [])
            self.assertEqual(json.loads(surface_index.read_text(encoding="utf-8"))["systems"][0]["contract_count"], 2)
            self.assertIn("| 2 |", surface_map.read_text(encoding="utf-8"))
            self.assertTrue((runtime / "maintenance.sqlite").is_file())


if __name__ == "__main__":
    unittest.main()
