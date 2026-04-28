from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class CatalogIngestRequest(BaseModel):
    org_id: str
    services: list[dict[str, Any]]

    class Config:
        extra = "allow"


class CatalogIngestResponse(BaseModel):
    status: str
    org_id: str
    service_count: int


class ToolContext(BaseModel):
    service_id: str
    environment: str
    owners: list[str] = Field(default_factory=list)
    repository: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    observability: dict[str, Any] = Field(default_factory=dict)
    ci: dict[str, Any] = Field(default_factory=dict)
    runbooks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
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
    repos: list[str] = Field(default_factory=list)
    repositories: list[dict[str, Any]] = Field(default_factory=list)
    environments: dict[str, dict[str, Any]]
    runbooks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    class Config:
        extra = "allow"
