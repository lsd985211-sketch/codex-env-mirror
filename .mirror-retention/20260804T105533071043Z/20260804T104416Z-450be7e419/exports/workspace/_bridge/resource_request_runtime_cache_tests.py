#!/usr/bin/env python3
"""Focused tests for mode-aware resource owner result caching."""

from __future__ import annotations

import json
import unittest

from resource_request_runtime_cache import owner_result_cache_allowed, owner_result_cache_key


class ResourceRequestRuntimeCacheTests(unittest.TestCase):
    def test_package_mode_and_lock_signature_are_cache_dimensions(self) -> None:
        base = {"target": "beautifulsoup4", "metadata": {"package_ecosystem": "python"}}
        inspect = {
            "target": "beautifulsoup4",
            "metadata": {
                "package_ecosystem": "python",
                "package_execution_mode": "inspect_then_install",
                "dependency_lock_signature": "lock-a",
            },
        }
        other_lock = {
            "target": "beautifulsoup4",
            "metadata": {
                "package_ecosystem": "python",
                "package_execution_mode": "inspect_then_install",
                "dependency_lock_signature": "lock-b",
            },
        }
        self.assertNotEqual(owner_result_cache_key("package_manager", base), owner_result_cache_key("package_manager", inspect))
        self.assertNotEqual(owner_result_cache_key("package_manager", inspect), owner_result_cache_key("package_manager", other_lock))
        json.loads(owner_result_cache_key("package_manager", inspect))

    def test_write_mode_never_uses_read_only_owner_cache(self) -> None:
        inspect = {"target": "beautifulsoup4", "metadata": {"package_execution_mode": "inspect_then_install"}}
        automatic = {
            "target": "beautifulsoup4",
            "allow_filesystem_write": True,
            "metadata": {"package_action": "install", "package_execution_mode": "verified_auto_install"},
        }
        self.assertTrue(owner_result_cache_allowed("package_manager", inspect))
        self.assertFalse(owner_result_cache_allowed("package_manager", automatic))


if __name__ == "__main__":
    unittest.main()
