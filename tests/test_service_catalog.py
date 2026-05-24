from __future__ import annotations

import unittest
from pathlib import Path

from org_context_graph.service_catalog import (
    CatalogValidationError,
    ServiceCatalog,
    catalog_warnings,
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

    def test_resolves_by_owner_team(self) -> None:
        result = self.catalog.resolve(org_id="default", query="team-platform", environment="prod")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["service"]["id"], "backend")

    def test_resolves_by_owner_slack_channel(self) -> None:
        result = self.catalog.resolve(org_id="default", query="#team-platform", environment="prod")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["service"]["id"], "backend")

    def test_resolves_by_service_channel(self) -> None:
        result = self.catalog.resolve(org_id="default", query="#backend-api", environment="prod")
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
        self.assertEqual(tool_context["channels"], ["#backend-api"])

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

    def test_get_repo_context(self) -> None:
        context = self.catalog.get_repo_context(org_id="default", repository_id="acme/backend")

        assert context is not None
        self.assertEqual(context["repository"]["full_name"], "acme/backend")
        self.assertEqual(context["service"]["id"], "backend")
        self.assertEqual(context["owners"][0]["id"], "team-platform")
        self.assertEqual(context["environments"], ["prod", "staging"])
        self.assertEqual(context["test_commands"], ["npm test"])

    def test_get_repo_context_accepts_url(self) -> None:
        context = self.catalog.get_repo_context(
            org_id="default",
            repository_id="https://github.com/acme/backend",
        )

        assert context is not None
        self.assertEqual(context["service"]["id"], "backend")

    def test_get_dependencies_normalizes_dependencies(self) -> None:
        dependencies = self.catalog.get_dependencies(org_id="default", service_id="backend")

        assert dependencies is not None
        self.assertEqual(dependencies["service_id"], "backend")
        self.assertEqual(
            dependencies["dependencies"],
            [
                {
                    "target": "postgres-main",
                    "kind": "database",
                    "criticality": None,
                    "metadata": {},
                },
                {
                    "target": "redis-cache",
                    "kind": "cache",
                    "criticality": None,
                    "metadata": {},
                },
            ],
        )

    def test_get_dependencies_tracks_dependents(self) -> None:
        backend = _valid_service("backend")
        worker = _valid_service("worker")
        worker["dependencies"] = [{"target": "backend", "kind": "api", "criticality": "high"}]
        catalog = ServiceCatalog({"org_id": "default", "services": [backend, worker]})

        dependencies = catalog.get_dependencies(org_id="default", service_id="backend")

        assert dependencies is not None
        self.assertEqual(dependencies["dependents"], ["worker"])

    def test_ingest_health_snapshot(self) -> None:
        snapshot = self.catalog.ingest_health_snapshot({
            "service_id": "backend",
            "environment": "production",
            "status": "healthy",
            "summary": "All checks passing.",
            "checked_at": "2026-07-11T10:00:00Z",
            "signals": {"checks": 3},
            "source": "tool-control-plane",
        })
        health = self.catalog.get_health_summary(
            org_id="default",
            service_id="backend",
            environment="prod",
        )

        self.assertEqual(snapshot["environment"], "prod")
        assert health is not None
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["signals"], {"checks": 3})

    def test_get_health_summary_returns_unknown_without_snapshot(self) -> None:
        health = self.catalog.get_health_summary(
            org_id="default",
            service_id="backend",
            environment="prod",
        )

        assert health is not None
        self.assertEqual(health["status"], "unknown")
        self.assertEqual(health["summary"], "No cached health snapshot is available.")

    def test_get_health_summary_normalizes_static_snapshot(self) -> None:
        catalog = ServiceCatalog({
            "org_id": "default",
            "services": [_valid_service("backend")],
            "health": {
                "backend:prod": {
                    "status": "degraded",
                    "summary": "Latency is elevated.",
                    "checked_at": "2026-07-15T00:00:00Z",
                    "signals": [
                        {
                            "name": "latency_p95_ms",
                            "value": 1250,
                            "status": "warning",
                        }
                    ],
                }
            },
        })

        health = catalog.get_health_summary(
            org_id="default",
            service_id="backend",
            environment="production",
        )

        assert health is not None
        self.assertEqual(health["service_id"], "backend")
        self.assertEqual(health["environment"], "prod")
        self.assertEqual(health["signals"], {
            "latency_p95_ms": {
                "value": 1250,
                "status": "warning",
            }
        })

    def test_ingest_health_snapshot_rejects_unknown_environment(self) -> None:
        with self.assertRaisesRegex(CatalogValidationError, "has no 'qa' environment"):
            self.catalog.ingest_health_snapshot({
                "service_id": "backend",
                "environment": "qa",
                "status": "healthy",
            })

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
        self.assertEqual(results[0]["metadata"]["kind"], "database")

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

    def test_search_finds_channels(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="#backend-api",
            result_type="channel",
        )

        self.assertEqual(results[0]["type"], "channel")
        self.assertEqual(results[0]["reference"], "#backend-api")

    def test_search_finds_owner_by_slack_channel(self) -> None:
        results = self.catalog.search(
            org_id="default",
            query="#team-platform",
            result_type="owner",
        )

        self.assertEqual(results[0]["type"], "owner")
        self.assertEqual(results[0]["reference"], "team-platform")

    def test_ingest_repo_index_adds_searchable_code_metadata(self) -> None:
        result = self.catalog.ingest_repo_index(
            org_id="default",
            repository="acme/backend",
            service_id=None,
            entries=[
                {
                    "path": "src/health.ts",
                    "symbol": "checkBackendHealth",
                    "summary": "Checks backend health and database reachability.",
                    "language": "typescript",
                    "kind": "function",
                }
            ],
        )
        results = self.catalog.search(org_id="default", query="database reachability", result_type="code")

        assert result is not None
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["indexed_count"], 1)
        self.assertEqual(self.catalog.code_index()[0]["symbol"], "checkBackendHealth")
        self.assertEqual(results[0]["type"], "code")
        self.assertEqual(results[0]["metadata"]["path"], "src/health.ts")

    def test_ingest_repo_index_rejects_secret_like_entries(self) -> None:
        result = self.catalog.ingest_repo_index(
            org_id="default",
            repository="acme/backend",
            service_id=None,
            entries=[
                {
                    "path": "src/config.ts",
                    "summary": "api_key = \"supersecretvalue\"",
                }
            ],
        )

        assert result is not None
        self.assertEqual(result["status"], "accepted_with_rejections")
        self.assertEqual(result["indexed_count"], 0)
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(self.catalog.code_index(), [])

    def test_search_unknown_org_returns_empty(self) -> None:
        results = self.catalog.search(org_id="missing", query="backend")

        self.assertEqual(results, [])

    def test_catalog_warnings_reports_missing_optional_context(self) -> None:
        service = _valid_service("backend")
        warnings = catalog_warnings({"org_id": "default", "services": [service]})

        codes = {warning["code"] for warning in warnings}
        self.assertIn("missing_runbooks", codes)
        self.assertIn("missing_playbooks", codes)
        self.assertIn("missing_test_commands", codes)
        self.assertIn("missing_channels", codes)
        self.assertIn("missing_observability", codes)
        self.assertIn("missing_ci", codes)

    def test_catalog_warnings_empty_for_sample_catalog(self) -> None:
        self.assertEqual(self.catalog.validation_warnings(), [])

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

    def test_recent_incidents_are_returned_in_resolved_tool_context(self) -> None:
        self.catalog.ingest_incident({
            "service_id": "backend",
            "environment": "prod",
            "title": "Old timeout",
            "occurred_at": "2026-07-10T09:00:00Z",
        })
        self.catalog.ingest_incident({
            "service_id": "backend",
            "environment": "prod",
            "title": "New timeout",
            "occurred_at": "2026-07-11T09:00:00Z",
        })

        result = self.catalog.resolve(org_id="default", query="backend", environment="prod")
        incidents = result["tool_context"]["recent_incidents"]

        self.assertEqual([incident["title"] for incident in incidents], ["New timeout", "Old timeout"])

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

    def test_rejects_invalid_channels(self) -> None:
        service = _valid_service("backend")
        service["channels"] = "#backend"
        with self.assertRaisesRegex(CatalogValidationError, "channels must be a list"):
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
