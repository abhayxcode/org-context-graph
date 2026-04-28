from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ResolveResult:
    status: str
    service_id: str | None
    environment: str | None
    confidence: float
    reason: str | None = None


def normalize_environment(value: str) -> str:
    aliases = {
        "production": "prod",
        "prd": "prod",
        "live": "prod",
        "stage": "staging",
    }
    return aliases.get(value.strip().lower(), value.strip().lower())


class ServiceCatalog:
    def __init__(self, catalog: dict[str, Any]):
        self.catalog = catalog

    @classmethod
    def from_file(cls, path: str | Path) -> "ServiceCatalog":
        return cls(json.loads(Path(path).read_text(encoding="utf8")))

    @property
    def org_id(self) -> str:
        return str(self.catalog["org_id"])

    def services(self) -> list[dict[str, Any]]:
        return list(self.catalog.get("services", []))

    def get_service(self, org_id: str, service_id: str) -> dict[str, Any] | None:
        if org_id != self.org_id:
            return None
        for service in self.services():
            if service.get("id") == service_id:
                return service
        return None

    def resolve(self, *, org_id: str, query: str, environment: str) -> dict[str, Any]:
        if org_id != self.org_id:
            return {
                "status": "not_found",
                "confidence": 0,
                "reason": f"No catalog for org '{org_id}'.",
            }

        normalized_query = query.strip().lower()
        normalized_env = normalize_environment(environment)
        candidates = [
            service for service in self.services()
            if _matches_service(service, normalized_query)
        ]

        if not candidates:
            return {
                "status": "not_found",
                "confidence": 0,
                "candidates": [],
                "reason": f"No service matched '{query}'.",
            }

        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                "confidence": 0.45,
                "candidates": [service["id"] for service in candidates],
                "reason": f"Multiple services matched '{query}'.",
            }

        service = candidates[0]
        environment_config = service.get("environments", {}).get(normalized_env)
        if environment_config is None:
            return {
                "status": "environment_not_found",
                "confidence": 0.2,
                "service_id": service["id"],
                "environment": normalized_env,
                "reason": f"Service '{service['id']}' has no '{normalized_env}' environment.",
            }

        return {
            "status": "resolved",
            "confidence": 0.96,
            "service": service,
            "environment": normalized_env,
            "environment_config": environment_config,
            "tool_context": build_tool_context(service, normalized_env, environment_config),
        }


def _matches_service(service: dict[str, Any], normalized_query: str) -> bool:
    aliases = [str(alias).lower() for alias in service.get("aliases", [])]
    repositories = [_repository_name(repository).lower() for repository in service.get("repositories", [])]
    return (
        str(service.get("id", "")).lower() == normalized_query
        or str(service.get("name", "")).lower() == normalized_query
        or normalized_query in aliases
        or normalized_query in repositories
        or normalized_query in [repo.rsplit("/", 1)[-1].lower() for repo in service.get("repos", [])]
    )


def build_tool_context(
    service: dict[str, Any],
    environment: str,
    environment_config: dict[str, Any],
) -> dict[str, Any]:
    repository = primary_repository(service)
    repository_name = repository.get("full_name", "")
    runtime = environment_config.get("runtime", {})
    observability = environment_config.get("observability", {})
    ci = environment_config.get("ci", {})

    return {
        "service_id": service.get("id"),
        "environment": environment,
        "owners": service.get("owners", []),
        "repository": repository,
        "runtime": runtime,
        "observability": observability,
        "ci": ci,
        "runbooks": service.get("runbooks", []),
        "dependencies": service.get("dependencies", []),
        "tool_arguments": {
            "code_host.get_recent_changes": _without_empty({
                "repository": repository_name,
                "branch": repository.get("default_branch"),
            }),
            "code_host.create_draft_pr": _without_empty({
                "repository": repository_name,
                "base": repository.get("default_branch"),
            }),
            "ci.get_checks": _without_empty({
                "repository": repository_name,
                "workflow": ci.get("workflow"),
            }),
            "deploy.get_recent_deploys": _without_empty({
                "provider": ci.get("provider"),
                "workflow": ci.get("workflow"),
            }),
            "runtime.get_workload_status": _without_empty({
                "provider": runtime.get("provider"),
                "namespace": runtime.get("namespace"),
                "workload": runtime.get("workload"),
            }),
            "metrics.get_service_health": _without_empty({
                "target": observability.get("metrics"),
            }),
            "errors.get_recent_errors": _without_empty({
                "project": observability.get("errors"),
            }),
            "docs.search_runbooks": _without_empty({
                "runbooks": service.get("runbooks", []),
            }),
        },
    }


def primary_repository(service: dict[str, Any]) -> dict[str, Any]:
    repositories = service.get("repositories", [])
    if repositories:
        repository = dict(repositories[0])
        owner = str(repository.get("owner", "")).strip()
        name = str(repository.get("name", "")).strip()
        host = str(repository.get("host", "github.com")).strip() or "github.com"
        if owner and name:
            repository.setdefault("provider", "github" if host == "github.com" else host)
            repository.setdefault("full_name", f"{owner}/{name}")
            repository.setdefault("url", f"https://{host}/{owner}/{name}")
        return repository

    repos = service.get("repos", [])
    if not repos:
        return {}
    return parse_repository(str(repos[0]))


def parse_repository(value: str) -> dict[str, Any]:
    raw = value.strip()
    if raw.startswith("git@"):
        host_and_path = raw.removeprefix("git@").replace(":", "/", 1)
        raw = f"https://{host_and_path}"
    elif "://" not in raw and raw.count("/") == 1:
        raw = f"https://github.com/{raw}"
    elif "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        return {"raw": value}

    owner = path_parts[-2]
    name = path_parts[-1].removesuffix(".git")
    host = parsed.netloc or "github.com"
    provider = "github" if host == "github.com" else host
    return {
        "provider": provider,
        "host": host,
        "owner": owner,
        "name": name,
        "full_name": f"{owner}/{name}",
        "default_branch": "main",
        "url": f"https://{host}/{owner}/{name}",
    }


def _repository_name(repository: dict[str, Any]) -> str:
    if repository.get("name"):
        return str(repository["name"])
    if repository.get("full_name"):
        return str(repository["full_name"]).rsplit("/", 1)[-1]
    if repository.get("url"):
        return str(repository["url"]).rstrip("/").rsplit("/", 1)[-1]
    return ""


def _without_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != []
    }
