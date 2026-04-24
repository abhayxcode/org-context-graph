# Org Context Graph

Standalone service for organization context:

- service catalog
- service/environment resolution
- owners/repos/runbooks/dependencies
- incident memory
- code metadata and RAG retrieval

## Phase 0 Status

This repo is scaffolded for later extraction. Phase 1 local demo uses an in-process mock inside `../claude-tag`.

Planned stack:

- Python FastAPI
- Postgres
- pgvector
- Redis for jobs/cache

## First API Targets

- `GET /healthz`
- `GET /v1/resolve?q=backend&environment=prod`
- `GET /v1/services/{service_id}`
- `POST /v1/ingest/service-catalog`
