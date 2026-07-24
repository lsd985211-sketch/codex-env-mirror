#!/usr/bin/env python3
"""Focused health-contract tests for the isolated DDGS worker."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import local_mcp_hub_resource_search as search


class ResourceSearchHealthTests(unittest.TestCase):
    def test_runtime_identity_has_abi_tag(self) -> None:
        identity = search._runtime_identity()
        self.assertRegex(identity["abi_tag"], r"^cp[0-9]+$")
        self.assertTrue(identity["python_version"])

    def test_missing_dependency_is_machine_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(search, "DEPENDENCY_BASE_ROOT", Path(temp_dir) / "missing"):
                payload = search._dependency_state()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "runtime_scoped_dependency_missing")
        self.assertEqual(payload["required_package"], "ddgs==9.14.4")
        self.assertEqual(payload["runtime_key"], search.managed_python_runtime.runtime_key())
        self.assertEqual(payload["selected_kind"], "runtime_scoped")
        self.assertEqual(payload["execution_owner"], "resource_package_owner")
        self.assertNotIn("platform_deferred", payload)

    def test_worker_timeout_honors_requested_total_budget(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "status": "completed"}),
            stderr="",
        )
        with mock.patch.object(search.subprocess, "run", return_value=completed) as run:
            payload = search.resource_search_call("resource_search.text", {"query": "python", "timeout_seconds": 45})
        self.assertTrue(payload["ok"])
        self.assertEqual(run.call_args.kwargs["timeout"], 45 + search.WORKER_EXIT_GRACE_SECONDS)

    def test_auto_backends_share_the_total_budget(self) -> None:
        timeouts: list[int] = []
        regions: list[str] = []

        class FakeClient:
            def text(self, **kwargs):
                regions.append(kwargs["region"])
                return []

        def factory(**kwargs):
            timeouts.append(kwargs["timeout"])
            return FakeClient()

        with mock.patch.object(
            search,
            "_dependency_state",
            return_value={
                "ok": True,
                "factory": factory,
                "dependency_root": "",
                "runtime_available_backends": {"text": ["duckduckgo", "brave"]},
            },
        ):
            payload = search._resource_search_call_in_process(
                "resource_search.text",
                {"query": "python", "timeout_seconds": 45, "region": "wt-wt"},
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(timeouts, [21])
        self.assertEqual(regions, ["us-en", "us-en"])

    def test_disabled_runtime_backend_is_rejected_without_factory_call(self) -> None:
        factory = mock.Mock()
        with mock.patch.object(
            search,
            "_dependency_state",
            return_value={
                "ok": True,
                "factory": factory,
                "dependency_root": "",
                "runtime_available_backends": {"text": ["duckduckgo", "brave"]},
            },
        ):
            payload = search._resource_search_call_in_process(
                "resource_search.text",
                {"query": "python", "backend": "bing"},
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_class"], "unsupported_search_backend")
        self.assertEqual(payload["allowed_backends"], ["duckduckgo", "brave"])
        factory.assert_not_called()

    def test_tool_schema_matches_runtime_timeout_and_region_defaults(self) -> None:
        text_spec = next(item for item in search.resource_search_tool_specs() if item["name"] == "resource_search.text")
        properties = text_spec["inputSchema"]["properties"]
        self.assertEqual(properties["region"]["default"], "us-en")
        self.assertEqual(properties["timeout_seconds"]["maximum"], search.MAX_SEARCH_TIMEOUT_SECONDS)

    def test_current_runtime_scoped_tree_wins_over_legacy(self) -> None:
        identity = {"abi_tag": "cp314", "platform_tag": "win_amd64"}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scoped = root / "ddgs" / "cp314-win_amd64"
            (scoped / "ddgs").mkdir(parents=True)
            (root / "ddgs" / "ddgs").mkdir(exist_ok=True)
            with mock.patch.object(search, "DEPENDENCY_BASE_ROOT", root), mock.patch.object(
                search.managed_python_runtime, "runtime_identity", return_value=identity
            ), mock.patch.object(
                search.managed_python_runtime, "probe_imports", return_value={"ok": True}
            ):
                selection = search._dependency_selection()
        self.assertTrue(selection["ok"])
        self.assertEqual(selection["selected_kind"], "runtime_scoped")
        self.assertEqual(Path(selection["path"]), scoped)

    def test_incompatible_cp312_legacy_tree_is_not_selected_for_cp314(self) -> None:
        identity = {"abi_tag": "cp314", "platform_tag": "win_amd64"}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ddgs" / "ddgs").mkdir(parents=True)
            with mock.patch.object(search, "DEPENDENCY_BASE_ROOT", root), mock.patch.object(
                search.managed_python_runtime, "runtime_identity", return_value=identity
            ), mock.patch.object(
                search.managed_python_runtime,
                "probe_imports",
                return_value={"ok": False, "error": "cp312 extension cannot load in cp314"},
            ):
                selection = search._dependency_selection()
        self.assertFalse(selection["ok"])
        self.assertEqual(selection["selected_kind"], "runtime_scoped")
        self.assertEqual(selection["reason"], "runtime_scoped_dependency_missing")
        self.assertFalse(selection["legacy_probe"]["ok"])


if __name__ == "__main__":
    unittest.main()
