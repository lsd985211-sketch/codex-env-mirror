import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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
            data, mode = mirror_cli.source_payload(path, "copy")
            self.assertEqual(mode, "copy_with_token_redaction")
            self.assertIn(b"<SECRET:BEARER_TOKEN>", data)

    def test_sensitive_json_keys_are_redacted(self) -> None:
        payload = mirror_cli.redact_json_value({"token": "abc", "nested": {"password": "def"}})
        self.assertEqual(payload["token"], "<SECRET:TOKEN>")
        self.assertEqual(payload["nested"]["password"], "<SECRET:PASSWORD>")

    def test_secret_placeholder_is_not_a_finding(self) -> None:
        findings = mirror_cli.secret_findings('api_key = "<SECRET:API_KEY>"', path="config.toml", config_file=True)
        self.assertEqual(findings, [])

    def test_restore_graph_is_acyclic(self) -> None:
        self.assertEqual(mirror_cli.restore_graph_issues(), [])

    def test_stage_path_maps_logical_roots(self) -> None:
        self.assertEqual(
            mirror_cli.stage_relative_path("${CODEX_HOME}\\skills\\demo\\SKILL.md", "unused").as_posix(),
            "codex-home/skills/demo/SKILL.md",
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
