#!/usr/bin/env python3
"""Focused tests for runtime-aware atomic Python dependency installation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import resource_python_package_installer as installer


class PythonPackageInstallerTests(unittest.TestCase):
    def test_runtime_identity_matches_current_interpreter(self) -> None:
        identity = installer.runtime_identity()
        self.assertEqual(identity["abi_tag"], f"cp{installer.sys.version_info.major}{installer.sys.version_info.minor}")

    def test_default_target_is_scoped_to_current_runtime(self) -> None:
        target, explicit = installer._target_dir({}, "ddgs")
        self.assertFalse(explicit)
        self.assertEqual(target.parent.name, "ddgs")
        self.assertIn(installer.runtime_identity()["abi_tag"], target.name)

    def test_atomic_install_replaces_stale_tree_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ddgs"
            target.mkdir()
            (target / "stale.txt").write_text("stale", encoding="utf-8")

            def fake_run(command, **_kwargs):
                staging = Path(command[command.index("--target") + 1])
                (staging / "fresh.txt").write_text("fresh", encoding="utf-8")
                return mock.Mock(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(installer.subprocess, "run", side_effect=fake_run):
                result, _ = installer._atomic_install_target(target, "ddgs==9.14.4", {}, 30)
            self.assertTrue(result["ok"])
            self.assertFalse((target / "stale.txt").exists())
            self.assertEqual((target / "fresh.txt").read_text(encoding="utf-8"), "fresh")

    def test_failed_install_keeps_existing_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ddgs"
            target.mkdir()
            (target / "stale.txt").write_text("stale", encoding="utf-8")
            with mock.patch.object(
                installer.subprocess,
                "run",
                return_value=mock.Mock(returncode=1, stdout="", stderr="failed"),
            ):
                result, _ = installer._atomic_install_target(target, "ddgs==9.14.4", {}, 30)
            self.assertFalse(result["ok"])
            self.assertTrue((target / "stale.txt").exists())

    def test_failed_import_smoke_keeps_existing_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ddgs"
            target.mkdir()
            (target / "stable.txt").write_text("stable", encoding="utf-8")

            def fake_run(command, **_kwargs):
                staging = Path(command[command.index("--target") + 1])
                (staging / "ddgs").mkdir()
                (staging / "ddgs" / "__init__.py").write_text("", encoding="utf-8")
                return mock.Mock(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(installer.subprocess, "run", side_effect=fake_run), mock.patch.object(
                installer.managed_python_runtime,
                "probe_imports",
                return_value={"ok": False, "error": "ImportError: lxml.etree ABI mismatch"},
            ):
                result, _ = installer._atomic_install_target(
                    target,
                    "ddgs==9.14.4",
                    {},
                    30,
                    required_imports=("ddgs", "lxml.etree"),
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "managed_dependency_import_smoke_failed")
            self.assertTrue((target / "stable.txt").exists())

    def test_locked_dependency_uses_hash_checking_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "beautifulsoup4"
            observed: dict[str, object] = {}

            def fake_run(command, **_kwargs):
                observed["command"] = list(command)
                requirements = Path(command[command.index("-r") + 1])
                observed["requirements"] = requirements.read_text(encoding="utf-8")
                staging = Path(command[command.index("--target") + 1])
                (staging / "bs4").mkdir()
                (staging / "bs4" / "__init__.py").write_text("", encoding="utf-8")
                return mock.Mock(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(installer.subprocess, "run", side_effect=fake_run), mock.patch.object(
                installer.managed_python_runtime,
                "probe_imports",
                return_value={"ok": True},
            ):
                result, _ = installer._atomic_install_target(
                    target,
                    "beautifulsoup4==4.15.0",
                    {},
                    30,
                    required_imports=("bs4",),
                    locked_requirements=installer.managed_python_runtime.locked_requirement_lines("beautifulsoup4"),
                )
            self.assertTrue(result["ok"])
            self.assertIn("--require-hashes", observed["command"])
            self.assertIn("--no-deps", observed["command"])
            self.assertEqual(str(observed["requirements"]).count("#sha256="), 3)

    def test_ready_runtime_target_is_reused_without_pip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "beautifulsoup4"
            target.mkdir()
            request = {
                "target": "beautifulsoup4==4.15.0",
                "allow_filesystem_write": True,
                "metadata": {
                    "package_action": "install",
                    "package_spec": "beautifulsoup4==4.15.0",
                    "install_approved": True,
                    "authorization": {
                        "grant_ref": "scoped-authorization:grant:test",
                        "operation_id": "install-test",
                        "thread_id": "turn-test",
                    },
                },
            }
            with mock.patch.object(installer, "_target_dir", return_value=(target, False)), mock.patch.object(
                installer, "_installed_version", return_value="4.15.0"
            ), mock.patch.object(
                installer.managed_python_runtime, "probe_imports", return_value={"ok": True, "imports": [{"name": "bs4", "ok": True}]}
            ), mock.patch.object(
                installer,
                "consume_install_authorization",
                return_value={"ok": True, "operation_id": "install-test", "grant_ref": "scoped-authorization:grant:test"},
            ), mock.patch.object(
                installer, "start_authorized_effect", return_value={"ok": True}
            ), mock.patch.object(
                installer, "finish_authorized_effect", return_value={"ok": True, "status": "completed"}
            ), mock.patch.object(installer, "_atomic_install_target") as install:
                actual = installer.execute_python_package_install(request, "beautifulsoup4", {}, 30, lambda **payload: payload)
            self.assertTrue(actual["ok"])
            self.assertTrue(actual["metadata"]["reused_existing_install"])
            self.assertFalse(actual["metadata"]["will_install"])
            self.assertFalse(actual["writes_files"])
            install.assert_not_called()

    def test_missing_scoped_authorization_blocks_before_pip(self) -> None:
        request = {
            "target": "beautifulsoup4==4.15.0",
            "allow_filesystem_write": True,
            "metadata": {
                "package_action": "install",
                "package_spec": "beautifulsoup4==4.15.0",
                "install_approved": True,
            },
        }
        with mock.patch.object(installer, "_atomic_install_target") as install:
            actual = installer.execute_python_package_install(
                request, "beautifulsoup4", {}, 30, lambda **payload: payload
            )
        self.assertEqual(actual["error_class"], "scoped_authorization_required")
        self.assertIn("authorization.grant_ref", actual["metadata"]["authorization"]["missing"])
        install.assert_not_called()

    def test_authorization_scope_and_operation_are_consumed_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "beautifulsoup4"
            request = {
                "metadata": {
                    "authorization": {
                        "grant_ref": "scoped-authorization:grant:abc",
                        "operation_id": "install-operation",
                        "thread_id": "turn-one",
                    }
                }
            }
            with mock.patch.object(
                installer.scoped_authorization,
                "consume_grant",
                return_value={"ok": True, "consumption_ref": "receipt"},
            ) as consume:
                actual = installer.consume_install_authorization(
                    request, "beautifulsoup4", target, "beautifulsoup4==4.15.0"
                )
            self.assertTrue(actual["ok"])
            scope = consume.call_args.args[1]
            self.assertEqual(scope["thread_id"], "turn-one")
            self.assertEqual(scope["action"], "resource.package.install")
            self.assertEqual(scope["source_signature"], installer.managed_python_runtime.dependency_lock_signature("beautifulsoup4"))
            self.assertEqual(consume.call_args.kwargs["consumer_owner"], installer.AUTHORIZATION_OWNER)
            self.assertEqual(consume.call_args.kwargs["operation_id"], "install-operation")


if __name__ == "__main__":
    unittest.main()
