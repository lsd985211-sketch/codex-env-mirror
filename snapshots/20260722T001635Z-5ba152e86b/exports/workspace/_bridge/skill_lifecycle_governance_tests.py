#!/usr/bin/env python3
"""Regression tests for skill lifecycle and global-root governance."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _bridge import skill_admission_discover as admission
from _bridge import skill_active_catalog
from _bridge import skill_lifecycle_governance as lifecycle
from _bridge import skill_lifecycle_state
from _bridge import skill_orchestrator
from _bridge.shared import backup_router


class SkillLifecycleGovernanceTests(unittest.TestCase):
    def test_configured_plugin_catalog_ignores_cache_backups_and_disabled_plugins(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            cache = root / "cache"
            config.write_text(
                '[plugins."chrome@openai-bundled"]\nenabled = true\n\n'
                '[plugins."disabled@openai-bundled"]\nenabled = false\n',
                encoding="utf-8",
            )
            latest = cache / "openai-bundled" / "chrome" / "latest" / "skills" / "control-chrome" / "SKILL.md"
            old = cache / "openai-bundled" / "chrome" / "26.707.72221" / "skills" / "control-chrome" / "SKILL.md"
            backup = cache / "openai-bundled" / "plugin-backup-old" / "chrome" / "26.1" / "skills" / "control-chrome" / "SKILL.md"
            disabled = cache / "openai-bundled" / "disabled" / "1" / "skills" / "disabled" / "SKILL.md"
            for path in (latest, old, backup, disabled):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\nname: test-skill\ndescription: Test skill.\n---\n", encoding="utf-8")

            active = skill_active_catalog.discover_active_plugin_skill_files(plugin_cache=cache, config_path=config)
            self.assertEqual(active, [latest])
            snapshot = skill_active_catalog.catalog_snapshot(plugin_cache=cache, config_path=config)
            self.assertTrue(snapshot["ok"])
            self.assertEqual(snapshot["active_skill_count"], 1)

    def test_default_metadata_manifest_stays_bounded_without_disabling_skills(self) -> None:
        records = [
            {"name": "global-framework", "source": "user", "description": "x" * 200, "path": "global"},
            {"name": "pdf", "source": "user", "description": "x" * 200, "path": "pdf"},
            {"name": "rare-skill", "source": "user", "description": "x" * 20_000, "path": "rare"},
        ]
        budget = lifecycle.default_metadata_budget(records)
        self.assertTrue(budget["within_budget"])
        self.assertEqual(budget["default_skill_count"], 2)
        self.assertEqual(budget["deferred_skill_count"], 1)

    def test_declared_wsl_scope_allows_python3_without_unix_portability_flag(self) -> None:
        with TemporaryDirectory() as directory:
            skill = Path(directory) / "wsl-tool" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: wsl-tool\ndescription: WSL-only owner helper.\n"
                'metadata: {"codex":{"platform_scope":"wsl_work_git"}}\n---\n'
                "```bash\npython3 _bridge/owner.py validate\n```\n",
                encoding="utf-8",
            )
            record = lifecycle.skill_record(skill, "user")
        self.assertEqual(record["platform_scope"], "wsl_work_git")
        self.assertNotIn("unix_only_command", record["flags"])

    def test_generic_layered_selection_limits_execution_but_honors_explicit_multi_skill_requests(self) -> None:
        candidates = [
            {"name": "workspace-knowledge", "layer": "context", "reasons": []},
            {"name": "mcsmanager-fabric-mc", "layer": "execution", "reasons": []},
            {"name": "mc-mod-automation", "layer": "execution", "reasons": []},
        ]
        selected = skill_orchestrator.select_layered_candidates(candidates, 4)
        self.assertEqual([row["name"] for row in selected], ["workspace-knowledge", "mcsmanager-fabric-mc"])

        explicit = [
            {"name": "pdf", "layer": "execution", "reasons": ["name_mentioned"]},
            {"name": "xlsx", "layer": "execution", "reasons": ["name_mentioned"]},
        ]
        self.assertEqual(skill_orchestrator.select_layered_candidates(explicit, 4), explicit)

    def test_global_root_is_only_user_skill_source(self) -> None:
        report = lifecycle.audit()
        self.assertEqual(report["workspace_skill_entries"], [])
        self.assertFalse(report["backup_pollution"])
        self.assertFalse(report["active_local_collisions"])

    def test_code_graph_domains_select_the_matching_graph_skill(self) -> None:
        gitnexus_domains = skill_orchestrator.classify("GitNexus semantic code execution flow")
        graphify_domains = skill_orchestrator.classify("Graphify managed graph review delta")

        self.assertIn("gitnexus_semantic_graph", {item["domain"].key for item in gitnexus_domains})
        self.assertIn("graphify_knowledge_graph", {item["domain"].key for item in graphify_domains})

    def test_hardware_domain_is_bound_to_its_member_system(self) -> None:
        domains = skill_orchestrator.classify("Huawei tablet MTP is busy while WSL USB/IP needs attachment")
        hardware = next(item for item in domains if item["domain"].key == "hardware")

        self.assertIn("hardware", hardware["systems"])
        self.assertEqual(hardware["domain"].preferred_skills[0], "hardware-ops")
        self.assertIn("windows-usb-ops", hardware["domain"].preferred_skills)

    def test_short_usage_window_never_authorizes_retirement(self) -> None:
        quality = {
            "record_count": 4,
            "first_recorded_at": "2026-01-01T00:00:00+00:00",
            "last_recorded_at": "2026-01-02T00:00:00+00:00",
            "skills": {
                "alpha": {"selected": 1, "applied": 1},
                "beta": {"selected": 1, "applied": 0},
            },
        }
        with mock.patch.object(lifecycle.skill_lifecycle_state, "quality_summary", return_value=quality):
            usage = lifecycle.usage_window()
        self.assertEqual(usage["record_count"], 4)
        self.assertEqual(usage["unique_selected_count"], 2)
        self.assertEqual(usage["unique_used_count"], 1)
        self.assertFalse(usage["retirement_evidence_sufficient"])

    def test_v1_state_database_migrates_additively(self) -> None:
        with TemporaryDirectory() as directory:
            state_db = Path(directory) / "state.sqlite"
            connection = sqlite3.connect(state_db)
            connection.execute(
                "CREATE TABLE skill_state(path TEXT PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL, "
                "stat_fingerprint TEXT NOT NULL, content_sha256 TEXT NOT NULL, status TEXT NOT NULL, "
                "first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_changed_at TEXT NOT NULL, "
                "removed_at TEXT NOT NULL DEFAULT '', record_json TEXT NOT NULL)"
            )
            connection.commit()
            connection.close()

            migrated = skill_lifecycle_state.connect(state_db)
            columns = {row[1] for row in migrated.execute("PRAGMA table_info(skill_state)")}
            tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = migrated.execute(
                "SELECT value FROM lifecycle_meta WHERE key='schema_version'"
            ).fetchone()[0]
            migrated.close()

            self.assertTrue({"skill_id", "admission_state", "trust_state"}.issubset(columns))
            self.assertTrue({"skill_quality_event", "skill_lineage"}.issubset(tables))
            self.assertEqual(version, str(skill_lifecycle_state.SCHEMA_VERSION))

    def test_registry_preserves_identity_after_unambiguous_move(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            snapshots.mkdir()
            old_path = root / "old" / "alpha"
            new_path = root / "new" / "alpha"
            fingerprints = {"skill_md": "one", "agents": None, "scripts": None, "references": None, "assets": None}
            existing = {
                "skills": [{
                    "skill_id": "stable-alpha",
                    "name": "alpha",
                    "path": str(old_path),
                    "source": "user-managed",
                    "state": "approved",
                    "fingerprints": fingerprints,
                }]
            }
            with (
                mock.patch.object(admission, "SNAPSHOT_DIR", snapshots),
                mock.patch.object(admission, "fingerprint_skill", return_value=fingerprints),
                mock.patch.object(admission, "extract_declared_primary_layer", return_value=""),
            ):
                updated, _ = admission.update_registry(
                    existing,
                    [admission.SkillRecord(name="alpha", path=new_path, source="user-managed")],
                )
            self.assertEqual(updated["skills"][0]["skill_id"], "stable-alpha")
            self.assertEqual(updated["skills"][0]["state"], "approved")

    def test_registry_does_not_guess_identity_for_ambiguous_move(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            snapshots.mkdir()
            new_path = root / "new" / "alpha"
            fingerprints = {"skill_md": "one", "agents": None, "scripts": None, "references": None, "assets": None}
            existing = {
                "skills": [
                    {"skill_id": "alpha-one", "name": "alpha", "path": str(root / "one"), "source": "user-managed", "state": "approved", "fingerprints": fingerprints},
                    {"skill_id": "alpha-two", "name": "alpha", "path": str(root / "two"), "source": "user-managed", "state": "approved", "fingerprints": fingerprints},
                ]
            }
            with (
                mock.patch.object(admission, "SNAPSHOT_DIR", snapshots),
                mock.patch.object(admission, "fingerprint_skill", return_value=fingerprints),
                mock.patch.object(admission, "extract_declared_primary_layer", return_value=""),
            ):
                updated, _ = admission.update_registry(
                    existing,
                    [admission.SkillRecord(name="alpha", path=new_path, source="user-managed")],
                )
            self.assertEqual(
                updated["skills"][0]["skill_id"],
                admission.build_skill_id("user-managed", new_path),
            )

    def test_admission_trust_mapping_and_routing_gate(self) -> None:
        self.assertEqual(lifecycle.trust_state_for({"state": "approved"}, "user"), ("approved", "trusted"))
        self.assertEqual(lifecycle.trust_state_for({"state": "audit-pending"}, "user"), ("audit-pending", "provisional"))
        self.assertEqual(lifecycle.trust_state_for(None, "system"), ("unregistered", "managed"))

        base = {"source": "user", "routing_eligible": True, "routing_block_reason": ""}
        pending = lifecycle.apply_admission(base, {"skill_id": "alpha", "state": "audit-pending"})
        rejected = lifecycle.apply_admission(base, {"skill_id": "alpha", "state": "rejected"})
        self.assertTrue(pending["routing_eligible"])
        self.assertEqual(pending["trust_state"], "provisional")
        self.assertFalse(rejected["routing_eligible"])
        self.assertEqual(rejected["routing_block_reason"], "admission_rejected")

    def test_quality_events_are_idempotent_and_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            state_db = Path(directory) / "state.sqlite"
            event = {
                "event_key": "event:alpha:selected",
                "occurred_at": "2026-01-01T00:00:00+00:00",
                "skill_name": "alpha",
                "event_kind": "selected",
            }
            first = skill_lifecycle_state.record_quality_events([event], path=state_db)
            second = skill_lifecycle_state.record_quality_events([event], path=state_db)
            invalid = skill_lifecycle_state.record_quality_events(
                [{**event, "event_key": "event:bad", "event_kind": "arbitrary"}],
                path=state_db,
            )
            self.assertEqual(first["inserted_count"], 1)
            self.assertEqual(second["duplicate_count"], 1)
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"], "invalid_event_kind")

    def test_quality_only_breaks_equal_business_scores(self) -> None:
        inventory = {
            "alpha": {"name": "alpha", "path": "alpha/SKILL.md", "source": "local"},
            "beta": {"name": "beta", "path": "beta/SKILL.md", "source": "local"},
        }
        refresh = {
            "records": [
                {"name": "alpha", "source": "user", "routing_eligible": True},
                {"name": "beta", "source": "user", "routing_eligible": True},
            ],
            "summary": {},
            "state_db": "state.sqlite",
        }
        quality = {
            "skills": {
                "alpha": {"ranking_signal": -2},
                "beta": {"ranking_signal": 2},
            }
        }

        def equal_score(skill: dict[str, object], *_: object) -> tuple[int, list[str]]:
            return 10, [str(skill["name"])]

        def unequal_score(skill: dict[str, object], *_: object) -> tuple[int, list[str]]:
            return (11 if skill["name"] == "alpha" else 10), [str(skill["name"])]

        common_patches = (
            mock.patch.object(skill_orchestrator.skill_lifecycle, "refresh_incremental", return_value=refresh),
            mock.patch.object(skill_orchestrator, "merged_inventory", return_value=inventory),
            mock.patch.object(skill_orchestrator, "classify", return_value=[]),
            mock.patch.object(skill_orchestrator, "detect_gaps", return_value=[]),
            mock.patch.object(skill_orchestrator.skill_lifecycle_state, "quality_summary", return_value=quality),
        )
        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4], mock.patch.object(skill_orchestrator, "score_skill", side_effect=equal_score):
            equal_plan = skill_orchestrator.build_plan("test", max_skills=2)
        with (
            mock.patch.object(skill_orchestrator.skill_lifecycle, "refresh_incremental", return_value=refresh),
            mock.patch.object(skill_orchestrator, "merged_inventory", return_value=inventory),
            mock.patch.object(skill_orchestrator, "classify", return_value=[]),
            mock.patch.object(skill_orchestrator, "detect_gaps", return_value=[]),
            mock.patch.object(skill_orchestrator.skill_lifecycle_state, "quality_summary", return_value=quality),
            mock.patch.object(skill_orchestrator, "score_skill", side_effect=unequal_score),
        ):
            unequal_plan = skill_orchestrator.build_plan("test", max_skills=2)
        self.assertEqual([row["name"] for row in equal_plan["selected_skills"]], ["beta"])
        self.assertEqual([row["name"] for row in unequal_plan["selected_skills"]], ["alpha"])

    def test_routing_context_reuses_one_lifecycle_refresh_for_message_batch(self) -> None:
        refresh = {"records": [], "summary": {}, "state_db": "state.sqlite"}
        with (
            mock.patch.object(skill_orchestrator.skill_lifecycle, "refresh_incremental", return_value=refresh) as refresh_mock,
            mock.patch.object(skill_orchestrator, "merged_inventory", return_value={}),
            mock.patch.object(skill_orchestrator, "resolved_myskills_inventory_path", return_value=None),
            mock.patch.object(skill_orchestrator.skill_lifecycle_state, "quality_summary", return_value={"skills": {}}),
            mock.patch.object(skill_orchestrator, "classify", return_value=[]),
            mock.patch.object(skill_orchestrator, "detect_gaps", return_value=[]),
        ):
            context = skill_orchestrator.prepare_routing_context()
            first = skill_orchestrator.build_plan("first", routing_context=context)
            second = skill_orchestrator.build_plan("second", routing_context=context)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(refresh_mock.call_count, 1)

    def test_invalid_lineage_kind_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            result = skill_lifecycle_state.record_lineage(
                {
                    "lineage_key": "alpha:rewrite",
                    "skill_name": "alpha",
                    "evolution_kind": "REWRITE",
                    "reason": "test",
                },
                path=Path(directory) / "state.sqlite",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_evolution_kind")

    def test_legacy_usage_migration_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            usage_log = root / "skill_usage.jsonl"
            state_db = root / "state.sqlite"
            usage_log.write_text(
                json.dumps({
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                    "task_kind": "test",
                    "selected_skills": ["alpha"],
                    "used_skills": ["alpha"],
                    "outcome": "ok",
                }) + "\n",
                encoding="utf-8",
            )
            original = skill_lifecycle_state.record_quality_events

            def write_temp(events: object) -> dict[str, object]:
                return original(events, path=state_db)

            with (
                mock.patch.object(skill_orchestrator, "USAGE_LOG", usage_log),
                mock.patch.object(skill_orchestrator.skill_lifecycle_state, "record_quality_events", side_effect=write_temp),
            ):
                first = skill_orchestrator.migrate_legacy_usage()
                second = skill_orchestrator.migrate_legacy_usage()
            self.assertGreater(first["inserted_count"], 0)
            self.assertEqual(second["inserted_count"], 0)
            self.assertGreater(second["duplicate_count"], 0)

    def test_pdf_user_skill_is_retained_when_plugin_is_not_portable(self) -> None:
        report = lifecycle.audit()["pdf_capability"]
        self.assertEqual(report["decision"], "retain_both")
        self.assertFalse(report["safe_to_disable_user_skill"])

    def test_user_plugin_name_overlap_is_resolved_by_namespacing(self) -> None:
        collisions = lifecycle.audit()["cross_source_collisions"]
        pdf = next(row for row in collisions if row["name"] == "pdf")
        self.assertTrue(pdf["resolved"])
        self.assertEqual(pdf["resolution"], "user_primary_plugin_namespaced")

    def test_exact_duplicate_content_is_reported_separately(self) -> None:
        report = lifecycle.audit()
        self.assertEqual(report["exact_content_duplicates"], [])
        self.assertTrue(all(row["line_count"] > lifecycle.RECOMMENDED_SKILL_LINE_LIMIT for row in report["oversized_candidates"]))

    def test_unreferenced_full_guide_is_detected(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "guide-owner" / "SKILL.md"
            guide = skill_file.parent / "references" / "full-guide.md"
            guide.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: guide-owner\ndescription: Test guide owner.\n---\n", encoding="utf-8")
            guide.write_text("# Full guide\n", encoding="utf-8")
            record = lifecycle.skill_record(skill_file, "user")
            self.assertIn("unreferenced_full_guide", record["flags"])

    def test_scenario_alias_plan_ignores_empty_legacy_scenarios(self) -> None:
        snapshot = lifecycle.scenario_snapshot()
        self.assertEqual(snapshot["alias_count"], 0)

    def test_backup_router_keeps_skill_backups_outside_active_root(self) -> None:
        plan = backup_router.plan(
            [str(lifecycle.GLOBAL_SKILLS / "global-framework" / "SKILL.md")],
            category="skill-governance",
            remark="test",
        )
        target = Path(plan["items"][0]["backup_dir"]).resolve()
        self.assertFalse(str(target).lower().startswith(str(lifecycle.GLOBAL_SKILLS.resolve()).lower()))
        self.assertFalse(str(target).lower().startswith(str(lifecycle.ROOT.resolve()).lower()))

    def test_apply_requires_confirmation(self) -> None:
        result = lifecycle.apply_approved(["archive-backup-root:any"], confirm_apply=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirm_apply_required")

    def test_nested_script_reference_resolves(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skill_file = root / "SKILL.md"
            script = root / "ooxml" / "scripts" / "unpack.py"
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            skill_file.write_text("", encoding="utf-8")
            self.assertTrue(lifecycle.script_reference_resolves(skill_file, "ooxml/scripts/unpack.py"))

    def test_external_owner_variable_is_not_reported_missing(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "SKILL.md"
            skill_file.write_text("", encoding="utf-8")
            self.assertTrue(lifecycle.script_reference_resolves(skill_file, "${OWNER_SKILL_DIR}/scripts/main.ts"))

    def test_missing_implementation_directory_blocks_routing(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "missing-owner" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: missing-owner\ndescription: Test missing implementation.\n---\nRun scripts/main.py", encoding="utf-8")
            record = lifecycle.skill_record(skill_file, "user")
            self.assertFalse(record["routing_eligible"])
            self.assertEqual(record["routing_block_reason"], "missing_local_implementation")

    def test_invalid_skill_contract_blocks_routing(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "invalid-owner" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: invalid-owner\n---\n", encoding="utf-8")
            record = lifecycle.skill_record(skill_file, "user")
            self.assertFalse(record["routing_eligible"])
            self.assertEqual(record["routing_block_reason"], "invalid_skill_contract")
            self.assertIn("missing_description", record["contract_errors"])

    def test_missing_declared_environment_blocks_routing(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "vault-owner" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text(
                '---\nname: vault-owner\ndescription: Test required environment.\nmetadata: {"codex":{"required_env":["UNSET_SKILL_TEST_ROOT"]}}\n---\n',
                encoding="utf-8",
            )
            record = lifecycle.skill_record(skill_file, "user")
            self.assertFalse(record["routing_eligible"])
            self.assertEqual(record["missing_required_env"], ["UNSET_SKILL_TEST_ROOT"])

    def test_superseded_skill_is_not_routed(self) -> None:
        with TemporaryDirectory() as directory:
            skill_file = Path(directory) / "legacy-owner" / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text(
                '---\nname: legacy-owner\ndescription: Test superseded skill.\nmetadata: {"codex":{"superseded_by":"current-owner"}}\n---\n',
                encoding="utf-8",
            )
            record = lifecycle.skill_record(skill_file, "user")
            self.assertFalse(record["routing_eligible"])
            self.assertEqual(record["superseded_by"], "current-owner")

    def test_tree_fingerprint_tracks_bundled_resource_changes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "tracked-skill"
            skill_file = root / "SKILL.md"
            reference = root / "references" / "guide.md"
            reference.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: tracked-skill\ndescription: Test tree tracking.\n---\n", encoding="utf-8")
            reference.write_text("one", encoding="utf-8")
            before = lifecycle.skill_tree_stat_fingerprint(skill_file)
            reference.write_text("two-two", encoding="utf-8")
            after = lifecycle.skill_tree_stat_fingerprint(skill_file)
            self.assertNotEqual(before, after)

    def test_incremental_state_records_only_real_changes(self) -> None:
        with TemporaryDirectory() as directory:
            state_db = Path(directory) / "state.sqlite"
            first = {
                "path": str(Path(directory) / "alpha" / "SKILL.md"),
                "source": "user",
                "name": "alpha",
                "stat_fingerprint": "one",
                "content_sha256": "content-one",
                "record": {"name": "alpha", "routing_eligible": True, "flags": []},
            }
            added = skill_lifecycle_state.sync_records([first], path=state_db, sources=("user",))
            self.assertTrue(added["recorded_run"])
            self.assertEqual(added["counts"]["added"], 1)

            unchanged = skill_lifecycle_state.sync_records([first], path=state_db, sources=("user",))
            self.assertFalse(unchanged["recorded_run"])
            self.assertEqual(unchanged["change_count"], 0)
            self.assertEqual(skill_lifecycle_state.snapshot(state_db)["run_count"], 1)

            modified_record = dict(first)
            modified_record["stat_fingerprint"] = "two"
            modified = skill_lifecycle_state.sync_records([modified_record], path=state_db, sources=("user",))
            self.assertEqual(modified["counts"]["modified"], 1)

            removed = skill_lifecycle_state.sync_records([], path=state_db, sources=("user",))
            self.assertEqual(removed["counts"]["removed"], 1)
            state = skill_lifecycle_state.snapshot(state_db)
            self.assertEqual(state["active_count"], 0)
            self.assertEqual(state["removed_count"], 1)
            self.assertEqual(state["run_count"], 3)

    def test_incremental_refresh_handles_add_modify_and_rename(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skills_root = root / "skills"
            skill_dir = skills_root / "alpha"
            skill_file = skill_dir / "SKILL.md"
            script = skill_dir / "scripts" / "run.py"
            script.parent.mkdir(parents=True)
            skill_file.write_text("---\nname: alpha\ndescription: Alpha test skill.\n---\n", encoding="utf-8")
            script.write_text("print('one')\n", encoding="utf-8")
            state_db = root / "state.sqlite"
            with (
                mock.patch.object(lifecycle, "GLOBAL_SKILLS", skills_root),
                mock.patch.object(lifecycle, "SYSTEM_SKILLS", skills_root / ".system"),
                mock.patch.object(lifecycle, "PLUGIN_CACHE", root / "plugins"),
            ):
                added = lifecycle.refresh_incremental(state_db=state_db)
                self.assertEqual(added["summary"]["counts"]["added"], 1)
                self.assertEqual(added["parsed_count"], 1)

                unchanged = lifecycle.refresh_incremental(state_db=state_db)
                self.assertEqual(unchanged["summary"]["change_count"], 0)
                self.assertEqual(unchanged["reused_count"], 1)

                script.write_text("print('two-two')\n", encoding="utf-8")
                modified = lifecycle.refresh_incremental(state_db=state_db)
                self.assertEqual(modified["summary"]["counts"]["modified"], 1)
                self.assertEqual(modified["parsed_count"], 1)

                renamed = skills_root / "beta"
                skill_dir.rename(renamed)
                (renamed / "SKILL.md").write_text(
                    "---\nname: beta\ndescription: Beta test skill.\n---\n",
                    encoding="utf-8",
                )
                rename_refresh = lifecycle.refresh_incremental(state_db=state_db)
                self.assertEqual(rename_refresh["summary"]["counts"]["added"], 1)
                self.assertEqual(rename_refresh["summary"]["counts"]["removed"], 1)


if __name__ == "__main__":
    unittest.main()
