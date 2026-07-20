#!/usr/bin/env python3
"""Focused health-contract tests for the isolated DDGS worker."""

from __future__ import annotations

import tempfile
import unittest
import os
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
            with mock.patch.object(search, "DEPENDENCY_ROOT", Path(temp_dir) / "missing"):
                payload = search._dependency_state()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "ddgs_dependency_missing")
        self.assertEqual(payload["required_package"], "ddgs==9.14.4")
        if os.name != "nt":
            self.assertTrue(payload["platform_deferred"])
            self.assertEqual(payload["execution_owner"], "windows_host_compatibility_projection")


if __name__ == "__main__":
    unittest.main()
