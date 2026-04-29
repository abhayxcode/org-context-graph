from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.routing import APIRoute, serialize_response

from org_context_graph.main import create_app
from org_context_graph.models import (
    CatalogIngestRequest,
    CatalogIngestResponse,
    EnvironmentResponse,
    HealthResponse,
    IncidentIngestRequest,
    IncidentIngestResponse,
    OwnerResponse,
    RepoContextResponse,
    ResolveResponse,
    SearchResponse,
    ServiceListResponse,
    ServiceResponse,
    SimilarIncidentsResponse,
)
from org_context_graph.storage import MemoryCatalogStore


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "service-catalog.json"


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryCatalogStore(json.loads(CATALOG_PATH.read_text(encoding="utf8")))
        self.app = create_app(catalog_store=self.store)

    def test_healthz(self) -> None:
        route = _route(self.app, "/healthz")
        body = _serialized_response(route, route.endpoint())

        self.assertEqual(route.response_model, HealthResponse)
        self.assertEqual(body, {"status": "ok"})

    def test_resolve_returns_tool_context(self) -> None:
        route = _route(self.app, "/v1/resolve")
        raw_body = route.endpoint(q="backend", environment="prod")
        body = _serialized_response(route, raw_body)

        self.assertEqual(route.response_model, ResolveResponse)
        self.assertEqual(body["status"], "resolved")
        self.assertEqual(body["environment"], "prod")
        self.assertEqual(body["tool_context"]["repository"]["full_name"], "acme/backend")
        self.assertEqual(body["tool_context"]["playbooks"][0]["id"], "backend-timeout")
        self.assertEqual(body["tool_context"]["test_commands"], ["npm test"])
        self.assertEqual(body["tool_context"]["suggested_reviewers"], ["team-platform"])
        self.assertEqual(
            body["tool_context"]["tool_arguments"]["code_host.get_recent_changes"],
            {"repository": "acme/backend", "branch": "main"},
        )

    def test_resolve_not_found_keeps_sparse_shape(self) -> None:
        route = _route(self.app, "/v1/resolve")
        raw_body = route.endpoint(q="payments")
        body = _serialized_response(route, raw_body)

        self.assertEqual(body["status"], "not_found")
        self.assertEqual(body["candidates"], [])
        self.assertNotIn("service", body)
        self.assertNotIn("tool_context", body)

    def test_search_returns_catalog_results(self) -> None:
        route = _route(self.app, "/v1/search")
        raw_body = route.endpoint(q="oncall", result_type="runbook")
        body = _serialized_response(route, raw_body)

        self.assertEqual(route.response_model, SearchResponse)
        self.assertEqual(body["org_id"], "default")
        self.assertEqual(body["query"], "oncall")
        self.assertEqual(body["type"], "runbook")
        self.assertEqual(body["result_count"], 1)
        self.assertEqual(body["results"][0]["service_id"], "backend")
        self.assertEqual(body["results"][0]["reference"], "docs/backend-oncall.md")

    def test_search_returns_playbook_results(self) -> None:
        route = _route(self.app, "/v1/search")
        raw_body = route.endpoint(q="timeout", result_type="playbook")
        body = _serialized_response(route, raw_body)

        self.assertEqual(body["result_count"], 1)
        self.assertEqual(body["results"][0]["type"], "playbook")
        self.assertEqual(body["results"][0]["reference"], "backend-timeout")

    def test_search_unknown_org(self) -> None:
        route = _route(self.app, "/v1/search")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(q="backend", org_id="missing")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "catalog not found")

    def test_list_services(self) -> None:
        route = _route(self.app, "/v1/services")
        raw_body = route.endpoint()
        body = _serialized_response(route, raw_body)

        self.assertEqual(route.response_model, ServiceListResponse)
        self.assertEqual(body["org_id"], "default")
        self.assertEqual(body["service_count"], 1)
        self.assertEqual(body["services"][0]["id"], "backend")

    def test_list_services_unknown_org(self) -> None:
        route = _route(self.app, "/v1/services")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(org_id="missing")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "catalog not found")

    def test_get_service(self) -> None:
        route = _route(self.app, "/v1/services/{service_id}")
        raw_body = route.endpoint(service_id="backend")
        body = _serialized_response(route, raw_body)

        self.assertEqual(route.response_model, ServiceResponse)
        self.assertEqual(body["id"], "backend")
        self.assertEqual(body["repositories"][0]["owner"], "acme")
        self.assertEqual(body["repositories"][0]["name"], "backend")
        self.assertIn("prod", body["environments"])

    def test_get_service_404(self) -> None:
        route = _route(self.app, "/v1/services/{service_id}")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(service_id="payments")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "service not found")

    def test_get_owner(self) -> None:
        route = _route(self.app, "/v1/owners/{team_id}")
        body = _serialized_response(route, route.endpoint(team_id="team-platform"))

        self.assertEqual(route.response_model, OwnerResponse)
        self.assertEqual(body["id"], "team-platform")
        self.assertEqual(body["github_team"], "acme/platform")
        self.assertEqual(body["services"], ["backend"])

    def test_get_owner_404(self) -> None:
        route = _route(self.app, "/v1/owners/{team_id}")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(team_id="team-missing")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "owner not found")

    def test_get_repo_context(self) -> None:
        route = _route(self.app, "/v1/repos/{repo_id}/context")
        body = _serialized_response(route, route.endpoint(repo_id="acme/backend"))

        self.assertEqual(route.response_model, RepoContextResponse)
        self.assertEqual(body["repository"]["full_name"], "acme/backend")
        self.assertEqual(body["service"]["id"], "backend")
        self.assertEqual(body["owners"][0]["id"], "team-platform")
        self.assertEqual(body["test_commands"], ["npm test"])

    def test_get_repo_context_404(self) -> None:
        route = _route(self.app, "/v1/repos/{repo_id}/context")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(repo_id="acme/payments")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "repository not found")

    def test_get_environment_returns_tool_context(self) -> None:
        route = _route(self.app, "/v1/services/{service_id}/environments/{environment}")
        raw_body = route.endpoint(service_id="backend", environment="production")
        body = _serialized_response(route, raw_body)

        self.assertEqual(route.response_model, EnvironmentResponse)
        self.assertEqual(body["service_id"], "backend")
        self.assertEqual(body["environment"], "prod")
        self.assertEqual(body["environment_config"]["runtime"]["workload"], "backend-api")
        self.assertEqual(body["tool_context"]["repository"]["full_name"], "acme/backend")

    def test_get_environment_404(self) -> None:
        route = _route(self.app, "/v1/services/{service_id}/environments/{environment}")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(service_id="backend", environment="qa")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "environment not found")

    def test_ingest_service_catalog_replaces_active_catalog(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/service-catalog")
        resolve_route = _route(self.app, "/v1/resolve")
        payload = CatalogIngestRequest(
            org_id="default",
            services=[_valid_service("payments", "Payments API")],
        )

        raw_body = ingest_route.endpoint(payload=payload)
        body = _serialized_response(ingest_route, raw_body)
        resolved = resolve_route.endpoint(q="payments", environment="prod")

        self.assertEqual(ingest_route.response_model, CatalogIngestResponse)
        self.assertEqual(body, {"status": "accepted", "org_id": "default", "service_count": 1})
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["service"]["id"], "payments")
        self.assertEqual(self.store.load()["services"][0]["id"], "payments")

    def test_invalid_ingest_does_not_replace_active_catalog(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/service-catalog")
        resolve_route = _route(self.app, "/v1/resolve")
        payload = CatalogIngestRequest(org_id="default", services=[{"id": "broken"}])

        with self.assertRaises(HTTPException) as context:
            ingest_route.endpoint(payload=payload)

        resolved = resolve_route.endpoint(q="backend", environment="prod")
        self.assertEqual(context.exception.status_code, 422)
        self.assertIn("name is required", context.exception.detail)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["service"]["id"], "backend")

    def test_ingest_full_catalog_yaml(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/service-catalog/yaml")
        resolve_route = _route(self.app, "/v1/resolve")
        payload = """
org_id: default
services:
  - id: payments
    name: Payments API
    aliases:
      - payments
    owners:
      - team-payments
    repos:
      - github.com/acme/payments
    environments:
      prod:
        runtime:
          provider: kubernetes
          namespace: prod
          workload: payments-api
"""

        body = _serialized_response(ingest_route, ingest_route.endpoint(payload=payload))
        resolved = resolve_route.endpoint(q="payments", environment="prod")

        self.assertEqual(body, {"status": "accepted", "org_id": "default", "service_count": 1})
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["service"]["id"], "payments")
        self.assertEqual(self.store.load()["services"][0]["id"], "payments")

    def test_ingest_single_service_yaml(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/service-catalog/yaml")
        payload = """
id: search
name: Search API
aliases:
  - search
owners:
  - team-search
repos:
  - github.com/acme/search
environments:
  prod:
    runtime:
      provider: kubernetes
      namespace: prod
      workload: search-api
"""

        body = _serialized_response(
            ingest_route,
            ingest_route.endpoint(payload=payload, org_id="acme"),
        )

        self.assertEqual(body, {"status": "accepted", "org_id": "acme", "service_count": 1})
        self.assertEqual(self.store.load()["org_id"], "acme")
        self.assertEqual(self.store.load()["services"][0]["id"], "search")

    def test_invalid_yaml_ingest_does_not_replace_active_catalog(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/service-catalog/yaml")
        resolve_route = _route(self.app, "/v1/resolve")

        with self.assertRaises(HTTPException) as context:
            ingest_route.endpoint(payload="- not-a-catalog")

        resolved = resolve_route.endpoint(q="backend", environment="prod")
        self.assertEqual(context.exception.status_code, 422)
        self.assertIn("must be an object", context.exception.detail)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(self.store.load()["services"][0]["id"], "backend")

    def test_ingest_incident_and_find_similar(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/incident")
        similar_route = _route(self.app, "/v1/incidents/similar")
        payload = IncidentIngestRequest(
            org_id="default",
            service_id="backend",
            environment="production",
            title="Database timeout during checkout",
            summary="Backend timed out while calling postgres-main.",
            tags=["database", "timeout"],
        )

        raw_body = ingest_route.endpoint(payload=payload)
        body = _serialized_response(ingest_route, raw_body)
        similar = _serialized_response(
            similar_route,
            similar_route.endpoint(
                service_id="backend",
                q="database timeout",
                environment="prod",
            ),
        )

        self.assertEqual(ingest_route.response_model, IncidentIngestResponse)
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["incident"]["environment"], "prod")
        self.assertEqual(self.store.load()["incidents"][0]["title"], "Database timeout during checkout")
        self.assertEqual(similar_route.response_model, SimilarIncidentsResponse)
        self.assertEqual(similar["incident_count"], 1)
        self.assertEqual(similar["incidents"][0]["incident"]["title"], "Database timeout during checkout")

    def test_ingest_incident_unknown_service(self) -> None:
        ingest_route = _route(self.app, "/v1/ingest/incident")
        payload = IncidentIngestRequest(
            service_id="payments",
            title="Unknown service incident",
        )

        with self.assertRaises(HTTPException) as context:
            ingest_route.endpoint(payload=payload)

        self.assertEqual(context.exception.status_code, 422)
        self.assertIn("does not exist", context.exception.detail)

    def test_similar_incidents_unknown_service(self) -> None:
        route = _route(self.app, "/v1/incidents/similar")

        with self.assertRaises(HTTPException) as context:
            route.endpoint(service_id="payments", q="timeout")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "service not found")

    def test_openapi_uses_response_models(self) -> None:
        schema = self.app.openapi()

        self.assertIn("CatalogIngestRequest", schema["components"]["schemas"])
        self.assertIn("CatalogIngestResponse", schema["components"]["schemas"])
        self.assertIn("EnvironmentResponse", schema["components"]["schemas"])
        self.assertIn("HealthResponse", schema["components"]["schemas"])
        self.assertIn("IncidentIngestRequest", schema["components"]["schemas"])
        self.assertIn("IncidentIngestResponse", schema["components"]["schemas"])
        self.assertIn("OwnerResponse", schema["components"]["schemas"])
        self.assertIn("RepoContextResponse", schema["components"]["schemas"])
        self.assertIn("ResolveResponse", schema["components"]["schemas"])
        self.assertIn("SearchResponse", schema["components"]["schemas"])
        self.assertIn("SearchResult", schema["components"]["schemas"])
        self.assertIn("ServiceListResponse", schema["components"]["schemas"])
        self.assertIn("ServiceResponse", schema["components"]["schemas"])
        self.assertIn("SimilarIncidentsResponse", schema["components"]["schemas"])
        self.assertIn("SimilarIncidentResult", schema["components"]["schemas"])


def _route(app: Any, path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise AssertionError(f"Route not found: {path}")


def _serialized_response(route: APIRoute, body: Any) -> Any:
    return asyncio.run(
        serialize_response(
            field=route.response_field,
            response_content=body,
            exclude_unset=route.response_model_exclude_unset,
            exclude_defaults=route.response_model_exclude_defaults,
            exclude_none=route.response_model_exclude_none,
            is_coroutine=False,
        )
    )


def _valid_service(service_id: str, name: str) -> dict[str, Any]:
    return {
        "id": service_id,
        "name": name,
        "aliases": [service_id],
        "owners": ["team-platform"],
        "repositories": [
            {
                "provider": "github",
                "host": "github.com",
                "owner": "acme",
                "name": service_id,
                "default_branch": "main",
            }
        ],
        "environments": {
            "prod": {
                "runtime": {
                    "provider": "kubernetes",
                    "namespace": "prod",
                    "workload": service_id,
                }
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
