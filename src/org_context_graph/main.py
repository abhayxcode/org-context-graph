from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from org_context_graph.models import HealthResponse, ResolveResponse, ServiceResponse
from org_context_graph.service_catalog import ServiceCatalog


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
        "/v1/services/{service_id}",
        response_model=ServiceResponse,
        response_model_exclude_unset=True,
    )
    def get_service(service_id: str, org_id: str = "default") -> dict[str, object]:
        service = catalog.get_service(org_id=org_id, service_id=service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="service not found")
        return service

    return app


app = create_app()
