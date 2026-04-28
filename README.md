# Org Context Graph

Standalone service for organization context:

- service catalog
- service/environment resolution
- owners/repos/runbooks/dependencies
- incident memory
- code metadata and RAG retrieval

## Phase 0 Status

This repo now has the first real service boundary for Majdoor. It exposes service resolution from a local JSON service catalog and returns tool-ready context for downstream Tool Control Plane calls.

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

`POST /v1/ingest/service-catalog` validates a service catalog payload and replaces the active in-memory catalog only after validation succeeds. Disk persistence is planned for a later phase.

FastAPI response models define the public contract for health, service lookup, service catalog ingest, and service resolution responses. The generated OpenAPI schema includes `CatalogIngestRequest`, `CatalogIngestResponse`, `HealthResponse`, `ResolveResponse`, `ServiceResponse`, and the nested `ToolContext` model.

Resolved responses include `tool_context`:

- service and environment
- owners
- primary repository metadata
- runtime targets
- observability targets
- CI metadata
- runbooks and dependencies
- `tool_arguments` keyed by Tool Control Plane capability/action, such as `code_host.get_recent_changes`, `ci.get_checks`, and `runtime.get_workload_status`

Catalogs are validated at load time. Invalid catalog data fails fast for missing org IDs, duplicate service IDs, missing owners/repos, unsupported repository providers, missing environments, and non-normalized environment names.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
