from __future__ import annotations

import unittest
from pathlib import Path

from org_context_graph.service_catalog import (
    CatalogValidationError,
    ServiceCatalog,
    normalize_environment,
    parse_repository,
    primary_repository,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "service-catalog.json"


class ServiceCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ServiceCatalog.from_file(CATALOG_PATH)

    def test_resolves_backend_prod(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["service"]["id"], "backend")
        self.assertEqual(result["environment"], "prod")
        self.assertEqual(result["tool_context"]["repository"]["full_name"], "acme/backend")

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

    def test_resolves_by_repository_name(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["service"]["id"], "backend")

    def test_tool_context_contains_tool_arguments(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        tool_arguments = result["tool_context"]["tool_arguments"]
        self.assertEqual(
            tool_arguments["code_host.get_recent_changes"],
            {"repository": "acme/backend", "branch": "main"},
        )
        self.assertEqual(
            tool_arguments["runtime.get_workload_status"],
            {
                "provider": "kubernetes",
                "namespace": "prod",
                "workload": "backend-api",
            },
        )
        self.assertEqual(
            tool_arguments["metrics.get_service_health"],
            {"target": "backend-prod"},
        )

    def test_primary_repository_uses_structured_repository(self) -> None:
        service = self.catalog.get_service(org_id="default", service_id="backend")
        assert service is not None
        repository = primary_repository(service)
        self.assertEqual(repository["provider"], "github")
        self.assertEqual(repository["full_name"], "acme/backend")
        self.assertEqual(repository["default_branch"], "main")

    def test_parse_repository_variants(self) -> None:
        self.assertEqual(parse_repository("github.com/acme/backend")["full_name"], "acme/backend")
        self.assertEqual(parse_repository("https://github.com/acme/backend")["full_name"], "acme/backend")
        self.assertEqual(parse_repository("git@github.com:acme/backend.git")["full_name"], "acme/backend")

    def test_search_finds_runbooks(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="oncall",
            result_type="runbook",
        )

        self.assertEqual(results[0]["type"], "runbook")
        self.assertEqual(results[0]["service_id"], "backend")
        self.assertEqual(results[0]["reference"], "docs/backend-oncall.md")

    def test_search_finds_dependencies(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="postgres",
            result_type="dependency",
        )

        self.assertEqual(results[0]["type"], "dependency")
        self.assertEqual(results[0]["reference"], "postgres-main")

    def test_search_finds_repository(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="acme/backend",
            result_type="repository",
        )

        self.assertEqual(results[0]["type"], "repository")
        self.assertEqual(results[0]["reference"], "https://github.com/acme/backend")

    def test_search_unknown_org_returns_empty(self) -> None:
        results = self.catalog.search(org_id="missing", query="backend")

        self.assertEqual(results, [])

    def test_rejects_missing_org_id(self) -> None:
        with self.assertRaisesRegex(CatalogValidationError, "org_id is required"):
            ServiceCatalog({"services": [{"id": "backend"}]})

    def test_rejects_duplicate_service_ids(self) -> None:
        catalog = {
            "org_id": "default",
            "services": [
                _valid_service("backend"),
                _valid_service("backend"),
            ],
        }
        with self.assertRaisesRegex(CatalogValidationError, "duplicated"):
            ServiceCatalog(catalog)

    def test_rejects_missing_repository(self) -> None:
        service = _valid_service("backend")
        service.pop("repositories")
        service.pop("repos")
        with self.assertRaisesRegex(CatalogValidationError, "repositories or repos"):
            ServiceCatalog({"org_id": "default", "services": [service]})

    def test_rejects_non_normalized_environment_name(self) -> None:
        service = _valid_service("backend")
        service["environments"]["production"] = service["environments"].pop("prod")
        with self.assertRaisesRegex(CatalogValidationError, "normalized environment"):
            ServiceCatalog({"org_id": "default", "services": [service]})

def _valid_service(service_id: str) -> dict:
    return {
        "id": service_id,
        "name": "Backend API",
        "aliases": ["backend"],
        "owners": ["team-platform"],
        "repos": ["github.com/acme/backend"],
        "repositories": [
            {
                "provider": "github",
                "host": "github.com",
                "owner": "acme",
                "name": "backend",
                "default_branch": "main",
            }
        ],
        "environments": {
            "prod": {
                "runtime": {
                    "provider": "kubernetes",
                    "namespace": "prod",
                    "workload": "backend-api",
                }
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
