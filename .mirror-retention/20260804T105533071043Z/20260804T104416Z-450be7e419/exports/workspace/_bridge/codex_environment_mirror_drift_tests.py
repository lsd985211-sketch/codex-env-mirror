#!/usr/bin/env python3

import unittest

import codex_environment_mirror_drift as drift


class CodexEnvironmentMirrorDriftTests(unittest.TestCase):
    def test_known_sources_route_to_existing_owners(self) -> None:
        payload = drift.build_drift_plan([
            {"code": "source_assets_changed", "source_id": "codex-global-agents", "sample": ["codex-global-agents"]},
            {"code": "source_assets_changed", "source_id": "codex-skills", "sample": ["codex-skills:global-framework/SKILL.md"]},
            {"code": "source_assets_missing", "source_id": "codex-native-memory-files", "sample": ["codex-native-memory-files:MEMORY.md"]},
        ])

        rows = {row["item_id"]: row for row in payload["rows"]}
        self.assertEqual(rows["codex-global-agents"]["owner"], "wsl_workspace_owner")
        self.assertEqual(rows["codex-global-agents"]["classification"], "projection_stale")
        self.assertEqual(rows["codex-skills:global-framework/SKILL.md"]["owner"], "skill_lifecycle_governance")
        self.assertEqual(rows["codex-skills:global-framework/SKILL.md"]["classification"], "source_update")
        self.assertEqual(rows["codex-native-memory-files:MEMORY.md"]["owner"], "memory_governance")
        self.assertEqual(rows["codex-native-memory-files:MEMORY.md"]["classification"], "private_state")
        self.assertFalse(payload["refresh_allowed"])

    def test_unknown_source_fails_closed(self) -> None:
        payload = drift.build_drift_plan([
            {"code": "source_assets_changed", "source_id": "mystery", "sample": ["mystery:item"]}
        ])

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["refresh_allowed"])
        self.assertEqual(payload["rows"][0]["classification"], "unknown")
        self.assertEqual(payload["rows"][0]["decision_status"], "blocked")
        self.assertTrue(payload["blockers"])

    def test_private_state_never_enters_public_mirror(self) -> None:
        payload = drift.build_drift_plan([
            {"code": "source_assets_changed", "source_id": "codex-native-memory-files", "sample": ["codex-native-memory-files:raw_memories.md"]}
        ])

        row = payload["rows"][0]
        self.assertTrue(row["blocker"])
        self.assertEqual(row["decision"], "owner_private_restore_review")
        self.assertNotIn("adopt_into_public_mirror", row["next_action"])

    def test_duplicate_issues_are_deduplicated_and_order_is_stable(self) -> None:
        first = {"code": "source_assets_changed", "source_id": "codex-skills", "sample": ["codex-skills:windows-codex-ops/SKILL.md"]}
        second = {"code": "source_assets_changed", "source_id": "codex-global-agents", "sample": ["codex-global-agents"]}

        left = drift.build_drift_plan([first, second, first])
        right = drift.build_drift_plan([second, first])

        self.assertEqual(left["row_count"], 2)
        self.assertEqual(left["plan_digest"], right["plan_digest"])
        self.assertEqual(left["rows"], right["rows"])

    def test_evidence_digests_are_projected_without_content(self) -> None:
        payload = drift.build_drift_plan(
            [{"code": "source_assets_changed", "source_id": "codex-global-agents", "sample": ["codex-global-agents"]}],
            evidence={
                "codex-global-agents": {
                    "path": "codex-home/AGENTS.md",
                    "work_git_digest": "a" * 64,
                    "approved_derived_digest": "b" * 64,
                    "current_projection_digest": "c" * 64,
                    "refs": ["snapshot:snapshot-1"],
                }
            },
        )

        row = payload["rows"][0]
        self.assertEqual(row["digests"]["work_git"], "a" * 64)
        self.assertEqual(row["digests"]["approved_derived"], "b" * 64)
        self.assertEqual(row["digests"]["current_projection"], "c" * 64)
        self.assertNotIn("content", row)

    def test_declared_owner_hint_routes_generic_source_without_unknown(self) -> None:
        payload = drift.build_drift_plan(
            [{"code": "source_assets_changed", "source_id": "workspace-bridge-source", "sample": ["workspace-bridge-source:owner.py"]}],
            owner_hints={
                "workspace-bridge-source": {
                    "owner": "system_membership",
                    "classification": "authority_export",
                    "kind": "tree",
                }
            },
        )

        row = payload["rows"][0]
        self.assertEqual(row["owner"], "system_membership")
        self.assertEqual(row["classification"], "source_update")
        self.assertEqual(row["decision_status"], "pending_review")
        self.assertTrue(row["blocker"])

    def test_reviewed_source_rows_require_allowed_disposition_and_owner_receipt(self) -> None:
        plan = drift.build_drift_plan(
            [{"code": "source_assets_changed", "source_id": "workspace-bridge-source", "sample": ["workspace-bridge-source:owner.py"]}],
            owner_hints={"workspace-bridge-source": {"owner": "system_membership"}},
        )
        blocked = drift.apply_reviewed_dispositions(
            plan, decisions={"source:workspace-bridge-source": "adopt_current_authority"}
        )
        self.assertFalse(blocked["refresh_allowed"])
        reviewed = drift.apply_reviewed_dispositions(
            plan,
            decisions={"source:workspace-bridge-source": "adopt_current_authority"},
            owner_receipts={"system_membership": "receipt:membership-ok"},
        )
        self.assertTrue(reviewed["refresh_allowed"])
        self.assertEqual(reviewed["rows"][0]["decision_status"], "consumed")

    def test_unknown_rows_cannot_be_reviewed_without_attribution(self) -> None:
        plan = drift.build_drift_plan([{"code": "source_assets_changed", "source_id": "unknown"}])
        reviewed = drift.apply_reviewed_dispositions(
            plan,
            decisions={"class:unknown": "adopt_current_authority"},
            owner_receipts={"unresolved": "receipt:any"},
        )
        self.assertFalse(reviewed["refresh_allowed"])
        self.assertEqual(reviewed["blockers"][0]["reason"], "classification_requires_owner_attribution")

    def test_mirror_control_plane_drift_routes_only_known_governance_codes(self) -> None:
        payload = drift.build_drift_plan([
            {"code": "control_plane_static_file_drift"},
            {"code": "governance_drift"},
        ])
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["owner"], "codex_environment_mirror")
        self.assertEqual(payload["rows"][0]["classification"], "source_update")
        unknown = drift.build_drift_plan([{"code": "unclassified_asset"}])
        self.assertEqual(unknown["rows"][0]["owner"], "unresolved")
        self.assertEqual(unknown["rows"][0]["classification"], "unknown")


if __name__ == "__main__":
    unittest.main()
