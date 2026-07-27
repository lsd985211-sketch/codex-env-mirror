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


if __name__ == "__main__":
    unittest.main()
