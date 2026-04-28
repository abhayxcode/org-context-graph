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


class CatalogValidationError(ValueError):
    pass


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
        self._incidents = list(catalog.get("incidents", []))
        validate_catalog(self.catalog)

    @classmethod
    def from_file(cls, path: str | Path) -> "ServiceCatalog":
        return cls(json.loads(Path(path).read_text(encoding="utf8")))

    @property
    def org_id(self) -> str:
        return str(self.catalog["org_id"])

    def services(self) -> list[dict[str, Any]]:
        return list(self.catalog.get("services", []))

    def incidents(self) -> list[dict[str, Any]]:
        return list(self._incidents)

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

    def search(
        self,
        *,
        org_id: str,
        query: str,
        result_type: str = "all",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if org_id != self.org_id:
            return []

        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        results: list[dict[str, Any]] = []
        for service in self.services():
            results.extend(_search_service(service, normalized_query, result_type))

        return sorted(results, key=lambda item: (-item["score"], item["type"], item["title"]))[:limit]

    def ingest_incident(self, incident: dict[str, Any]) -> dict[str, Any]:
        normalized_incident = dict(incident)
        service_id = str(normalized_incident.get("service_id", "")).strip()
        if self.get_service(org_id=self.org_id, service_id=service_id) is None:
            raise CatalogValidationError(f"service_id '{service_id}' does not exist")

        normalized_incident["service_id"] = service_id
        if normalized_incident.get("environment"):
            normalized_incident["environment"] = normalize_environment(str(normalized_incident["environment"]))
        if not normalized_incident.get("id"):
            normalized_incident["id"] = f"incident-{len(self._incidents) + 1}"

        self._incidents.append(normalized_incident)
        self.catalog["incidents"] = self.incidents()
        return normalized_incident

    def similar_incidents(
        self,
        *,
        org_id: str,
        service_id: str,
        query: str = "",
        environment: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if org_id != self.org_id:
            return []
        if self.get_service(org_id=org_id, service_id=service_id) is None:
            return []

        normalized_query = query.strip().lower()
        normalized_environment = normalize_environment(environment) if environment else None
        results: list[dict[str, Any]] = []
        for incident in self._incidents:
            if incident.get("service_id") != service_id:
                continue
            if normalized_environment and incident.get("environment") not in {None, "", normalized_environment}:
                continue

            score = _incident_score(incident, normalized_query, normalized_environment)
            if score == 0:
                continue
            results.append({
                "incident": incident,
                "score": score,
                "matched_fields": _incident_matched_fields(incident, normalized_query),
            })

        return sorted(
            results,
            key=lambda item: (-item["score"], str(item["incident"].get("occurred_at", ""))),
        )[:limit]


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


def _search_service(
    service: dict[str, Any],
    normalized_query: str,
    result_type: str,
) -> list[dict[str, Any]]:
    allowed_type = result_type.strip().lower()
    results: list[dict[str, Any]] = []

    def add_result(
        item_type: str,
        title: str,
        reference: str,
        haystack: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if allowed_type not in {"all", item_type}:
            return
        score = _match_score(normalized_query, haystack)
        if score == 0:
            return
        results.append({
            "type": item_type,
            "service_id": service["id"],
            "title": title,
            "reference": reference,
            "score": score,
            "metadata": metadata or {},
        })

    repository = primary_repository(service)
    service_name = str(service.get("name", ""))
    add_result(
        "service",
        service_name,
        str(service.get("id", "")),
        [
            str(service.get("id", "")),
            service_name,
            *[str(alias) for alias in service.get("aliases", [])],
        ],
        {"owners": service.get("owners", [])},
    )
    add_result(
        "repository",
        repository.get("full_name", repository.get("url", "")),
        repository.get("url", repository.get("full_name", "")),
        [
            repository.get("full_name", ""),
            repository.get("name", ""),
            repository.get("url", ""),
            *[str(repo) for repo in service.get("repos", [])],
        ],
        {"provider": repository.get("provider")},
    )

    for owner in service.get("owners", []):
        add_result("owner", str(owner), str(owner), [str(owner), service_name])

    for runbook in service.get("runbooks", []):
        add_result("runbook", str(runbook).rsplit("/", 1)[-1], str(runbook), [str(runbook), service_name])

    for dependency in service.get("dependencies", []):
        add_result("dependency", str(dependency), str(dependency), [str(dependency), service_name])

    return results


def _match_score(normalized_query: str, haystack: list[str]) -> float:
    best_score = 0.0
    query_terms = [term for term in normalized_query.replace("/", " ").replace("-", " ").split() if term]
    for value in haystack:
        normalized_value = str(value).strip().lower()
        value_terms = set(normalized_value.replace("/", " ").replace("-", " ").split())
        if normalized_value == normalized_query:
            best_score = max(best_score, 1.0)
        elif normalized_query in normalized_value:
            best_score = max(best_score, 0.82)
        elif query_terms and all(term in value_terms for term in query_terms):
            best_score = max(best_score, 0.72)
        elif query_terms and any(term in value_terms for term in query_terms):
            best_score = max(best_score, 0.45)
    return best_score


def _incident_score(
    incident: dict[str, Any],
    normalized_query: str,
    normalized_environment: str | None,
) -> float:
    score = 0.35
    if normalized_environment and incident.get("environment") == normalized_environment:
        score += 0.2
    if not normalized_query:
        return score

    fields = [
        str(incident.get("title", "")),
        str(incident.get("summary", "")),
        str(incident.get("root_cause", "")),
        str(incident.get("resolution", "")),
        *[str(tag) for tag in incident.get("tags", [])],
    ]
    return max(score, _match_score(normalized_query, fields))


def _incident_matched_fields(incident: dict[str, Any], normalized_query: str) -> list[str]:
    if not normalized_query:
        return []

    matched_fields: list[str] = []
    for field in ["title", "summary", "root_cause", "resolution"]:
        if _match_score(normalized_query, [str(incident.get(field, ""))]) > 0:
            matched_fields.append(field)
    if _match_score(normalized_query, [str(tag) for tag in incident.get("tags", [])]) > 0:
        matched_fields.append("tags")
    return matched_fields


def validate_catalog(catalog: dict[str, Any]) -> None:
    errors: list[str] = []
    if not str(catalog.get("org_id", "")).strip():
        errors.append("org_id is required")

    services = catalog.get("services")
    if not isinstance(services, list) or not services:
        errors.append("services must be a non-empty list")
        raise CatalogValidationError("; ".join(errors))

    seen_service_ids: set[str] = set()
    for index, service in enumerate(services):
        prefix = f"services[{index}]"
        service_id = str(service.get("id", "")).strip()
        if not service_id:
            errors.append(f"{prefix}.id is required")
        elif service_id in seen_service_ids:
            errors.append(f"{prefix}.id '{service_id}' is duplicated")
        seen_service_ids.add(service_id)

        if not str(service.get("name", "")).strip():
            errors.append(f"{prefix}.name is required")

        owners = service.get("owners")
        if not isinstance(owners, list) or not owners:
            errors.append(f"{prefix}.owners must be a non-empty list")

        if not service.get("repositories") and not service.get("repos"):
            errors.append(f"{prefix} must define repositories or repos")
        for repository_index, repository in enumerate(service.get("repositories", [])):
            errors.extend(_validate_repository(repository, f"{prefix}.repositories[{repository_index}]"))

        environments = service.get("environments")
        if not isinstance(environments, dict) or not environments:
            errors.append(f"{prefix}.environments must be a non-empty object")
            continue

        for environment_name, environment_config in environments.items():
            env_prefix = f"{prefix}.environments.{environment_name}"
            if normalize_environment(str(environment_name)) != str(environment_name):
                errors.append(f"{env_prefix} must use normalized environment name")
            if not isinstance(environment_config, dict):
                errors.append(f"{env_prefix} must be an object")
                continue
            runtime = environment_config.get("runtime")
            if not isinstance(runtime, dict):
                errors.append(f"{env_prefix}.runtime must be an object")
            elif not str(runtime.get("provider", "")).strip():
                errors.append(f"{env_prefix}.runtime.provider is required")

    if errors:
        raise CatalogValidationError("; ".join(errors))


def _validate_repository(repository: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(repository, dict):
        return [f"{prefix} must be an object"]
    if not str(repository.get("owner", "")).strip():
        errors.append(f"{prefix}.owner is required")
    if not str(repository.get("name", "")).strip():
        errors.append(f"{prefix}.name is required")
    if repository.get("provider") and str(repository.get("provider")).strip() != "github":
        errors.append(f"{prefix}.provider currently supports only github")
    return errors


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
