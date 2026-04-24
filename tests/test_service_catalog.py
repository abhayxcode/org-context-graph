from __future__ import annotations

import unittest
from pathlib import Path

from org_context_graph.service_catalog import ServiceCatalog, normalize_environment


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "service-catalog.json"


class ServiceCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ServiceCatalog.from_file(CATALOG_PATH)

    def test_resolves_backend_prod(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["service"]["id"], "backend")
        self.assertEqual(result["environment"], "prod")

    def test_resolves_environment_alias(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="production")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["environment"], "prod")

    def test_unknown_service(self) -> None:
        result = self.catalog.resolve(org_id="default", query="payments", environment="prod")
        self.assertEqual(result["status"], "not_found")

    def test_unknown_environment(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="qa")
        self.assertEqual(result["status"], "environment_not_found")

    def test_normalize_environment(self) -> None:
        self.assertEqual(normalize_environment("live"), "prod")
        self.assertEqual(normalize_environment("stage"), "staging")


if __name__ == "__main__":
    unittest.main()
