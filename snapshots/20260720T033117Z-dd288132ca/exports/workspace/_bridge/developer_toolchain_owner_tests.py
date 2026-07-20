from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import developer_toolchain_owner as owner


class DeveloperToolchainOwnerTests(unittest.TestCase):
    def test_production_lock_declares_all_tools_required(self) -> None:
        contracts = owner.desired_executable_contracts()
        self.assertEqual({"rg", "fd", "uv", "uvx", "ruff", "gh"}, set(contracts))
        self.assertTrue(all(item["required"] for item in contracts.values()))

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
