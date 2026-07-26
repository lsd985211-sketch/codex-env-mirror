#!/usr/bin/env python3

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import headroom_runtime


class HeadroomRuntimeTests(unittest.TestCase):
    def test_dependency_root_uses_canonical_work_git_runtime_from_linked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            canonical_root = Path(temp_dir) / "work-git"
            dependency_root = canonical_root / "workspace" / "_bridge" / "runtime_dependencies"
            dependency_root.mkdir(parents=True)
            with patch.dict(os.environ, {"CODEX_HEADROOM_DEPENDENCY_ROOT": ""}), patch.object(
                headroom_runtime.platform_paths,
                "wsl_worktree_linux_root",
                return_value=str(canonical_root),
            ):
                result = headroom_runtime.dependency_root()
        self.assertEqual(result, dependency_root.resolve())

    def test_status_requires_pinned_version_and_managed_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "headroom-ai" / "cp314-linux_x86_64"
            selection = {"ok": True, "path": str(target), "reason": "runtime_scoped_dependency_ready"}
            with patch.object(headroom_runtime.managed_python_runtime, "select_dependency_target", return_value=selection), patch.object(
                headroom_runtime, "_installed_version", return_value=headroom_runtime.EXPECTED_VERSION
            ):
                result = headroom_runtime.status(dependency=Path(temp_dir), state=Path(temp_dir) / "state")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state_contract"], "ttl_bound_reversible_context_cache_not_long_term_memory")
        self.assertTrue(result["pmb_authority_preserved"])

    def test_command_uses_owner_launcher_not_generic_headroom_cli(self) -> None:
        with patch.object(headroom_runtime, "status", return_value={"ok": True}):
            result = headroom_runtime.command_spec(dependency=Path("/tmp/deps"), state=Path("/tmp/state"))
        self.assertTrue(result["ok"])
        self.assertIn("serve", result["command"])
        self.assertNotIn("headroom", result["command"][:1])

    def test_validate_requires_mcp_import_contract(self) -> None:
        with patch.object(headroom_runtime, "status", return_value={"ok": True}), patch.object(
            headroom_runtime.managed_python_runtime,
            "required_imports",
            return_value=("headroom",),
        ):
            result = headroom_runtime.validate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "headroom_mcp_import_contract_missing")


if __name__ == "__main__":
    unittest.main()
