from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    org_id: str
    service_count: int
    warning_count: int
    checks: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class CatalogIngestRequest(BaseModel):
    org_id: str
    services: list[dict[str, Any]]

    class Config:
        extra = "allow"


class CatalogIngestResponse(BaseModel):
    status: str
    org_id: str
    service_count: int
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class CatalogValidationResponse(BaseModel):
    status: str
    org_id: str
    service_count: int
    warning_count: int
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class IncidentIngestRequest(BaseModel):
    org_id: str = "default"
    service_id: str
    environment: str | None = None
    title: str
    summary: str = ""
    root_cause: str | None = None
    resolution: str | None = None
    occurred_at: str | None = None
    links: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    class Config:
        extra = "allow"


class IncidentIngestResponse(BaseModel):
    status: str
    org_id: str
    incident: dict[str, Any]


class CodeIndexEntry(BaseModel):
    path: str
    symbol: str | None = None
    summary: str = ""
    language: str | None = None
    kind: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class RepoIngestRequest(BaseModel):
    org_id: str = "default"
    repository: str
    service_id: str | None = None
    entries: list[CodeIndexEntry]


class RepoIngestResponse(BaseModel):
    status: str
    org_id: str
    repository: str
    service_id: str | None = None
    indexed_count: int
    rejected_count: int
    rejected: list[dict[str, Any]] = Field(default_factory=list)


class HealthSnapshotIngestRequest(BaseModel):
    org_id: str = "default"
    service_id: str
    environment: str = "prod"
    status: str
    summary: str = ""
    checked_at: str | None = None
    signals: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None

    class Config:
        extra = "allow"


class HealthSnapshotIngestResponse(BaseModel):
    status: str
    org_id: str
    service_id: str
    environment: str
    snapshot: dict[str, Any]


class HealthSummaryResponse(BaseModel):
    org_id: str
    service_id: str
    environment: str
    status: str
    summary: str = ""
    checked_at: str | None = None
    signals: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None


class ToolContext(BaseModel):
    service_id: str
    environment: str
    owners: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    repository: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    observability: dict[str, Any] = Field(default_factory=dict)
    ci: dict[str, Any] = Field(default_factory=dict)
    runbooks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    playbooks: list[dict[str, Any]] = Field(default_factory=list)
    recent_incidents: list[dict[str, Any]] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    suggested_reviewers: list[str] = Field(default_factory=list)
    tool_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ResolveResponse(BaseModel):
    status: str
    confidence: float
    reason: str | None = None
    candidates: list[str] | None = None
    service_id: str | None = None
    environment: str | None = None
    service: dict[str, Any] | None = None
    environment_config: dict[str, Any] | None = None
    tool_context: ToolContext | None = None


class ServiceResponse(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    environments: dict[str, dict[str, Any]]
    runbooks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    playbooks: list[dict[str, Any]] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    suggested_reviewers: list[str] = Field(default_factory=list)

    class Config:
        extra = "allow"


class ServiceListResponse(BaseModel):
    org_id: str
    service_count: int
    services: list[ServiceResponse]


class OwnerResponse(BaseModel):
    id: str
    name: str | None = None
    github_team: str | None = None
    slack_channel: str | None = None
    oncall: str | None = None
    members: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class RepoContextResponse(BaseModel):
    org_id: str
    repository: dict[str, Any]
    service: ServiceResponse
    owners: list[OwnerResponse] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    suggested_reviewers: list[str] = Field(default_factory=list)


class EnvironmentResponse(BaseModel):
    service_id: str
    environment: str
    environment_config: dict[str, Any]
    tool_context: ToolContext


class DependencyRecord(BaseModel):
    target: str
    kind: str = "external"
    criticality: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DependencyResponse(BaseModel):
    org_id: str
    service_id: str
    dependencies: list[DependencyRecord] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    type: str
    service_id: str
    title: str
    reference: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    org_id: str
    query: str
    type: str
    result_count: int
    results: list[SearchResult]


class SimilarIncidentResult(BaseModel):
    incident: dict[str, Any]
    score: float
    matched_fields: list[str] = Field(default_factory=list)


class SimilarIncidentsResponse(BaseModel):
    org_id: str
    service_id: str
    environment: str | None = None
    query: str
    incident_count: int
    incidents: list[SimilarIncidentResult]
