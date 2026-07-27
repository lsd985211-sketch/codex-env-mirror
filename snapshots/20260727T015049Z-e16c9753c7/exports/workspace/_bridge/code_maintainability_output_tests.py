#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import code_maintainability
import module_asset_catalog
from module_asset_catalog import bounded_catalog_output


class ModuleOutputBudgetTests(unittest.TestCase):
    def test_module_path_identity_is_platform_neutral(self) -> None:
        self.assertEqual(
            code_maintainability.normalize_module_path("_bridge\\shared\\owner.py"),
            code_maintainability.normalize_module_path("_bridge/shared/owner.py"),
        )

    def test_module_lookup_clamps_untrusted_limit(self) -> None:
        modules = [
            {"module": f"_bridge/module_{index}.py", "purpose": "workflow", "boundary": "test", "capability_terms": ["workflow"], "public_entrypoints": []}
            for index in range(300)
        ]
        args = argparse.Namespace(limit=1000, term=["workflow"])
        with patch.object(code_maintainability, "load_module_index", return_value={"ok": True, "modules": modules}):
            result = code_maintainability.lookup_module(args)

        self.assertEqual(len(result["matches"]), 200)
        self.assertEqual(result["output_budget"]["effective_limit"], 200)

    def test_catalog_lookup_clamps_untrusted_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "catalog.json"
            catalog_path.write_text(
                __import__("json").dumps(
                    {
                        "ok": True,
                        "groups": {"test": [{"module": f"module_{index}.py", "purpose": "resource", "category": "test", "roles": []} for index in range(300)]},
                        "task_mode_views": {"code": {"focus": []}},
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(limit=1000, term=["resource"], task_mode="code", rebuild=False)
            with patch.object(module_asset_catalog, "CATALOG_PATH", catalog_path):
                result = module_asset_catalog.lookup_catalog(args)

        self.assertEqual(len(result["matches"]), 200)
        self.assertEqual(result["output_budget"]["effective_limit"], 200)

    def test_runtime_dependencies_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendored = root / "runtime_dependencies" / "package"
            vendored.mkdir(parents=True)
            source = vendored / "module.py"
            source.write_text("x = 1\n", encoding="utf-8")

            self.assertEqual(code_maintainability.iter_python_files([root]), [])
            self.assertEqual(code_maintainability.iter_python_files([root], include_excluded=True), [source])

    def test_catalog_build_output_obeys_total_limit(self) -> None:
        catalog = {
            "ok": True,
            "generated_at": "now",
            "catalog_path": "catalog.json",
            "source_scope": "all_bridge",
            "module_count": 50,
            "category_counts": {"test": 50},
            "role_counts": {},
            "lifecycle_counts": {},
            "groups": {
                "test": [
                    {"module": f"module_{index}.py", "category": "test", "purpose": "test"}
                    for index in range(50)
                ]
            },
            "rules": {},
        }

        result = bounded_catalog_output(catalog, limit=7, task_mode="maintenance")

        self.assertEqual(len(result["records"]), 7)
        self.assertEqual(result["output_budget"]["returned_record_count"], 7)
        self.assertTrue(result["output_budget"]["strict_total_record_limit"])

    def test_module_context_obeys_route_and_nested_limits(self) -> None:
        large_route = {
            "path": "_bridge/example.py",
            "line_count": 2000,
            "max_function": {"name": "large", "line_count": 500},
            "public_entrypoints": [
                {"name": f"entry_{index}", "kind": "function", "line": index}
                for index in range(20)
            ],
        }
        payload = {
            "scan_scope": {"file_count": 10},
            "largest_files": [{**large_route, "path": f"_bridge/example_{index}.py"} for index in range(10)],
            "issues": [
                {"kind": "large_function", "path": f"_bridge/example_{route}.py", "line": issue}
                for route in range(10)
                for issue in range(10)
            ],
        }
        args = argparse.Namespace(limit=3, term=[])

        with patch.object(code_maintainability, "snapshot", return_value=payload):
            result = code_maintainability.module_context(args)

        self.assertEqual(len(result["routes"]), 3)
        self.assertTrue(result["output_budget"]["strict_total_record_limit"])
        for route in result["routes"]:
            self.assertLessEqual(len(route["public_entrypoints"]), 6)
            self.assertLessEqual(len(route["issues"]), 4)


if __name__ == "__main__":
    unittest.main()
