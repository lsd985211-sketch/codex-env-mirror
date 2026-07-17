from __future__ import annotations

import unittest

from module_asset_catalog import asset_category, catalog_records


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


if __name__ == "__main__":
    unittest.main()
