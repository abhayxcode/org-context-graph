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

    def test_tool_context_contains_pr_metadata(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        tool_context = result["tool_context"]

        self.assertEqual(tool_context["build_commands"], ["npm install", "npm run build"])
        self.assertEqual(tool_context["test_commands"], ["npm test"])
        self.assertEqual(tool_context["suggested_reviewers"], ["team-platform"])

    def test_tool_context_contains_playbooks(self) -> None:
        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        playbooks = result["tool_context"]["playbooks"]

        self.assertEqual(playbooks[0]["id"], "backend-timeout")
        self.assertEqual(playbooks[0]["title"], "Backend timeout triage")

    def test_primary_repository_uses_structured_repository(self) -> None:
        service = self.catalog.get_service(org_id="default", service_id="backend")
        assert service is not None
        repository = primary_repository(service)
        self.assertEqual(repository["provider"], "github")
        self.assertEqual(repository["full_name"], "acme/backend")
        self.assertEqual(repository["default_branch"], "main")

    def test_get_owner_returns_team_metadata_and_services(self) -> None:
        owner = self.catalog.get_owner(org_id="default", team_id="team-platform")

        assert owner is not None
        self.assertEqual(owner["id"], "team-platform")
        self.assertEqual(owner["github_team"], "acme/platform")
        self.assertEqual(owner["slack_channel"], "#team-platform")
        self.assertEqual(owner["services"], ["backend"])

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

    def test_search_finds_playbooks(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="timeout",
            result_type="playbook",
        )

        self.assertEqual(results[0]["type"], "playbook")
        self.assertEqual(results[0]["reference"], "backend-timeout")
        self.assertEqual(results[0]["metadata"]["tags"], ["timeout", "database", "restart"])

    def test_search_unknown_org_returns_empty(self) -> None:
        results = self.catalog.search(org_id="missing", query="backend")

        self.assertEqual(results, [])

    def test_ingest_incident_adds_memory(self) -> None:
        incident = self.catalog.ingest_incident({
            "service_id": "backend",
            "environment": "production",
            "title": "Database timeout during checkout",
            "summary": "Backend timed out while calling postgres-main.",
            "tags": ["database", "timeout"],
        })

        self.assertEqual(incident["id"], "incident-1")
        self.assertEqual(incident["environment"], "prod")
        self.assertEqual(self.catalog.incidents()[0]["title"], "Database timeout during checkout")

    def test_ingest_incident_rejects_unknown_service(self) -> None:
        with self.assertRaisesRegex(CatalogValidationError, "does not exist"):
            self.catalog.ingest_incident({
                "service_id": "payments",
                "title": "Unknown service incident",
            })

    def test_similar_incidents_finds_prior_diagnosis(self) -> None:
        self.catalog.ingest_incident({
            "service_id": "backend",
            "environment": "prod",
            "title": "Database timeout during checkout",
            "summary": "Backend timed out while calling postgres-main.",
            "root_cause": "Connection pool saturation",
            "resolution": "Raised pool limit and restarted workers.",
            "tags": ["database", "timeout"],
        })

        incidents = self.catalog.similar_incidents(
            org_id="default",
            service_id="backend",
            query="database timeout",
            environment="prod",
        )

        self.assertEqual(incidents[0]["incident"]["title"], "Database timeout during checkout")
        self.assertIn("title", incidents[0]["matched_fields"])

    def test_similar_incidents_unknown_service_returns_empty(self) -> None:
        incidents = self.catalog.similar_incidents(
            org_id="default",
            service_id="payments",
            query="timeout",
        )

        self.assertEqual(incidents, [])

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

    def test_rejects_invalid_pr_metadata_fields(self) -> None:
        service = _valid_service("backend")
        service["test_commands"] = "npm test"
        with self.assertRaisesRegex(CatalogValidationError, "test_commands must be a list"):
            ServiceCatalog({"org_id": "default", "services": [service]})

    def test_rejects_invalid_playbook(self) -> None:
        service = _valid_service("backend")
        service["playbooks"] = [{"id": "timeout"}]
        with self.assertRaisesRegex(CatalogValidationError, "playbooks\\[0\\].title is required"):
            ServiceCatalog({"org_id": "default", "services": [service]})

    def test_rejects_invalid_team(self) -> None:
        catalog = {
            "org_id": "default",
            "teams": [{"name": "Platform Team"}],
            "services": [_valid_service("backend")],
        }
        with self.assertRaisesRegex(CatalogValidationError, "teams\\[0\\].id is required"):
            ServiceCatalog(catalog)

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
