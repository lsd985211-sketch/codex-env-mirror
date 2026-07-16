from __future__ import annotations

import unittest
from unittest.mock import patch

from codex_workflow_entry import select_self_update_owners
from self_update_governance import (
    OWNER_SPECS,
    build_change_set,
    doctor,
    owner_review_items,
    snapshot,
    stale_signals,
    validation_receipt_index,
)


class OwnerReviewItemsTests(unittest.TestCase):
    def test_closeout_owner_selection_is_surface_scoped(self) -> None:
        self.assertEqual(
            select_self_update_owners(
                changed_surfaces=["resource-network"],
                task_kind="maintenance",
                outcome="ok",
                major_change=False,
            ),
            ["network_gateway", "resource_broker", "resource_strategy", "resource_process"],
        )
        self.assertEqual(
            select_self_update_owners(
                changed_surfaces=["startup", "config"],
                task_kind="startup-maintenance",
                outcome="ok",
                major_change=False,
            ),
            ["config_guard", "config_projection", "session_store"],
        )

    def test_owner_catalog_is_derived_for_all_registered_systems(self) -> None:
        systems = {str(spec.get("system")) for spec in OWNER_SPECS.values()}
        self.assertTrue(
            {
                "bridge",
                "drafts",
                "mail",
                "mcp",
                "memory",
                "network",
                "office",
                "records",
                "resource",
                "skills",
                "startup",
                "workflow",
            }.issubset(systems)
        )
        self.assertIn("system_membership", OWNER_SPECS)
        self.assertIn("rule_governance", OWNER_SPECS)

    def test_changed_file_builds_stable_dependency_aware_change_set(self) -> None:
        first = build_change_set(
            changed_files=["_bridge/self_update_governance.py"],
            task_kind="maintenance_governance",
            outcome="ok",
        )
        second = build_change_set(
            changed_files=["_bridge/self_update_governance.py"],
            task_kind="maintenance_governance",
            outcome="ok",
        )

        self.assertEqual(first["change_id"], second["change_id"])
        self.assertIn("workflow", first["affected_systems"])
        self.assertIn("system_membership", first["selected_owners"])
        self.assertIn("rule_governance", first["selected_owners"])
        domain_steps = [item for item in first["owner_steps"] if item["phase"] == "domain_validation"]
        self.assertTrue(domain_steps)
        self.assertTrue(all("owner:system_membership" in item["depends_on"] for item in domain_steps))
        self.assertTrue(all("owner:rule_governance" in item["depends_on"] for item in domain_steps))

    def test_targeted_snapshot_does_not_invent_unselected_owner_failures(self) -> None:
        payload = snapshot(
            selected_owners=["workflow"],
            validation_receipts=[
                {
                    "owner": "workflow",
                    "ok": True,
                    "status": "validated",
                    "payload": {"ok": True, "status": "ok"},
                }
            ],
        )

        self.assertEqual(list(payload["owners"]), ["workflow"])
        self.assertEqual(payload["selection"]["mode"], "targeted")
        self.assertEqual(payload["selection"]["receipt_reuse_count"], 1)
        self.assertEqual(payload["signals"], [])
        self.assertTrue(payload["change_set"]["change_id"].startswith("evo-"))
        self.assertIn("wall_elapsed_ms", payload["selection"])

    def test_receipt_aliases_reuse_existing_codex_owner_evidence(self) -> None:
        receipts = validation_receipt_index(
            ["codex_config_guard=ok", "codex_config_projection=ok", "workflow_orchestrator=ok"]
        )
        self.assertEqual(set(receipts), {"config_guard", "config_projection", "workflow"})

    def test_validated_membership_meta_change_uses_specific_changed_file_closure(self) -> None:
        payload = build_change_set(
            changed_files=["_bridge/system_membership.py", "_bridge/codex_config_projection.py"],
            task_kind="config_governance",
            outcome="ok",
            config_changed=True,
            validated_owners=["system_membership"],
        )
        self.assertIn("startup", payload["affected_systems"])
        self.assertIn("workflow", payload["affected_systems"])
        self.assertNotIn("bridge", payload["affected_systems"])
        self.assertTrue(payload["impact"]["membership"]["narrowed_by_authority_receipt"])

    def test_only_live_owner_execution_is_authoritative_for_queue_reconciliation(self) -> None:
        receipt_payload = doctor(
            selected_owners=["workflow"],
            validation_receipts=[{"owner": "workflow", "ok": True, "status": "validated"}],
        )
        self.assertEqual(receipt_payload["authoritative_owners"], [])

        with patch(
            "self_update_governance.run_owner",
            return_value={
                "name": "workflow",
                "ok": True,
                "execution_state": "ok",
                "returncode": 0,
                "command": "workflow_orchestrator.py validate",
                "payload": {"ok": True},
                "stderr_tail": "",
            },
        ):
            live_payload = doctor(selected_owners=["workflow"])
        self.assertEqual(live_payload["authoritative_owners"], ["workflow"])

    def test_resource_process_failures_become_concrete_review_items(self) -> None:
        items = owner_review_items(
            "resource_process",
            {
                "failures": [
                    {
                        "severity": "risk",
                        "code": "mcp_session_multiplication_pressure",
                        "message": "Configured MCP process chains consume 27 root instances.",
                        "root_instance_count": 27,
                        "working_set_mb": 1369.2,
                        "warn_budget": {"roots": 12, "working_set_mb": 300.0},
                        "risk_budget": {"roots": 24, "working_set_mb": 600.0},
                        "manual_action": "Restart Codex Desktop, then re-run the owner validator.",
                    }
                ]
            },
            "Use the owner repair entrypoint.",
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source_item_id"], "self_update:resource_process:mcp_session_multiplication_pressure")
        self.assertIn("27 root instances", item["summary"])
        self.assertEqual(item["approval_action"], "Restart Codex Desktop, then re-run the owner validator.")
        self.assertEqual(item["attributes"]["root_instance_count"], 27)
        self.assertEqual(item["attributes"]["working_set_mb"], 1369.2)
        self.assertEqual(item["attributes"]["risk_budget"]["roots"], 24)

    def test_same_code_different_profiles_keep_distinct_stable_ids(self) -> None:
        payload = {
            "failures": [
                {
                    "severity": "risk",
                    "code": "mcp_orphaned_stdio_host_chain",
                    "profile": "filesystem_admin_mcp",
                    "message": "Admin profile has one orphaned root.",
                },
                {
                    "severity": "risk",
                    "code": "mcp_orphaned_stdio_host_chain",
                    "profile": "filesystem_mcp",
                    "message": "Read-only profile has one orphaned root.",
                },
            ]
        }

        first = owner_review_items("resource_process", payload, "Use owner repair-plan.")
        second = owner_review_items("resource_process", payload, "Use owner repair-plan.")

        self.assertEqual([item["source_item_id"] for item in first], [item["source_item_id"] for item in second])
        self.assertEqual(len({item["source_item_id"] for item in first}), 2)
        self.assertEqual(first[0]["attributes"]["profile"], "filesystem_admin_mcp")

    def test_transport_failure_is_diagnostic_not_domain_defect(self) -> None:
        signals = stale_signals(
            {
                "memory": {
                    "ok": False,
                    "execution_state": "transport_failure",
                    "error": "owner timed out",
                    "payload": {},
                },
                "workflow": {
                    "ok": False,
                    "execution_state": "parse_failure",
                    "payload": {"error": "invalid json"},
                },
            }
        )

        workflow = next(item for item in signals if item["surface"] == "workflow")
        self.assertEqual(workflow["code"], "owner_evidence_unavailable")
        self.assertEqual(workflow["execution_state"], "parse_failure")
        self.assertEqual(workflow["review_items"], [])
        self.assertFalse(any(item.get("code") == "memory_governance_not_ok" for item in signals))

    def test_owner_payload_preserves_concrete_issue(self) -> None:
        signals = stale_signals(
            {
                "resource_process": {
                    "ok": False,
                    "execution_state": "owner_reported_failure",
                    "payload": {
                        "failures": [
                            {
                                "code": "resource_process_fanout",
                                "message": "Four stale roots remain.",
                            }
                        ]
                    },
                }
            }
        )

        signal = next(item for item in signals if item["surface"] == "resource_process")
        self.assertEqual(signal["code"], "owner_reported_issues")
        self.assertEqual(len(signal["review_items"]), 1)
        self.assertIn("Four stale roots", signal["review_items"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
