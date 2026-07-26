from __future__ import annotations

import io
import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

import developer_toolchain_owner as owner


class DeveloperToolchainOwnerTests(unittest.TestCase):
    def test_production_lock_declares_all_tools_required(self) -> None:
        contracts = owner.desired_executable_contracts()
        self.assertEqual({"rg", "fd", "uv", "uvx", "ruff", "gh", "jq"}, set(contracts))
        self.assertTrue(all(item["required"] for item in contracts.values()))

    def test_hash_locked_single_binary_is_installed_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "managed" / "jq" / "1.8.2"
            artifact = target / "download" / "jq-linux-amd64"
            artifact.parent.mkdir(parents=True)
            content = b"#!/bin/sh\nprintf 'jq-1.8.2\\n'\n"
            artifact.write_bytes(content)
            component = {
                "id": "jq",
                "artifact_sha256": hashlib.sha256(content).hexdigest(),
                "artifact_url": "https://example.invalid/jq-linux-amd64",
                "artifact_name": "jq-linux-amd64",
                "max_bytes": "5MB",
                "executables": [{"name": "jq", "relative_path": "jq"}],
            }
            paths = {
                "store": root / "store",
                "receipts": root / "receipts.jsonl",
                "resource_log": root / "resource.jsonl",
            }

            result = owner._install_binary_component(component, target, paths)
            executable = target / "jq"

            self.assertTrue(result["ok"])
            self.assertEqual(content, executable.read_bytes())
            self.assertTrue(executable.stat().st_mode & 0o111)

    def test_projection_refuses_foreign_path_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "managed"
            target = install_root / "uv" / "bin" / "uv"
            target.parent.mkdir(parents=True)
            target.write_text("uv", encoding="utf-8")
            link = root / "bin" / "uv"
            link.parent.mkdir()
            link.write_text("foreign", encoding="utf-8")

            result = owner._project_executable(target, link, install_root)

            self.assertFalse(result["ok"])
            self.assertEqual("foreign_path_projection_refused", result["reason"])
            self.assertEqual("foreign", link.read_text(encoding="utf-8"))

    def test_projection_atomically_creates_managed_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "managed"
            target = install_root / "ruff" / "bin" / "ruff"
            target.parent.mkdir(parents=True)
            target.write_text("ruff", encoding="utf-8")
            link = root / "bin" / "ruff"

            result = owner._project_executable(target, link, install_root)

            self.assertTrue(result["ok"])
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.resolve(), link.resolve())

    def test_appserver_path_shim_forwards_to_managed_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "managed" / "gh"
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/sh\nprintf 'managed:%s\\n' \"$1\"\n", encoding="utf-8")
            target.chmod(0o755)

            result = owner._project_appserver_path_shim(target, root / "appserver-bin")
            invocation = owner._run([str(root / "appserver-bin" / "gh"), "--version"], timeout=10)

            self.assertTrue(result["ok"])
            self.assertTrue(invocation["ok"])
            self.assertEqual("managed:--version", invocation["stdout"].strip())

    def test_appserver_path_shim_refuses_foreign_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "managed" / "gh"
            target.parent.mkdir(parents=True)
            target.write_text("managed", encoding="utf-8")
            shim = root / "appserver-bin" / "gh"
            shim.parent.mkdir()
            shim.write_text("foreign", encoding="utf-8")

            result = owner._project_appserver_path_shim(target, shim.parent)

            self.assertFalse(result["ok"])
            self.assertEqual("foreign_appserver_path_projection_refused", result["reason"])
            self.assertEqual("foreign", shim.read_text(encoding="utf-8"))

    def test_safe_extract_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                info = tarfile.TarInfo("../escape")
                data = b"bad"
                info.size = len(data)
                handle.addfile(info, io.BytesIO(data))

            result = owner._safe_extract_tar(archive, root / "target")

            self.assertFalse(result["ok"])
            self.assertEqual("archive_path_escape", result["reason"])
            self.assertFalse((root / "escape").exists())

    def test_github_wrapper_uses_native_binary_without_persisting_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native = root / "release" / "bin" / "gh"
            native.parent.mkdir(parents=True)
            native.write_text("#!/bin/sh\nprintf 'native:%s token:%s\\n' \"$1\" \"${GH_TOKEN:+present}\"\n", encoding="utf-8")
            native.chmod(0o755)
            host = root / "gh.exe"
            host.write_text("#!/bin/sh\nprintf 'ephemeral-test-token\\n'\n", encoding="utf-8")
            host.chmod(0o755)
            executable = {
                "relative_path": "bin/gh",
                "source_relative_path": "release/bin/gh",
                "credential_bridge": {
                    "type": "windows_gh_token",
                    "host_command": str(host),
                    "host_override_env": "TEST_WINDOWS_GH_PATH",
                },
            }

            result = owner._write_credential_bridge_wrapper(root, executable)
            invocation = owner._run([str(root / "bin" / "gh"), "api"], timeout=10)

            self.assertTrue(result["ok"])
            self.assertTrue(invocation["ok"])
            self.assertEqual("native:api token:present", invocation["stdout"].strip())
            self.assertNotIn("ephemeral-test-token", (root / "bin" / "gh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
