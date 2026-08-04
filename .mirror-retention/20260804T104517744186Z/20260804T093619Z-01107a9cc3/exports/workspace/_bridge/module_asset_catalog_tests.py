from __future__ import annotations

import unittest

from module_asset_catalog import asset_category, catalog_record, catalog_records


class ModuleAssetCatalogTests(unittest.TestCase):
    def test_windows_memory_is_system_diagnostics_not_long_term_memory(self) -> None:
        module = {
            "module": "_bridge\\windows_memory_governance.py",
            "purpose": "memory_governance_component",
            "boundary": "knowledge_memory_skill",
            "capability_terms": ["windows", "memory", "process", "doctor"],
            "public_entrypoints": [],
        }

        self.assertEqual(asset_category(module), "system_diagnostics")

    def test_full_records_take_priority_over_bounded_group_views(self) -> None:
        catalog = {
            "records": [{"module": "a.py"}, {"module": "b.py"}],
            "groups": {"general": [{"module": "a.py"}]},
        }

        self.assertEqual([item["module"] for item in catalog_records(catalog)], ["a.py", "b.py"])

    def test_legacy_grouped_catalog_remains_readable(self) -> None:
        catalog = {"groups": {"general": [{"module": "a.py"}]}}

        self.assertEqual(catalog_records(catalog), [{"module": "a.py"}])

    def test_indexed_module_is_discovery_only_until_owner_admission(self) -> None:
        record = catalog_record(
            {
                "module": "_bridge/ocr_owner.py",
                "owner_cli": "_bridge/ocr_owner.py",
                "public_entrypoints": [{"name": "status", "kind": "function", "line": 10}],
                "validation": ["ocr_owner.py validate"],
            }
        )

        self.assertTrue(record["observed"])
        self.assertFalse(record["admitted"])
        self.assertFalse(record["callable"])
        self.assertEqual(record["risk_class"], "discovery_only")
        self.assertEqual(len(record["candidate_signature"]), 64)
        self.assertIn("owner_contract", record["missing_requirements"])

    def test_unknown_script_and_outside_root_never_become_callable_owner(self) -> None:
        record = catalog_record(
            {
                "module": "/tmp/restart-service.sh",
                "owner_cli": "/tmp/restart-service.sh",
                "validation": [],
                "public_entrypoints": [],
            }
        )

        self.assertEqual(record["owner_ref"], "")
        self.assertFalse(record["admitted"])
        self.assertFalse(record["callable"])
        self.assertIn("outside_declared_discovery_root", record["risk_hints"])
        self.assertIn("unsupported_asset_type", record["risk_hints"])

    def test_candidate_signature_is_stable_and_projection_omits_sensitive_input(self) -> None:
        module = {
            "module": "_bridge/example_owner.py",
            "owner_cli": "_bridge/example_owner.py",
            "validation": ["example_owner.py validate"],
            "public_entrypoints": [{"name": "validate", "kind": "function", "line": 4}],
            "authorization_token": "must-not-project",
            "command_body": "secret command",
        }
        first = catalog_record(module)
        second = catalog_record(dict(reversed(list(module.items()))))

        self.assertEqual(first["candidate_signature"], second["candidate_signature"])
        self.assertNotIn("authorization_token", first)
        self.assertNotIn("command_body", first)


if __name__ == "__main__":
    unittest.main()
