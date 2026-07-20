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
    def test_codex_hooks_source_contract_is_optional_restore_template(self) -> None:
        config = json.loads(mirror_cli.SOURCE_MANIFEST.read_text(encoding="utf-8"))
        source = next(item for item in config["sources"] if item["id"] == "codex-hooks")
        self.assertEqual(source["kind"], "file")
        self.assertEqual(source["source"], "${CODEX_HOME}\\hooks.json")
        self.assertEqual(source["destination"], "exports/codex-home/hooks.template.json")
        self.assertEqual(source["restore_path"], "${CODEX_HOME}\\hooks.json")
        self.assertEqual(source["mode"], "redact_json")
        self.assertEqual(source["classification"], "configuration_template")
        self.assertEqual(source["owner"], "codex_rule_observer")
        self.assertEqual(source["activation"], "owner_merge_only")
        self.assertFalse(source["coverage_required"])
        self.assertFalse(source["required"])

    def test_optional_codex_hooks_source_can_be_absent_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "variables": {"CODEX_HOME": temp_dir},
                "policy": {"max_snapshot_bytes": 1024},
                "sources": [{
                    "id": "codex-hooks",
                    "kind": "file",
                    "source": "${CODEX_HOME}\\hooks.json",
                    "required": False,
                }],
                "generated_sources": [],
            }
            with patch.object(mirror_cli, "collect_asset_dispositions", return_value={"issues": []}), \
                    patch.object(mirror_cli, "membership_projection_issues", return_value=[]):
                plan = mirror_cli.collect_plan(config)
            self.assertTrue(plan["ok"])
            self.assertEqual(plan["summary"]["required_sources_missing"], [])
            self.assertFalse(plan["sources"][0]["exists"])

    def test_hooks_payload_redacts_secrets_without_enforcing_hook_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hooks.json"
            path.write_text(json.dumps({
                "api_token": "not-for-snapshot",
                "hooks": {"PreToolUse": [{"hooks": []}]},
            }), encoding="utf-8")
            data, mode, content_kind = mirror_cli.source_payload(path, "redact_json")
            payload = json.loads(data.decode("utf-8"))
            self.assertEqual(mode, "redact_json")
            self.assertEqual(content_kind, "text")
            self.assertEqual(payload["api_token"], "<SECRET:API_TOKEN>")
            self.assertIn("PreToolUse", payload["hooks"])

    def test_transient_diagnostics_have_explicit_regenerate_dispositions(self) -> None:
        policy = json.loads(mirror_cli.ASSET_DISPOSITIONS.read_text(encoding="utf-8"))
        roots = {item["id"]: item for item in policy["roots"]}

        def disposition_for(root_id: str, name: str) -> str:
            for rule in roots[root_id]["rules"]:
                if name in rule.get("names", []):
                    return str(rule["disposition"])
            return ""

        self.assertEqual(disposition_for("codex-home", "diagnostics"), "regenerate")
        self.assertEqual(disposition_for("cc-switch-home", "crash.log"), "regenerate")

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

    def test_redact_toml_preserves_non_sensitive_text_after_syntax_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            original = b'enabled = true\r\n[plugins]\r\nname = "example"\r\n'
            path.write_bytes(original)

            payload = mirror_cli.redact_toml(path)

            self.assertEqual(payload, original)

    def test_redact_toml_rewrites_only_sensitive_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_bytes(b'endpoint = "https://example.test"\r\napi_token = "not-for-export"\r\n')

            payload = mirror_cli.redact_toml(path).decode("utf-8")

            self.assertIn('endpoint = "https://example.test"\r\n', payload)
            self.assertIn('api_token = "<SECRET:API_TOKEN>"\r\n', payload)
            self.assertNotIn("not-for-export", payload)

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

    def test_governance_text_hash_is_stable_across_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "script.ps1"
            path.write_bytes(b"line-1\nline-2\n")
            lf_hash = mirror_cli.sha256_text_file(path)
            path.write_bytes(b"line-1\r\nline-2\r\n")
            self.assertEqual(mirror_cli.sha256_text_file(path), lf_hash)

    def test_control_plane_state_detects_static_drift_and_snapshot_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifests = root / "manifests"
            manifests.mkdir()
            static = root / "README.md"
            static.write_text("current\n", encoding="utf-8")
            current = root / "CURRENT.md"
            current.write_text("# Current\n", encoding="utf-8")
            contract = manifests / "control-plane-contract.json"
            contract.write_text(json.dumps({
                "schema": "codex_mirror.control_plane_contract.v1",
                "control_plane_version": "2.2.0",
                "files": [
                    {"path": "README.md", "role": "static_contract"},
                    {"path": "CURRENT.md", "role": "generated_current_state"},
                    {"path": "manifests/control-plane-state.json", "role": "generated_current_state"},
                ],
            }), encoding="utf-8")
            state = manifests / "control-plane-state.json"
            state.write_text(json.dumps({
                "schema": "codex_mirror.control_plane_state.v1",
                "control_plane_version": "2.2.0",
                "snapshot": {"snapshot_id": "old"},
                "files": [{"path": "README.md", "role": "static_contract", "sha256": "wrong"}],
                "current_md_sha256": mirror_cli.sha256_file(current),
            }), encoding="utf-8")
            with patch.object(mirror_cli, "ROOT", root), \
                    patch.object(mirror_cli, "CONTROL_PLANE_CONTRACT", contract), \
                    patch.object(mirror_cli, "CONTROL_PLANE_STATE", state), \
                    patch.object(mirror_cli, "CURRENT_STATE_PATH", current):
                issues = mirror_cli.control_plane_issues("latest")
            codes = {item["code"] for item in issues}
            self.assertIn("control_plane_snapshot_mismatch", codes)
            self.assertIn("control_plane_static_file_drift", codes)

    def test_control_plane_contract_declares_current_surfaces(self) -> None:
        contract = json.loads(mirror_cli.CONTROL_PLANE_CONTRACT.read_text(encoding="utf-8"))
        roles = {item["path"]: item["role"] for item in contract["files"]}
        self.assertEqual(roles["CURRENT.md"], "generated_current_state")
        self.assertEqual(roles["manifests/control-plane-state.json"], "generated_current_state")
        self.assertEqual(roles["manifests/contract-review-state.json"], "generated_milestone_evidence")
        self.assertNotIn("manifests/control-plane-state.json", mirror_cli.governance_hashes())
        self.assertNotIn("manifests/contract-review-state.json", mirror_cli.governance_hashes())

    def test_snapshot_validation_skips_live_sources_by_default(self) -> None:
        with patch.object(mirror_cli, "source_coverage_issues") as coverage, \
                patch.object(mirror_cli, "generated_source_issues") as generated, \
                patch.object(mirror_cli, "collect_asset_dispositions") as dispositions:
            payload = mirror_cli.validate_snapshot()
        coverage.assert_not_called()
        generated.assert_not_called()
        dispositions.assert_not_called()
        self.assertEqual(payload["validation_scope"], "snapshot")
        self.assertFalse(payload["source_freshness_checked"])
        self.assertIsNone(payload["source_freshness_ok"])

    def test_live_validation_reports_source_drift_separately(self) -> None:
        source_issue = {"code": "source_assets_changed", "source_id": "demo"}
        with patch.object(mirror_cli, "source_coverage_issues", return_value=[source_issue]), \
                patch.object(mirror_cli, "generated_source_issues", return_value=[]), \
                patch.object(mirror_cli, "collect_asset_dispositions", return_value={"issues": []}):
            payload = mirror_cli.validate_snapshot(live_sources=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["validation_scope"], "snapshot_and_live_sources")
        self.assertTrue(payload["source_freshness_checked"])
        self.assertFalse(payload["source_freshness_ok"])
        self.assertIn(source_issue, payload["issues"])

    def test_membership_projection_blocks_unowned_sources(self) -> None:
        config = {
            "sources": [{"id": "owned-source"}, {"id": "new-source"}],
            "generated_sources": [{"id": "owned-generated"}],
        }
        projection = {
            "issues": [],
            "source_ids": ["owned-source"],
            "generated_source_ids": ["owned-generated"],
        }
        issues = mirror_cli.membership_projection_issues(config, {}, projection)
        self.assertEqual(issues, [{"code": "source_missing_membership_owner", "source_id": "new-source"}])

    def test_source_dependency_graph_reports_missing_and_cycles(self) -> None:
        graph = mirror_cli.source_dependency_graph({
            "sources": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a", "missing"]}],
            "generated_sources": [],
        })
        codes = {item["code"] for item in graph["issues"]}
        self.assertFalse(graph["ok"])
        self.assertIn("source_dependency_missing", codes)
        self.assertIn("source_dependency_cycle", codes)

    def test_affected_source_plan_closes_generated_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "config.toml"
            source.write_text("value = true\n", encoding="utf-8")
            config = {
                "variables": {},
                "sources": [{"id": "config", "kind": "file", "source": str(source)}],
                "generated_sources": [{"id": "derived", "kind": "command_json", "command": ["python", "-c", "print('{}')"], "depends_on": ["config"]}],
            }
            plan = mirror_cli.affected_source_plan(config, [str(source)])
            self.assertTrue(plan["ok"])
            self.assertEqual(plan["direct_source_ids"], ["config"])
            self.assertEqual(plan["dependent_generated_source_ids"], ["derived"])
            self.assertFalse(plan["full_rebuild_required"])

    def test_incremental_plan_reacquires_membership_guard_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill = root / "skills" / "context-compression" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("active", encoding="utf-8")
            config = {
                "variables": {},
                "sources": [{"id": "codex-skills", "kind": "tree", "source": str(root / "skills")}],
                "generated_sources": [{
                    "id": mirror_cli.MEMBERSHIP_ASSET_ID,
                    "kind": "command_json",
                    "command": ["python", "-c", "print('{}')"],
                }],
            }
            plan = mirror_cli.affected_source_plan(config, [str(skill)])
            self.assertTrue(plan["ok"])
            self.assertFalse(plan["full_rebuild_required"])
            self.assertTrue(plan["guard_authority_refreshed"])
            self.assertIn(mirror_cli.MEMBERSHIP_ASSET_ID, plan["dependent_generated_source_ids"])
            self.assertNotIn(mirror_cli.MEMBERSHIP_ASSET_ID, plan["reused_generated_source_ids"])

    def test_workspace_bridge_file_change_is_incremental_and_refreshes_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "workspace" / "_bridge"
            changed = bridge / "local_mcp_hub.py"
            changed.parent.mkdir(parents=True)
            changed.write_text("ok\n", encoding="utf-8")
            config = {
                "variables": {},
                "sources": [{"id": "workspace-bridge-source", "kind": "tree", "source": str(bridge)}],
                "generated_sources": [{
                    "id": mirror_cli.MEMBERSHIP_ASSET_ID,
                    "kind": "command_json",
                    "command": ["python", "-c", "print('{}')"],
                    "depends_on": ["workspace-bridge-source"],
                }],
            }

            plan = mirror_cli.affected_source_plan(config, [str(changed)])

            self.assertTrue(plan["ok"])
            self.assertFalse(plan["full_rebuild_required"])
            self.assertEqual(plan["source_file_changes"], {"workspace-bridge-source": ["local_mcp_hub.py"]})
            self.assertTrue(plan["guard_authority_refreshed"])

    def test_path_aware_generated_dependency_skips_unrelated_bridge_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = root / "workspace" / "_bridge"
            unrelated = bridge / "codex_environment_mirror.py"
            watched = bridge / "wsl_workspace_owner.py"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("unrelated\n", encoding="utf-8")
            watched.write_text("watched\n", encoding="utf-8")
            config = {
                "variables": {},
                "sources": [{"id": "workspace-bridge-source", "kind": "tree", "source": str(bridge)}],
                "generated_sources": [
                    {"id": mirror_cli.MEMBERSHIP_ASSET_ID, "kind": "command_json", "command": ["python", "-c", "print('{}')"]},
                    {
                        "id": "wsl-export",
                        "kind": "command_json",
                        "command": ["python", "-c", "print('{}')"],
                        "depends_on": ["workspace-bridge-source"],
                        "depends_on_paths": {"workspace-bridge-source": ["wsl_workspace_owner.py", "shared/**"]},
                    },
                    {
                        "id": mirror_cli.WORK_GIT_RELEASE_SOURCE_ID,
                        "kind": "command_json",
                        "command": ["python", "-c", "print('{}')"],
                        "depends_on": ["workspace-bridge-source"],
                    },
                ],
            }

            unrelated_plan = mirror_cli.affected_source_plan(config, [str(unrelated)])
            watched_plan = mirror_cli.affected_source_plan(config, [str(watched)])

            self.assertNotIn("wsl-export", unrelated_plan["dependent_generated_source_ids"])
            self.assertIn(mirror_cli.MEMBERSHIP_ASSET_ID, unrelated_plan["dependent_generated_source_ids"])
            self.assertIn(mirror_cli.WORK_GIT_RELEASE_SOURCE_ID, unrelated_plan["dependent_generated_source_ids"])
            self.assertIn("wsl-export", watched_plan["dependent_generated_source_ids"])

    def test_work_git_release_proof_requires_matching_clean_heads(self) -> None:
        captured = {"ok": True, "work_git": {"release_ready": True, "clean": True, "worktree_head": "abc", "bare_head": "abc"}}
        current = {"ok": True, "work_git": {"release_ready": True, "clean": True, "worktree_head": "abc", "bare_head": "abc"}}

        self.assertTrue(mirror_cli.work_git_release_proves_snapshot_source(captured, current))
        self.assertFalse(mirror_cli.work_git_release_proves_snapshot_source(captured, {"ok": True, "work_git": {**current["work_git"], "worktree_head": "def"}}))
        self.assertFalse(mirror_cli.work_git_release_proves_snapshot_source(captured, {"ok": True, "work_git": {**current["work_git"], "clean": False}}))

    def test_trusted_work_git_source_coverage_requires_captured_release_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = root / "work-git-release.json"
            captured = {"ok": True, "work_git": {"release_ready": True, "clean": True, "worktree_head": "abc", "bare_head": "abc"}}
            receipt.write_text(json.dumps(captured), encoding="utf-8")
            manifest = {"assets": [{"asset_id": mirror_cli.WORK_GIT_RELEASE_SOURCE_ID, "snapshot_path": receipt.name}]}
            with patch.object(mirror_cli, "current_work_git_release", return_value=captured):
                trusted, evidence = mirror_cli.trusted_work_git_source_coverage(root, manifest, {
                    "variables": {"WORK_GIT_ROOT": str(root / "work-git")},
                    "sources": [
                        {"id": mirror_cli.WORK_GIT_SOURCE_ID, "source": "${WORK_GIT_ROOT}/workspace/_bridge"},
                        {"id": "external", "source": "/outside"},
                    ],
                })

            self.assertEqual(trusted, {mirror_cli.WORK_GIT_SOURCE_ID})
            self.assertEqual(evidence, {"mode": "work_git_release_receipt", "head": "abc", "source_count": 1})

    def test_snapshot_recovery_removes_stale_staging_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "runtime"
            snapshots = root / "snapshots"
            latest = snapshots / "latest.json"
            stale_stage = runtime / "staging" / "candidate"
            stale_stage.mkdir(parents=True)
            snapshots.mkdir()
            mirror_cli.write_json_atomic(latest, {"snapshot_id": "previous"})
            with patch.object(mirror_cli, "RUNTIME_ROOT", runtime), \
                    patch.object(mirror_cli, "SNAPSHOT_ROOT", snapshots), \
                    patch.object(mirror_cli, "LATEST_PATH", latest):
                mirror_cli.write_snapshot_transaction({
                    "token": "stale", "pid": 0, "snapshot_id": "candidate", "stage_path": str(stale_stage),
                    "phase": "capturing_sources", "previous_latest": {"snapshot_id": "previous"},
                })
                recovery = mirror_cli.recover_interrupted_snapshot_state()

            self.assertTrue(recovery["ok"])
            self.assertTrue(recovery["recovered"])
            self.assertFalse(stale_stage.exists())
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["snapshot_id"], "previous")
            self.assertFalse((runtime / "transactions" / mirror_cli.SNAPSHOT_TRANSACTION_NAME).exists())

    def test_snapshot_recovery_removes_unpublished_promoted_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "runtime"
            snapshots = root / "snapshots"
            latest = snapshots / "latest.json"
            candidate = snapshots / "candidate"
            candidate.mkdir(parents=True)
            (candidate / "snapshot-manifest.json").write_text(json.dumps({"snapshot_id": "candidate"}), encoding="utf-8")
            mirror_cli.write_json_atomic(latest, {"snapshot_id": "previous"})
            with patch.object(mirror_cli, "RUNTIME_ROOT", runtime), \
                    patch.object(mirror_cli, "SNAPSHOT_ROOT", snapshots), \
                    patch.object(mirror_cli, "LATEST_PATH", latest):
                mirror_cli.write_snapshot_transaction({
                    "token": "stale", "pid": 0, "snapshot_id": "candidate", "phase": "promoted",
                    "previous_latest": {"snapshot_id": "previous"},
                })
                recovery = mirror_cli.recover_interrupted_snapshot_state()

            self.assertTrue(recovery["ok"])
            self.assertFalse(candidate.exists())
            self.assertEqual(recovery["actions"][0]["code"], "orphan_snapshot_candidate_removed")

    def test_snapshot_recovery_reverts_invalid_latest_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / "runtime"
            snapshots = root / "snapshots"
            latest = snapshots / "latest.json"
            candidate = snapshots / "candidate"
            candidate.mkdir(parents=True)
            mirror_cli.write_json_atomic(latest, {"snapshot_id": "candidate"})
            with patch.object(mirror_cli, "RUNTIME_ROOT", runtime), \
                    patch.object(mirror_cli, "SNAPSHOT_ROOT", snapshots), \
                    patch.object(mirror_cli, "LATEST_PATH", latest):
                mirror_cli.write_snapshot_transaction({
                    "token": "stale", "pid": 0, "snapshot_id": "candidate", "phase": "latest_updated",
                    "previous_latest": {"snapshot_id": "previous"},
                })
                recovery = mirror_cli.recover_interrupted_snapshot_state()

            self.assertTrue(recovery["ok"])
            self.assertFalse(candidate.exists())
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["snapshot_id"], "previous")
            self.assertEqual(recovery["actions"][0]["code"], "invalid_latest_candidate_reverted")

    def test_incremental_reuse_checks_snapshot_integrity_without_current_governance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "snapshot-manifest.json").write_text(json.dumps({"snapshot_id": "snapshot"}), encoding="utf-8")
            with patch.object(mirror_cli, "resolve_snapshot", return_value=snapshot), \
                    patch.object(mirror_cli, "validate_snapshot", return_value={"ok": True}) as validate:
                path, manifest, status = mirror_cli.previous_snapshot_for_incremental()

            self.assertEqual(path, snapshot)
            self.assertEqual(manifest, {"snapshot_id": "snapshot"})
            self.assertEqual(status, "previous_snapshot_valid")
            validate.assert_called_once_with("snapshot", control_plane=False, governance=False)

    def test_incremental_reuse_keeps_unmodified_tree_asset(self) -> None:
        asset = {"asset_id": "workspace-bridge-source:unchanged.py"}

        self.assertTrue(mirror_cli.reuse_previous_asset_for_incremental(
            asset,
            {"workspace-bridge-source"},
            set(),
            {"workspace-bridge-source": {"changed.py"}},
        ))
        self.assertFalse(mirror_cli.reuse_previous_asset_for_incremental(
            {"asset_id": "workspace-bridge-source:changed.py"},
            {"workspace-bridge-source"},
            set(),
            {"workspace-bridge-source": {"changed.py"}},
        ))

    def test_incremental_reuse_links_verified_previous_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous = root / "previous"
            stage = root / "stage"
            source = previous / "exports" / "asset.txt"
            source.parent.mkdir(parents=True)
            source.write_text("verified", encoding="utf-8")
            asset = {
                "asset_id": "asset",
                "snapshot_path": "exports/asset.txt",
                "sha256": mirror_cli.sha256_file(source),
                "bytes": source.stat().st_size,
                "content_kind": "text",
            }

            copied = mirror_cli.copy_previous_asset(stage, previous, asset)

            destination = stage / "exports" / "asset.txt"
            self.assertEqual(destination.read_text(encoding="utf-8"), "verified")
            self.assertEqual(copied["sha256"], asset["sha256"])
            self.assertIn(copied["reuse"]["mode"], {"previous_snapshot_hardlink", "previous_snapshot_copy"})

    def test_control_plane_validation_reuses_verified_asset_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "snapshot-manifest.json").write_text(json.dumps({"snapshot_id": "snapshot", "governance_hashes": {"scripts/mirror_cli.py": "same"}}), encoding="utf-8")
            with patch.object(mirror_cli, "resolve_snapshot", return_value=root), \
                    patch.object(mirror_cli, "governance_hashes", return_value={"scripts/mirror_cli.py": "same"}), \
                    patch.object(mirror_cli, "control_plane_issues", return_value=[]), \
                    patch.object(mirror_cli, "restore_graph_issues", return_value=[]), \
                    patch.object(mirror_cli, "agent_bootstrap_issues", return_value=[]), \
                    patch.object(mirror_cli, "repository_secret_findings", return_value=[]):
                result = mirror_cli.validate_control_plane("snapshot")

            self.assertTrue(result["ok"])
            self.assertEqual(result["schema"], "codex_mirror.control_plane_validate.v1")


    def test_affected_source_plan_unknown_change_requires_full_rebuild(self) -> None:
        config = {"variables": {}, "sources": [{"id": "config", "kind": "file", "source": "C:/known.toml"}], "generated_sources": []}
        plan = mirror_cli.affected_source_plan(config, ["C:/outside.txt"])
        self.assertFalse(plan["ok"])
        self.assertTrue(plan["full_rebuild_required"])
        self.assertIn("changed_path_unmapped", plan["reasons"])

    def test_affected_source_plan_accepts_membership_logical_roots(self) -> None:
        config = {
            "variables": {"CODEX_HOME": str(Path.home() / "codex")},
            "sources": [{"id": "memory", "kind": "tree", "source": "${CODEX_HOME}\\memories"}],
            "generated_sources": [],
        }
        plan = mirror_cli.affected_source_plan(config, ["codex_home:memories/example.md"])
        self.assertEqual(plan["direct_source_ids"], ["memory"])
        self.assertFalse(plan["full_rebuild_required"])

    def test_source_dependency_graph_is_embedded_in_snapshot_manifest_contract(self) -> None:
        graph = mirror_cli.source_dependency_graph({
            "sources": [{"id": "source"}],
            "generated_sources": [{"id": "derived", "depends_on": ["source"]}],
        })
        self.assertTrue(graph["ok"])
        self.assertEqual(graph["graph"]["derived"], ["source"])

    def test_snapshot_lock_rejects_concurrent_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir)
            lock = runtime / "locks" / "snapshot.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"pid": __import__("os").getpid(), "operation": "snapshot", "token": "active"}), encoding="utf-8")
            with patch.object(mirror_cli, "RUNTIME_ROOT", runtime), patch.object(mirror_cli, "create_snapshot") as create:
                payload = mirror_cli.snapshot_with_lock({})
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["reason"], "mirror_operation_busy")
            create.assert_not_called()

    def test_gitignore_only_excludes_repository_runtime_root(self) -> None:
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/runtime/", rules)
        self.assertIn("/archives/", rules)
        self.assertNotIn("runtime/", rules)
        self.assertNotIn("archives/", rules)

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

    def test_capture_quiescence_uses_recoverable_redacted_content_and_semantic_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_file = root / "config.toml"
            config_file.write_text('api_token = "secret-a"\nmode = "stable"\n', encoding="utf-8")
            settings = root / "settings.json"
            settings.write_text('{"api_token":"secret-a","mode":"stable"}', encoding="utf-8")
            database = root / "cc-switch.db"
            connection = sqlite3.connect(database)
            for table in mirror_cli.CC_SWITCH_SEMANTIC_TABLES:
                connection.execute(f'CREATE TABLE "{table}" (id TEXT)')
            connection.commit()
            connection.close()
            config = {
                "variables": {},
                "policy": {"capture_quiescence": {"source_ids": ["config", "settings"], "generated_source_ids": ["semantic"], "interval_seconds": 1}},
                "sources": [
                    {"id": "config", "source": str(config_file), "mode": "redact_toml"},
                    {"id": "settings", "source": str(settings), "mode": "redact_json"},
                ],
                "generated_sources": [{"id": "semantic", "kind": "cc_switch_semantic_export", "source": str(database)}],
            }
            self.assertTrue(mirror_cli.capture_quiescence_probe(config)["ok"])

            def mutate_between_samples(_: float) -> None:
                settings.write_text('{"api_token":"secret-b","mode":"changed"}', encoding="utf-8")

            probe = mirror_cli.capture_quiescence_probe(config, sleep=mutate_between_samples)
            self.assertFalse(probe["ok"])
            self.assertEqual(probe["reason"], "source_capture_not_quiescent")
            self.assertEqual(probe["changed"][0]["asset_id"], "settings")

    def test_capture_quiescence_rejects_change_before_snapshot_staging(self) -> None:
        config = {"policy": {"capture_quiescence": {"source_ids": ["config"]}}, "sources": [{"id": "config"}], "generated_sources": []}
        unstable = {"schema": "codex_mirror.capture_quiescence.v1", "ok": False, "reason": "source_capture_not_quiescent"}
        with patch.object(mirror_cli, "collect_plan", return_value={"ok": True}), \
                patch.object(mirror_cli, "capture_quiescence_probe", return_value=unstable), \
                patch.object(mirror_cli, "write_snapshot_transaction") as transaction:
            result = mirror_cli.create_snapshot(config)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "source_capture_not_quiescent")
        self.assertFalse(result["candidate_created"])
        transaction.assert_not_called()

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
