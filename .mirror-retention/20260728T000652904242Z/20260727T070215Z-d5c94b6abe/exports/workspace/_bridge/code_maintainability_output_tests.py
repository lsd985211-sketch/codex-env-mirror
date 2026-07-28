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
    def test_batch_alone_does_not_claim_resource_acquisition(self) -> None:
        result = code_maintainability.classify_change_kind(
            "Batch already-approved review items through their existing owner lifecycle.",
            [],
        )

        self.assertNotEqual(result["kind"], "resource_acquisition")

    def test_generic_scheduler_language_does_not_claim_resource_acquisition(self) -> None:
        result = code_maintainability.classify_change_kind(
            "Reuse the unified scheduler for the approved review lifecycle.",
            [],
        )

        self.assertNotEqual(result["kind"], "resource_acquisition")

    def test_explicit_non_large_owner_target_is_not_displaced_by_broad_lookup(self) -> None:
        args = argparse.Namespace(
            root=[],
            all_bridge=False,
            include_excluded=False,
            term=[],
            task_mode="code",
            message="Add a review lifecycle readback facade.",
            target="workspace/_bridge/workflow_iteration_owner.py",
            limit=12,
            issue_limit=80,
            large_file_lines=1200,
            large_function_lines=160,
            large_function_decisions=30,
            json=False,
        )
        route = {
            "module": "_bridge/workflow_iteration_owner.py",
            "large_file_risk": False,
            "issues": [],
        }
        unrelated = {"ok": True, "match": {"module": "_bridge/workflow_validation.py"}}
        with patch.object(code_maintainability, "module_context", return_value={"routes": [route]}), patch.object(
            code_maintainability,
            "lookup_existing_purpose_module",
            return_value=unrelated,
        ):
            result = code_maintainability.placement_plan(args)

        self.assertEqual(result["owner_module"], "_bridge/workflow_iteration_owner.py")
        self.assertEqual(result["recommended_placement"], "owner_module")
        self.assertEqual(result["existing_purpose"]["reason"], "explicit_target_owner_boundary")

    def test_explicit_owner_target_survives_missing_derived_module_index(self) -> None:
        args = argparse.Namespace(
            root=[],
            all_bridge=False,
            include_excluded=False,
            term=["approval", "lifecycle"],
            task_mode="code",
            message="Reuse the unified scheduler for the approved review lifecycle.",
            target="workspace/_bridge/workflow_iteration_owner.py",
            limit=12,
            issue_limit=80,
            large_file_lines=1200,
            large_function_lines=160,
            large_function_decisions=30,
            json=False,
        )
        with patch.object(code_maintainability, "module_context", return_value={"routes": []}), patch.object(
            code_maintainability,
            "lookup_existing_purpose_module",
            return_value={"ok": False, "reason": "module_capability_index_missing"},
        ):
            result = code_maintainability.placement_plan(args)

        self.assertEqual(result["owner_module"], "_bridge/workflow_iteration_owner.py")
        self.assertEqual(result["recommended_placement"], "owner_module")
        self.assertEqual(result["existing_purpose"]["reason"], "explicit_target_owner_boundary")

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
        self.assertEqual(result["command_contract"]["command"], "module-context")
        accepted_flags = {
            flag
            for option in result["command_contract"]["accepted_options"]
            for flag in option["flags"]
        }
        self.assertIn("--root", accepted_flags)
        self.assertIn("--target", accepted_flags)
        self.assertNotIn("--path", accepted_flags)
        for route in result["routes"]:
            self.assertLessEqual(len(route["public_entrypoints"]), 6)
            self.assertLessEqual(len(route["issues"]), 4)


if __name__ == "__main__":
    unittest.main()
