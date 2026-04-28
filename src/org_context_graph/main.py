from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from org_context_graph.models import (
    CatalogIngestRequest,
    CatalogIngestResponse,
    EnvironmentResponse,
    HealthResponse,
    IncidentIngestRequest,
    IncidentIngestResponse,
    ResolveResponse,
    SearchResponse,
    ServiceListResponse,
    ServiceResponse,
    SimilarIncidentsResponse,
)
from org_context_graph.service_catalog import (
    CatalogValidationError,
    ServiceCatalog,
    build_tool_context,
    normalize_environment,
)


def default_catalog_path() -> Path:
    return Path(os.environ.get("ORG_CONTEXT_CATALOG_PATH", "data/service-catalog.json"))


def create_app(catalog_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Org Context Graph", version="0.1.0")
    catalog = ServiceCatalog.from_file(catalog_path or default_catalog_path())

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/v1/resolve",
        response_model=ResolveResponse,
        response_model_exclude_none=True,
        response_model_exclude_unset=True,
    )
    def resolve(
        q: Annotated[str, Query(min_length=1)],
        environment: str = "prod",
        org_id: str = "default",
    ) -> dict[str, object]:
        return catalog.resolve(org_id=org_id, query=q, environment=environment)

    @app.get(
        "/v1/search",
        response_model=SearchResponse,
    )
    def search(
        q: Annotated[str, Query(min_length=1)],
        result_type: Annotated[str, Query(alias="type")] = "all",
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        org_id: str = "default",
    ) -> dict[str, object]:
        if org_id != catalog.org_id:
            raise HTTPException(status_code=404, detail="catalog not found")
        results = catalog.search(org_id=org_id, query=q, result_type=result_type, limit=limit)
        return {
            "org_id": catalog.org_id,
            "query": q,
            "type": result_type,
            "result_count": len(results),
            "results": results,
        }

    @app.get(
        "/v1/services",
        response_model=ServiceListResponse,
        response_model_exclude_unset=True,
    )
    def list_services(org_id: str = "default") -> dict[str, object]:
        if org_id != catalog.org_id:
            raise HTTPException(status_code=404, detail="catalog not found")
        services = catalog.services()
        return {
            "org_id": catalog.org_id,
            "service_count": len(services),
            "services": services,
        }

    @app.get(
        "/v1/services/{service_id}",
        response_model=ServiceResponse,
        response_model_exclude_unset=True,
    )
    def get_service(service_id: str, org_id: str = "default") -> dict[str, object]:
        service = catalog.get_service(org_id=org_id, service_id=service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="service not found")
        return service

    @app.get(
        "/v1/services/{service_id}/environments/{environment}",
        response_model=EnvironmentResponse,
    )
    def get_environment(
        service_id: str,
        environment: str,
        org_id: str = "default",
    ) -> dict[str, object]:
        service = catalog.get_service(org_id=org_id, service_id=service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="service not found")

        normalized_environment = normalize_environment(environment)
        environment_config = service.get("environments", {}).get(normalized_environment)
        if environment_config is None:
            raise HTTPException(status_code=404, detail="environment not found")

        return {
            "service_id": service_id,
            "environment": normalized_environment,
            "environment_config": environment_config,
            "tool_context": build_tool_context(service, normalized_environment, environment_config),
        }

    @app.post(
        "/v1/ingest/service-catalog",
        response_model=CatalogIngestResponse,
    )
    def ingest_service_catalog(payload: CatalogIngestRequest) -> dict[str, object]:
        nonlocal catalog
        payload_dict = _model_to_dict(payload)
        try:
            next_catalog = ServiceCatalog(payload_dict)
        except CatalogValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        catalog = next_catalog
        return {
            "status": "accepted",
            "org_id": catalog.org_id,
            "service_count": len(catalog.services()),
        }

    @app.post(
        "/v1/ingest/incident",
        response_model=IncidentIngestResponse,
    )
    def ingest_incident(payload: IncidentIngestRequest) -> dict[str, object]:
        payload_dict = _model_to_dict(payload)
        org_id = str(payload_dict.pop("org_id", "default"))
        if org_id != catalog.org_id:
            raise HTTPException(status_code=404, detail="catalog not found")

        try:
            incident = catalog.ingest_incident(payload_dict)
        except CatalogValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        return {
            "status": "accepted",
            "org_id": catalog.org_id,
            "incident": incident,
        }

    @app.get(
        "/v1/incidents/similar",
        response_model=SimilarIncidentsResponse,
        response_model_exclude_none=True,
    )
    def similar_incidents(
        service_id: str,
        q: str = "",
        environment: str | None = None,
        limit: Annotated[int, Query(ge=1, le=25)] = 5,
        org_id: str = "default",
    ) -> dict[str, object]:
        if org_id != catalog.org_id:
            raise HTTPException(status_code=404, detail="catalog not found")
        if catalog.get_service(org_id=org_id, service_id=service_id) is None:
            raise HTTPException(status_code=404, detail="service not found")

        normalized_environment = normalize_environment(environment) if environment else None
        incidents = catalog.similar_incidents(
            org_id=org_id,
            service_id=service_id,
            query=q,
            environment=normalized_environment,
            limit=limit,
        )
        return {
            "org_id": catalog.org_id,
            "service_id": service_id,
            "environment": normalized_environment,
            "query": q,
            "incident_count": len(incidents),
            "incidents": incidents,
        }

    return app


app = create_app()


def _model_to_dict(model: CatalogIngestRequest | IncidentIngestRequest) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
