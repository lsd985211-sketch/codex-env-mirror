from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import mcp_recovery_bundle_owner as owner  # noqa: E402


def manifest_for(source: Path, **overrides: object) -> dict:
    bundle = {
        "id": "sample-linux-x64",
        "implementation_type": "offline_node_bundle",
        "platform": "linux-x64",
        "source": str(source),
        "include": ["package.json", "node_modules/**"],
        "entrypoints": ["node_modules/.bin/sample"],
        "distribution": "github_release_asset",
        "required": True,
        "redistribution": {"public_release": True, "authorization": "MIT"},
    }
    bundle.update(overrides)
    return {"schema": "mcp_recovery_bundle_manifest.v1", "policy": {"content_addressed": True, "hash_algorithm": "sha256"}, "bundles": [bundle]}


class McpRecoveryBundleOwnerTests(unittest.TestCase):
    def _write_owner_receipt(
        self, root: Path, manifest: dict, bundle_id: str, *, expired: bool = False, manifest_sha256: str = ""
    ) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "schema": "mcp_recovery_owner_receipt.v1",
            "bundle_id": bundle_id,
            "manifest_sha256": manifest_sha256 or owner.manifest_sha256(manifest),
            "owner": "test-platform-owner",
            "platform": "windows-x64",
            "accepted_at": now.isoformat(),
            "expires_at": (now - timedelta(seconds=1) if expired else now + timedelta(hours=1)).isoformat(),
            "acceptance": {"ok": True, "evidence_refs": ["sha256:" + "a" * 64]},
        }
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{bundle_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_import_release_verifies_hashes_before_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_root = root / "archives"
            source = root / "remote"
            source.mkdir()
            archive = source / "sample-linux-x64.tar.gz"
            with tarfile.open(archive, "w:gz") as content:
                member = tarfile.TarInfo("node_modules/.bin/sample")
                member.size = 1
                content.addfile(member, io.BytesIO(b"x"))
            digest = owner.sha256_file(archive)
            index = {"schema": "codex_mcp_release_asset_index.v1", "assets": [{"id": "sample-linux-x64", "name": archive.name, "sha256": digest, "size_bytes": archive.stat().st_size, "platform": "linux-x64", "entrypoints": ["node_modules/.bin/sample"]}]}
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                destination = Path(command[command.index("--dir") + 1])
                if "mcp-bundle-index.json" in command:
                    (destination / "mcp-bundle-index.json").write_text(json.dumps(index), encoding="utf-8")
                else:
                    (destination / archive.name).write_bytes(archive.read_bytes())
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch.object(owner.subprocess, "run", side_effect=fake_run):
                result = owner.import_release(archive_root, repo="example/mirror", tag="seed-v1.0.0", gh_command="gh")
            self.assertTrue(result["ok"], result)
            self.assertTrue((archive_root / archive.name).is_file())
            self.assertEqual(len(commands), 2)

    def test_import_release_downloads_referenced_archive_from_prior_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_root = root / "archives"
            source = root / "remote"
            source.mkdir()
            archive = source / "sample-linux-x64.tar.gz"
            with tarfile.open(archive, "w:gz") as content:
                member = tarfile.TarInfo("node_modules/.bin/sample")
                member.size = 1
                content.addfile(member, io.BytesIO(b"x"))
            digest = owner.sha256_file(archive)
            index = {"schema": "codex_mcp_release_asset_index.v1", "assets": [{"id": "sample-linux-x64", "name": archive.name, "sha256": digest, "size_bytes": archive.stat().st_size, "release_tag": "seed-v1.0.0"}]}
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                destination = Path(command[command.index("--dir") + 1])
                if "mcp-bundle-index.json" in command:
                    (destination / "mcp-bundle-index.json").write_text(json.dumps(index), encoding="utf-8")
                else:
                    (destination / archive.name).write_bytes(archive.read_bytes())
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch.object(owner.subprocess, "run", side_effect=fake_run):
                result = owner.import_release(archive_root, repo="example/mirror", tag="seed-v1.1.0", gh_command="gh")
            self.assertTrue(result["ok"], result)
            archive_download = next(command for command in commands if archive.name in command)
            self.assertIn("seed-v1.0.0", archive_download)

    def test_build_verify_and_readiness_for_offline_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "node_modules" / ".bin").mkdir(parents=True)
            (source / "package.json").write_text("{}", encoding="utf-8")
            (source / "node_modules" / ".bin" / "sample").write_text("#!/bin/sh", encoding="utf-8")
            manifest = manifest_for(source)
            archive_root = root / "archives"
            built = owner.build(manifest, owner.variables_for(manifest), archive_root, [], False, owner.host_platform())
            self.assertTrue(built["ok"], built)
            self.assertTrue(owner.verify_archive(next(iter(owner.load_index(archive_root)["bundles"].values())), archive_root)["ok"])
            result = owner.readiness(manifest, owner.variables_for(manifest), archive_root)
            self.assertTrue(result["capability_restore_ready"], result)

    def test_missing_source_blocks_required_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(root / "missing")
            status = owner.readiness(manifest, owner.variables_for(manifest), root / "archives")
            self.assertFalse(status["capability_restore_ready"])
            self.assertEqual(status["blocked_missing_bundle"], ["sample-linux-x64"])

    def test_missing_entrypoint_rejects_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "package.json").write_text("{}", encoding="utf-8")
            manifest = manifest_for(source)
            result = owner.build(manifest, owner.variables_for(manifest), root / "archives", [], False, owner.host_platform())
            self.assertFalse(result["ok"])
            self.assertEqual(result["results"][0]["reason"], "bundle_source_incomplete")

    def test_public_authorization_and_gitnexus_explicit_authorization_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = manifest_for(Path(temp_dir), distribution="github_release_asset_authorized_only", redistribution={"public_release": True, "authorization": "user"})
            result = owner.validate_manifest(manifest)
            self.assertFalse(result["ok"])
            self.assertEqual(result["issues"][0]["code"], "explicit_distribution_authorization_reference_missing")

    def test_platform_mismatch_is_deferred_not_claimed_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = manifest_for(Path(temp_dir), platform="windows-x64", implementation_type="platform_binary")
            result = owner.readiness(manifest, owner.variables_for(manifest), Path(temp_dir) / "archives")
            self.assertFalse(result["capability_restore_ready"])
            self.assertTrue(result["statuses"][0]["owner_reacquire_required"])

    def test_platform_owner_receipt_closes_handoff_without_claiming_local_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(
                root,
                platform="windows-x64",
                implementation_type="platform_binary",
                recovery_owner="test-platform-owner",
            )
            receipt_root = root / "receipts"
            self._write_owner_receipt(receipt_root, manifest, "sample-linux-x64")

            result = owner.readiness(
                manifest, owner.variables_for(manifest), root / "archives", receipt_root=receipt_root
            )

            self.assertTrue(result["capability_restore_ready"], result)
            self.assertTrue(result["statuses"][0]["owner_receipt_verified"])
            self.assertFalse(result["statuses"][0]["source_restored"])

    def test_owner_receipt_requires_nonempty_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(
                root,
                platform="windows-x64",
                implementation_type="platform_binary",
                recovery_owner="test-platform-owner",
            )
            receipt_root = root / "receipts"
            self._write_owner_receipt(receipt_root, manifest, "sample-linux-x64")
            receipt_path = receipt_root / "sample-linux-x64.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["acceptance"]["evidence_refs"] = []
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            verified = owner.verify_owner_receipt(
                manifest["bundles"][0], owner.manifest_sha256(manifest), receipt_root
            )

            self.assertFalse(verified["ok"])
            self.assertEqual(verified["reason"], "owner_receipt_acceptance_incomplete")

    def test_stale_or_wrong_manifest_owner_receipts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(
                root,
                platform="windows-x64",
                implementation_type="platform_binary",
                recovery_owner="test-platform-owner",
            )
            receipt_root = root / "receipts"
            for expired, digest, expected in (
                (True, "", "owner_receipt_expired"),
                (False, "0" * 64, "owner_receipt_manifest_mismatch"),
            ):
                with self.subTest(expected=expected):
                    self._write_owner_receipt(
                        receipt_root, manifest, "sample-linux-x64", expired=expired, manifest_sha256=digest
                    )
                    result = owner.readiness(
                        manifest, owner.variables_for(manifest), root / "archives", receipt_root=receipt_root
                    )
                    self.assertFalse(result["capability_restore_ready"])
                    self.assertEqual(result["statuses"][0]["owner_receipt_reason"], expected)

    def test_receipt_is_created_only_from_manifest_declared_owner_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(root, implementation_type="plugin_reacquire", platform="target-platform")
            manifest["bundles"][0].update({
                "recovery_owner": "plugin-owner",
                "recovery_acceptance": {
                    "schemas": ["plugin.validate.v1"], "required_true": ["ok", "projection.ok"],
                    "required_empty": ["projection.missing"], "minimum_evidence_count": 1, "ttl_seconds": 60,
                },
            })
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({"schema": "plugin.validate.v1", "ok": True, "projection": {"ok": True, "missing": []}}), encoding="utf-8")
            result = owner.record_owner_receipt(manifest, "sample-linux-x64", [evidence], root / "receipts", confirm=owner.RECORD_OWNER_RECEIPT_CONFIRM)
            self.assertTrue(result["ok"], result)
            self.assertTrue(owner.readiness(manifest, owner.variables_for(manifest), root / "archives", receipt_root=root / "receipts")["capability_restore_ready"])
            evidence.write_text(json.dumps({"schema": "plugin.validate.v1", "ok": True, "projection": {"ok": True, "missing": ["x"]}}), encoding="utf-8")
            rejected = owner.record_owner_receipt(manifest, "sample-linux-x64", [evidence], root / "receipts2", confirm=owner.RECORD_OWNER_RECEIPT_CONFIRM)
            self.assertFalse(rejected["ok"])

    def test_reconcile_delegates_to_existing_owner_and_reuses_receipt_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(root, implementation_type="plugin_reacquire", platform="target-platform")
            manifest["bundles"][0].update({
                "recovery_owner": "plugin-owner",
                "recovery_acceptance": {
                    "schemas": ["plugin.validate.v1"], "required_true": ["ok"],
                    "minimum_evidence_count": 1, "ttl_seconds": 60,
                },
            })
            evidence = [{"schema": "plugin.validate.v1", "ok": True}]
            with patch.object(owner, "collect_owner_evidence", return_value=evidence) as collect:
                result = owner.reconcile_owner_receipts(
                    manifest, ["sample-linux-x64"], root / "receipts",
                    confirm=owner.RECORD_OWNER_RECEIPT_CONFIRM,
                )
            self.assertTrue(result["ok"], result)
            collect.assert_called_once_with("sample-linux-x64")

    def test_reconcile_rejects_invalid_confirmation_before_collecting_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(root, implementation_type="plugin_reacquire", platform="target-platform")
            manifest["bundles"][0].update({
                "recovery_owner": "plugin-owner",
                "recovery_acceptance": {"schemas": ["plugin.validate.v1"]},
            })
            with patch.object(owner, "collect_owner_evidence") as collect:
                result = owner.reconcile_owner_receipts(
                    manifest, ["sample-linux-x64"], root / "receipts", confirm="wrong-confirmation",
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "confirmation_required")
            collect.assert_not_called()

    def test_owner_reacquired_python_dependency_requires_declared_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = manifest_for(
                Path(temp_dir), implementation_type="owner_reacquired_python_dependency", platform="target-platform",
            )
            result = owner.validate_manifest(manifest)
            self.assertFalse(result["ok"])
            self.assertEqual(
                {issue["code"] for issue in result["issues"]},
                {"recovery_owner_missing", "recovery_acceptance_contract_missing"},
            )

    def test_beautifulsoup_reconcile_collects_runtime_selection_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = manifest_for(
                root, implementation_type="owner_reacquired_python_dependency", platform="target-platform",
            )
            manifest["bundles"][0].update({
                "id": "beautifulsoup4-python-target-runtime",
                "recovery_owner": "managed_python_dependency_runtime",
                "recovery_acceptance": {
                    "schemas": ["managed_python_dependency_runtime.v1.selection"],
                    "required_true": ["ok", "import_probe.ok"],
                    "minimum_evidence_count": 1,
                    "ttl_seconds": 60,
                },
            })
            evidence = {
                "schema": "managed_python_dependency_runtime.v1.selection",
                "ok": True,
                "import_probe": {"ok": True},
            }
            with patch.object(owner, "run_owner_json", return_value=evidence) as run_owner:
                result = owner.reconcile_owner_receipts(
                    manifest, ["beautifulsoup4-python-target-runtime"], root / "receipts",
                    confirm=owner.RECORD_OWNER_RECEIPT_CONFIRM,
                )
            self.assertTrue(result["ok"], result)
            command = run_owner.call_args.args[0]
            self.assertIn("managed_python_dependency_runtime.py", command[1])
            self.assertEqual(command[-2:], ["--package", "beautifulsoup4"])
            readiness = owner.readiness(manifest, owner.variables_for(manifest), root / "archives", receipt_root=root / "receipts")
            self.assertTrue(readiness["capability_restore_ready"], readiness)

    def test_archive_hash_mismatch_and_path_traversal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as content:
                member = tarfile.TarInfo("../escape")
                member.size = 1
                content.addfile(member, io.BytesIO(b"x"))
            entry = {"archive": archive.name, "sha256": owner.sha256_file(archive), "entrypoints": []}
            result = owner.verify_archive(entry, root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "archive_path_traversal")
            entry["sha256"] = "0" * 64
            self.assertEqual(owner.verify_archive(entry, root)["reason"], "archive_hash_mismatch")

    def test_remote_and_plugin_states_require_completion_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "proxy.js").write_text("", encoding="utf-8")
            manifest = {"schema": "mcp_recovery_bundle_manifest.v1", "policy": {"content_addressed": True, "hash_algorithm": "sha256"}, "bundles": [
                {"id": "remote", "implementation_type": "remote_proxy_source", "platform": owner.host_platform(), "source": str(root), "include": ["proxy.js"], "entrypoints": ["proxy.js"], "distribution": "git_snapshot", "required": True, "redistribution": {"public_release": False, "authorization": "workspace_source"}},
                {"id": "plugin", "implementation_type": "plugin_reacquire", "platform": "target-platform", "source": "inventory.json", "include": [], "entrypoints": [], "distribution": "plugin_owner_reacquire", "required": True, "redistribution": {"public_release": False, "authorization": "owner_reacquire"}},
            ]}
            result = owner.readiness(manifest, owner.variables_for(manifest), root / "archives")
            self.assertFalse(result["capability_restore_ready"])
            self.assertEqual(result["remote_reconnect_required"], ["remote"])
            self.assertEqual(result["owner_reacquire_required"], ["plugin"])

    def test_archive_lock_is_reentrant_across_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with owner.archive_lock(root):
                self.assertTrue((root / ".index.lock").is_file())

    def test_materialization_does_not_claim_full_restore_with_owner_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "node_modules" / ".bin").mkdir(parents=True)
            (source / "package.json").write_text("{}", encoding="utf-8")
            (source / "node_modules" / ".bin" / "sample").write_text("#!/bin/sh", encoding="utf-8")
            manifest = manifest_for(source)
            manifest["bundles"].append({
                "id": "plugin",
                "implementation_type": "plugin_reacquire",
                "platform": "target-platform",
                "source": "inventory.json",
                "include": [],
                "entrypoints": [],
                "distribution": "plugin_owner_reacquire",
                "required": True,
                "redistribution": {"public_release": False, "authorization": "owner_reacquire"},
            })
            archive_root = root / "archives"
            owner.build(manifest, owner.variables_for(manifest), archive_root, ["sample-linux-x64"], False, owner.host_platform())
            with patch.object(owner, "tools_list_smoke", return_value={"ok": True, "tool_count": 1}):
                result = owner.materialize(manifest, owner.variables_for(manifest), archive_root, root / "stage")
            self.assertFalse(result["capability_restore_ready"])
            self.assertEqual(result["owner_handoffs_pending"], ["plugin"])


if __name__ == "__main__":
    unittest.main()
