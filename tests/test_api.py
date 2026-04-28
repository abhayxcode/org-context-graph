from __future__ import annotations

import asyncio
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
    ResolveResponse,
    ServiceListResponse,
    ServiceResponse,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "service-catalog.json"


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(CATALOG_PATH)

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

    def test_openapi_uses_response_models(self) -> None:
        schema = self.app.openapi()

        self.assertIn("CatalogIngestRequest", schema["components"]["schemas"])
        self.assertIn("CatalogIngestResponse", schema["components"]["schemas"])
        self.assertIn("EnvironmentResponse", schema["components"]["schemas"])
        self.assertIn("HealthResponse", schema["components"]["schemas"])
        self.assertIn("ResolveResponse", schema["components"]["schemas"])
        self.assertIn("ServiceListResponse", schema["components"]["schemas"])
        self.assertIn("ServiceResponse", schema["components"]["schemas"])


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
