from __future__ import annotations

import unittest

from org_context_graph.catalog_loader import CatalogParseError, parse_catalog_yaml


class CatalogLoaderTest(unittest.TestCase):
    def test_parse_full_catalog_yaml(self) -> None:
        catalog = parse_catalog_yaml("""
org_id: acme
services:
  - id: backend
    name: Backend API
    owners:
      - team-platform
    repos:
      - github.com/acme/backend
    environments:
      prod:
        runtime:
          provider: kubernetes
""")

        self.assertEqual(catalog["org_id"], "acme")
        self.assertEqual(catalog["services"][0]["id"], "backend")

    def test_parse_single_service_yaml(self) -> None:
        catalog = parse_catalog_yaml("""
id: backend
name: Backend API
owners:
  - team-platform
repos:
  - github.com/acme/backend
environments:
  prod:
    runtime:
      provider: kubernetes
""", default_org_id="acme")

        self.assertEqual(catalog["org_id"], "acme")
        self.assertEqual(catalog["services"][0]["id"], "backend")

    def test_rejects_non_object_yaml(self) -> None:
        with self.assertRaisesRegex(CatalogParseError, "must be an object"):
            parse_catalog_yaml("- backend")

    def test_rejects_unrecognized_object(self) -> None:
        with self.assertRaisesRegex(CatalogParseError, "services or a service id"):
            parse_catalog_yaml("name: Backend API")


if __name__ == "__main__":
    unittest.main()
