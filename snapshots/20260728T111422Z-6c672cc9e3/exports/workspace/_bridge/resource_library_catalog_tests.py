#!/usr/bin/env python3
from __future__ import annotations

import unittest

import resource_library_catalog as catalog


class ResourceLibraryCatalogTests(unittest.TestCase):
    def test_catalog_covers_required_domains(self) -> None:
        ids = {item["id"] for item in catalog.MODULES}
        self.assertTrue({"mail", "scheduler", "memory", "backups", "records", "resources", "websites"}.issubset(ids))

    def test_readme_contains_owner_boundaries(self) -> None:
        rendered = catalog.render_readme(catalog.snapshot())
        self.assertIn(catalog.GENERATED_MARKER, rendered)
        self.assertIn("权威状态", rendered)
        self.assertIn("维护入口", rendered)


if __name__ == "__main__":
    unittest.main()
