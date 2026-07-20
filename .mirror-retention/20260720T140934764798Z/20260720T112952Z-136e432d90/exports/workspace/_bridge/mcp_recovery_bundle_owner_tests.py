from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
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
