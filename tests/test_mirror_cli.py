import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mirror_cli", ROOT / "scripts" / "mirror_cli.py")
assert SPEC and SPEC.loader
mirror_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror_cli)


class MirrorCliTests(unittest.TestCase):
    def test_known_token_is_redacted(self) -> None:
        token = "sk-" + "abcdefghijklmnopqrstuvwxyz"
        value = "key=" + token
        self.assertNotIn(token, mirror_cli.scrub_known_tokens(value))

    def test_copy_payload_redacts_embedded_bearer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source.ts"
            bearer = "Bearer " + ("A" * 32)
            path.write_text(f'const value = "{bearer}";', encoding="utf-8")
            data, mode, content_kind = mirror_cli.source_payload(path, "copy")
            self.assertEqual(mode, "copy_with_token_redaction")
            self.assertEqual(content_kind, "text")
            self.assertIn(b"<SECRET:BEARER_TOKEN>", data)

    def test_sensitive_json_keys_are_redacted(self) -> None:
        payload = mirror_cli.redact_json_value({"token": "abc", "nested": {"password": "def"}})
        self.assertEqual(payload["token"], "<SECRET:TOKEN>")
        self.assertEqual(payload["nested"]["password"], "<SECRET:PASSWORD>")

    def test_sensitive_url_components_are_redacted(self) -> None:
        value = mirror_cli.redact_url_value("https://user:pass@example.com/path?token=abc&mode=fast")
        self.assertNotIn("user:pass", value)
        self.assertNotIn("token=abc", value)
        self.assertIn("mode=fast", value)

    def test_secret_placeholder_is_not_a_finding(self) -> None:
        findings = mirror_cli.secret_findings('api_key = "<SECRET:API_KEY>"', path="config.toml", config_file=True)
        self.assertEqual(findings, [])

    def test_restore_graph_is_acyclic(self) -> None:
        self.assertEqual(mirror_cli.restore_graph_issues(), [])

    def test_source_policy_includes_active_skill_dependencies_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "active").mkdir()
            (root / "active" / "SKILL.md").write_text("active", encoding="utf-8")
            (root / "active" / "font.ttf").write_bytes(b"font-data")
            (root / "active" / "schema.xsd").write_text("<schema/>", encoding="utf-8")
            (root / "active" / "LICENSE").write_text("license", encoding="utf-8")
            (root / "active" / ".DS_Store").write_bytes(b"junk")
            (root / ".disabled").mkdir()
            (root / ".disabled" / "SKILL.md").write_text("disabled", encoding="utf-8")
            (root / ".system").mkdir()
            (root / ".system" / "SKILL.md").write_text("system", encoding="utf-8")
            spec = {
                "exclude_dirs": [".disabled", ".system"],
                "exclude_files": [".DS_Store"],
                "extra_allowed_extensions": [".xsd"],
                "binary_extensions": [".ttf"],
                "allow_extensionless": True,
            }
            policy = {
                "allowed_extensions": [".md"],
                "prohibited_extensions": [".ttf"],
                "global_exclude_dirs": ["__pycache__"],
                "global_exclude_files": [".DS_Store"],
                "max_file_bytes": 1024,
            }
            files = {path.relative_to(root).as_posix() for path in mirror_cli.iter_source_files(root, spec, policy)}
            self.assertEqual(files, {"active/LICENSE", "active/SKILL.md", "active/font.ttf", "active/schema.xsd"})

    def test_workspace_bridge_excludes_historical_backup_notes(self) -> None:
        config = json.loads(mirror_cli.SOURCE_MANIFEST.read_text(encoding="utf-8"))
        workspace_bridge = next(item for item in config["sources"] if item["id"] == "workspace-bridge-source")
        excluded = set(workspace_bridge.get("exclude_files", []))
        self.assertEqual(
            excluded,
            {
                "backup-note-admin-superuser-permission-20260629-234500.txt",
                "backup-note-ask-whitelist-20260629-233000.txt",
                "backup-note-permission-policy-20260629-222015.txt",
                "backup-note-permission-table-deny-unauthorized-20260629-224813.txt",
            },
        )

    def test_source_freshness_detects_changed_content_but_allows_missing_live_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.md"
            source.write_text("new", encoding="utf-8")
            config = root / "sources.json"
            config.write_text(json.dumps({
                "variables": {},
                "policy": {
                    "allowed_extensions": [".md"],
                    "prohibited_extensions": [],
                    "global_exclude_dirs": [],
                    "global_exclude_files": [],
                    "max_file_bytes": 1024,
                },
                "sources": [{
                    "id": "source",
                    "kind": "file",
                    "source": str(source),
                    "mode": "copy",
                    "coverage_required": True,
                }],
            }), encoding="utf-8")
            old_manifest = mirror_cli.SOURCE_MANIFEST
            mirror_cli.SOURCE_MANIFEST = config
            try:
                manifest = {"assets": [{"asset_id": "source", "sha256": mirror_cli.sha256_bytes(b"old"), "mode": "copy"}]}
                findings = mirror_cli.source_coverage_issues(manifest)
                self.assertEqual(findings[0]["code"], "source_assets_changed")
                source.unlink()
                self.assertEqual(mirror_cli.source_coverage_issues(manifest), [])
            finally:
                mirror_cli.SOURCE_MANIFEST = old_manifest

    def test_asset_disposition_inventory_blocks_unknown_top_level_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live = root / "live"
            live.mkdir()
            (live / "known.md").write_text("known", encoding="utf-8")
            (live / "unknown.bin").write_bytes(b"unknown")
            dispositions = root / "asset-dispositions.json"
            dispositions.write_text(json.dumps({
                "schema": "codex_mirror.asset_dispositions.v1",
                "roots": [{"id": "live", "root": str(live), "required": True, "rules": []}],
            }), encoding="utf-8")
            archives = root / "external-archives.json"
            archives.write_text(json.dumps({"assets": [], "reacquire_instead_of_archive": [], "regenerate_instead_of_archive": []}), encoding="utf-8")
            config = {
                "variables": {},
                "sources": [{"id": "known", "source": str(live / "known.md")}],
            }
            with patch.object(mirror_cli, "ASSET_DISPOSITIONS", dispositions), patch.object(mirror_cli, "EXTERNAL_ARCHIVES", archives):
                payload = mirror_cli.collect_asset_dispositions(config)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["issues"][0]["name"], "unknown.bin")

    def test_agent_bootstrap_contract_is_present(self) -> None:
        self.assertEqual(mirror_cli.agent_bootstrap_issues(), [])

    def test_binary_asset_is_hash_validated_without_text_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "font.ttf"
            source.write_bytes(bytes(range(256)))
            data, mode, content_kind = mirror_cli.source_payload(source, "copy", "binary")
            asset = mirror_cli.add_asset(
                stage=root / "stage",
                snapshot_path="font.ttf",
                data=data,
                asset_id="font",
                owner="skill",
                classification="authority_export",
                mode=mode,
                content_kind=content_kind,
            )
            self.assertEqual(asset["content_kind"], "binary")
            self.assertEqual(asset["sha256"], mirror_cli.sha256_bytes(bytes(range(256))))

    def test_cc_switch_semantic_export_redacts_nested_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc-switch.db"
            connection = sqlite3.connect(database)
            for table in mirror_cli.CC_SWITCH_SEMANTIC_TABLES:
                if table == "providers":
                    connection.execute("CREATE TABLE providers (id TEXT, settings_config TEXT)")
                elif table == "settings":
                    connection.execute("CREATE TABLE settings (key TEXT, value TEXT)")
                else:
                    connection.execute(f'CREATE TABLE "{table}" (id TEXT)')
            connection.execute(
                "INSERT INTO providers VALUES (?, ?)",
                ("provider-1", json.dumps({"api_key": "raw-secret", "headers": {"Authorization": "Bearer raw-secret"}, "base_url": "https://example.com?v=1"})),
            )
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("access_token", "raw-token"))
            connection.commit()
            connection.close()
            payload = json.loads(mirror_cli.export_cc_switch_semantic(database))
            serialized = json.dumps(payload)
            self.assertNotIn("raw-secret", serialized)
            self.assertNotIn("raw-token", serialized)
            self.assertIn("<SECRET:API_KEY>", serialized)
            self.assertEqual(payload["tables"]["providers"]["row_count"], 1)
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE request_logs (id TEXT)")
            connection.execute("INSERT INTO request_logs VALUES ('runtime-only')")
            connection.commit()
            connection.close()
            updated = json.loads(mirror_cli.export_cc_switch_semantic(database))
            self.assertNotEqual(payload["source_sha256"], updated["source_sha256"])
            self.assertEqual(payload["semantic_sha256"], updated["semantic_sha256"])

    def test_plugin_inventory_records_enabled_manifest_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text('[plugins."demo@market"]\nenabled = true\n[plugins."off@market"]\nenabled = false\n', encoding="utf-8")
            manifest = root / "cache" / "market" / "demo" / "rev1" / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"name": "demo", "version": "1.2.3", "repository": "https://example.com/demo"}), encoding="utf-8")
            payload = json.loads(mirror_cli.export_plugin_inventory(config, root / "cache"))
            self.assertEqual(payload["enabled_count"], 1)
            self.assertEqual(payload["unresolved_count"], 0)
            self.assertEqual(payload["plugins"][0]["manifest_version"], "1.2.3")
            self.assertTrue(payload["plugins"][0]["manifest_sha256"])

    def test_current_checkpoint_export_uses_manifest_selection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            shared = Path(temp_dir)
            (shared / "checkpoints" / "current").mkdir(parents=True)
            (shared / "ARCHITECTURE.md").write_text("architecture", encoding="utf-8")
            (shared / "checkpoints" / "current" / "now.md").write_text("checkpoint", encoding="utf-8")
            manifest = shared / "checkpoints" / "MANIFEST.md"
            manifest.write_text(
                "# Manifest\n\n## Shared docs\n- ARCHITECTURE.md\n\n## Recent checkpoints\n- checkpoints/current/now.md\n",
                encoding="utf-8",
            )
            payload = json.loads(mirror_cli.export_current_checkpoints(manifest, shared))
            self.assertEqual(payload["selected_count"], 2)
            self.assertEqual({item["path"] for item in payload["selected"]}, {"ARCHITECTURE.md", "checkpoints/current/now.md"})

    def test_stage_path_maps_logical_roots(self) -> None:
        self.assertEqual(
            mirror_cli.stage_relative_path("${CODEX_HOME}\\skills\\demo\\SKILL.md", "unused").as_posix(),
            "codex-home/skills/demo/SKILL.md",
        )
        self.assertEqual(
            mirror_cli.stage_relative_path("${AGENT_HOME}\\skills\\demo\\SKILL.md", "unused").as_posix(),
            "agent-home/skills/demo/SKILL.md",
        )

    def test_manifest_files_parse(self) -> None:
        for path in (ROOT / "manifests").rglob("*.json"):
            json.loads(path.read_text(encoding="utf-8-sig"))

    def test_membership_guard_excludes_retired_wrapper_and_removes_lifecycle_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            membership = root / "membership.json"
            membership.write_text(json.dumps({"retirement_tombstones": [{
                "id": "mcp:legacy-index",
                "system": "mcp",
                "member": "legacy-index",
                "lifecycle": "decommissioned",
                "active_trace_paths": [{"path": "_bridge/legacy_index_mcp.py"}],
            }]}), encoding="utf-8")
            wrapper = root / "guarded-legacy-index.cmd"
            wrapper.write_text("echo retired", encoding="utf-8")
            assets = [
                {"asset_id": mirror_cli.MEMBERSHIP_ASSET_ID, "snapshot_path": "membership.json"},
                {"asset_id": "legacy-wrapper", "snapshot_path": "guarded-legacy-index.cmd", "restore_template": "${WORKSPACE_ROOT}\\_bridge\\tools\\mcp-wrappers\\guarded-legacy-index.cmd"},
            ]
            kept, guard = mirror_cli.apply_membership_guard(root, assets)
            self.assertEqual([item["asset_id"] for item in kept], [mirror_cli.MEMBERSHIP_ASSET_ID])
            self.assertTrue(membership.exists())
            self.assertFalse(wrapper.exists())
            self.assertEqual(guard["excluded_asset_count"], 1)
            self.assertNotIn("retirement_tombstones", membership.read_text(encoding="utf-8"))
            self.assertNotIn("legacy-index", membership.read_text(encoding="utf-8"))
            self.assertNotIn("tombstone_ids", guard)

    def test_retired_asset_reintroduction_is_detected(self) -> None:
        tombstones = [{
            "id": "scheduled_task:OldScheduler",
            "system": "scheduled_task",
            "member": "OldScheduler",
            "lifecycle": "decommissioned",
            "active_trace_paths": [{"path": "_bridge/shared/run-email-scheduler.ps1"}],
        }]
        assets = [{
            "asset_id": "legacy-runner",
            "snapshot_path": "exports/workspace/_bridge/shared/run-email-scheduler.ps1",
            "restore_template": "${WORKSPACE_ROOT}\\_bridge\\shared\\run-email-scheduler.ps1",
        }]
        guard = {
            "blocked_member_fingerprints": {},
            "blocked_path_fingerprints": [mirror_cli.fingerprint("_bridge/shared/run-email-scheduler.ps1")],
            "excluded_asset_fingerprints": [],
        }
        findings = mirror_cli.guarded_asset_conflicts(assets, guard)
        self.assertEqual(findings[0]["code"], "inactive_member_asset_reintroduced")

    def test_inactive_member_references_are_removed_and_fingerprint_detects_reentry(self) -> None:
        tombstones = [{"system": "mcp", "member": "legacy-index", "lifecycle": "decommissioned", "active_trace_paths": []}]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "owner.py"
            source.write_text('ACTIVE = True\nLEGACY = "LegacyIndex"\n', encoding="utf-8")
            assets = [{"asset_id": "owner", "snapshot_path": "owner.py", "sha256": "", "bytes": 0, "mode": "copy"}]
            changed = mirror_cli.sanitize_inactive_references(root, assets, tombstones)
            self.assertEqual(changed, 1)
            self.assertEqual(source.read_text(encoding="utf-8"), "ACTIVE = True\n")
            guard = mirror_cli.guard_fingerprints(tombstones, [])
            self.assertEqual(mirror_cli.guarded_text_conflicts('name = "legacy-index"', guard, "owner.py")[0]["code"], "inactive_member_reference_exported")


if __name__ == "__main__":
    unittest.main()
