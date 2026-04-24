# Org Context Graph

Standalone service for organization context:

- service catalog
- service/environment resolution
- owners/repos/runbooks/dependencies
- incident memory
- code metadata and RAG retrieval

## Phase 0 Status

This repo now has the first real service boundary for Majdoor. It exposes service resolution from a local JSON service catalog.

Planned stack:

- Python FastAPI
- Postgres
- pgvector
- Redis for jobs/cache

## Run

```bash
PYTHONPATH=src uvicorn org_context_graph.main:app --reload --port 4200
```

Override catalog path:

```bash
ORG_CONTEXT_CATALOG_PATH=data/service-catalog.json \
  PYTHONPATH=src uvicorn org_context_graph.main:app --port 4200
```

## APIs

- `GET /healthz`
- `GET /v1/resolve?q=backend&environment=prod`
- `GET /v1/services/{service_id}`
- `POST /v1/ingest/service-catalog`

`POST /v1/ingest/service-catalog` is planned, not implemented yet.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
