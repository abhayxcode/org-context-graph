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

Demo catalog:

```bash
ORG_CONTEXT_CATALOG_PATH=examples/demo-service-catalog.json \
  PYTHONPATH=src uvicorn org_context_graph.main:app --port 4200
```

The demo catalog also has a YAML source at `examples/demo-service-catalog.yaml`. Use the JSON file for direct service startup, because the current startup loader reads JSON catalogs. Use the YAML file with `POST /v1/ingest/service-catalog/yaml`.

## APIs

- `GET /healthz`
- `GET /v1/catalog/validation`
- `GET /v1/resolve?q=backend&environment=prod`
- `GET /v1/search?q=oncall&type=runbook`
- `GET /v1/incidents/similar?service_id=backend&q=timeout`
- `GET /v1/owners/{team_id}`
- `GET /v1/repos/{repo_id}/context`
- `GET /v1/services`
- `GET /v1/services/{service_id}`
- `GET /v1/services/{service_id}/dependencies`
- `GET /v1/services/{service_id}/environments/{environment}`
- `GET /v1/services/{service_id}/health`
- `POST /v1/ingest/health`
- `POST /v1/ingest/service-catalog`
- `POST /v1/ingest/service-catalog/yaml`
- `POST /v1/ingest/incident`
- `POST /v1/ingest/repo`

`POST /v1/ingest/service-catalog` validates a service catalog payload and replaces the active catalog only after validation succeeds. When the app is backed by `ORG_CONTEXT_CATALOG_PATH` or the default JSON catalog path, accepted catalog and incident changes are persisted back to that JSON file through the catalog store boundary.

`POST /v1/ingest/service-catalog/yaml` accepts repo-owned YAML. It supports either a full catalog object with `org_id` and `services`, or a single service object with `id`, `name`, `owners`, `repos`, and `environments`. Single-service YAML is wrapped into a catalog using the `org_id` query parameter.

FastAPI response models define the public contract for health, search, service listing, service lookup, environment lookup, service catalog ingest, and service resolution responses. The generated OpenAPI schema includes `CatalogIngestRequest`, `CatalogIngestResponse`, `EnvironmentResponse`, `HealthResponse`, `ResolveResponse`, `SearchResponse`, `SearchResult`, `ServiceListResponse`, `ServiceResponse`, and the nested `ToolContext` model.

Search is deterministic in v1 and covers service names, aliases, repositories, owners, team/channel routing, runbooks, playbooks, and dependencies. Vector-backed RAG can replace the implementation later without changing the API contract.

`POST /v1/ingest/repo` stores code metadata only: repository, service, path, symbol, language, kind, summary, and metadata. Raw source code is not stored. Entries are rejected when a lightweight secret scan finds likely keys, tokens, passwords, or private keys.

`POST /v1/ingest/health` stores cached health snapshots produced by external tools. Org Context Graph does not probe runtime systems directly; live checks still belong to Tool Control Plane providers.

Incident memory is stored in the active catalog store in the current phase. `POST /v1/ingest/incident` records a prior diagnosis for a known service, and `GET /v1/incidents/similar` returns deterministic matches by service, environment, title, summary, root cause, resolution, and tags. Postgres persistence and vector similarity are planned for later phases.

Resolved responses include `tool_context`:

- service and environment
- owners
- service/team channels
- primary repository metadata
- runtime targets
- observability targets
- CI metadata
- runbooks and dependencies
- troubleshooting playbooks
- recent related incidents
- build/test commands
- suggested PR reviewers
- `tool_arguments` keyed by Tool Control Plane capability/action, such as `code_host.get_recent_changes`, `ci.get_checks`, and `runtime.get_workload_status`

Catalogs are validated at load time. Invalid catalog data fails fast for missing org IDs, duplicate service IDs, malformed teams, missing owners/repos, unsupported repository providers, missing environments, and non-normalized environment names.

Non-blocking validation warnings are available through `GET /v1/catalog/validation` and catalog ingest responses. Warnings identify incomplete optional context such as missing runbooks, playbooks, test commands, channels, observability, CI metadata, or team metadata.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
