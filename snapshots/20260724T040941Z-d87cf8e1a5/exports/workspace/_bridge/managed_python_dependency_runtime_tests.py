#!/usr/bin/env python3
"""Focused regressions for ABI-scoped managed Python dependencies."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import managed_python_dependency_runtime as runtime


class ManagedPythonDependencyRuntimeTests(unittest.TestCase):
    def test_runtime_key_includes_python_abi_and_platform(self) -> None:
        identity = {"abi_tag": "cp314", "platform_tag": "win_amd64"}
        self.assertEqual(runtime.runtime_key(identity), "cp314-win_amd64")

    def test_default_target_is_runtime_scoped(self) -> None:
        identity = {"abi_tag": "cp314", "platform_tag": "win_amd64"}
        target = runtime.scoped_dependency_target(Path("C:/managed"), "DDGS", identity)
        self.assertEqual(target.as_posix(), "C:/managed/ddgs/cp314-win_amd64")

    def test_incompatible_legacy_tree_is_not_selected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            legacy = root / "ddgs"
            (legacy / "ddgs").mkdir(parents=True)
            with mock.patch.object(runtime, "probe_imports", return_value={"ok": False, "error": "cp312 != cp314"}):
                selected = runtime.select_dependency_target(
                    root,
                    "ddgs",
                    identity={"abi_tag": "cp314", "platform_tag": "win_amd64"},
                    python_executable=Path("python.exe"),
                )
        self.assertFalse(selected["ok"])
        self.assertEqual(selected["reason"], "runtime_scoped_dependency_missing")
        self.assertEqual(selected["selected_kind"], "runtime_scoped")

    def test_compatible_legacy_tree_remains_a_bounded_migration_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            legacy = root / "ddgs"
            (legacy / "ddgs").mkdir(parents=True)
            with mock.patch.object(runtime, "probe_imports", return_value={"ok": True, "runtime": {"abi_tag": "cp312"}}):
                selected = runtime.select_dependency_target(
                    root,
                    "ddgs",
                    identity={"abi_tag": "cp312", "platform_tag": "win_amd64"},
                    python_executable=Path("python.exe"),
                )
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["selected_kind"], "legacy_compatible")
        self.assertEqual(Path(selected["path"]), legacy)

    def test_cli_select_requires_dependency_root(self) -> None:
        with mock.patch.object(runtime.sys, "argv", ["managed_python_dependency_runtime.py", "select"]), mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(runtime.main(), 1)
        self.assertIn("dependency_root_required", str(output.call_args))


if __name__ == "__main__":
    unittest.main()
