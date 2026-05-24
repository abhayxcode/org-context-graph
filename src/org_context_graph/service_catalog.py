from __future__ import annotations

import json
import re
from copy import deepcopy
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

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.catalog)

    @property
    def org_id(self) -> str:
        return str(self.catalog["org_id"])

    def services(self) -> list[dict[str, Any]]:
        return list(self.catalog.get("services", []))

    def teams(self) -> list[dict[str, Any]]:
        return list(self.catalog.get("teams", []))

    def incidents(self) -> list[dict[str, Any]]:
        return list(self._incidents)

    def code_index(self) -> list[dict[str, Any]]:
        return list(self.catalog.get("code_index", []))

    def health_snapshots(self) -> dict[str, Any]:
        return dict(self.catalog.get("health", {}))

    def validation_warnings(self) -> list[dict[str, Any]]:
        return catalog_warnings(self.catalog)

    def get_service(self, org_id: str, service_id: str) -> dict[str, Any] | None:
        if org_id != self.org_id:
            return None
        for service in self.services():
            if service.get("id") == service_id:
                return service
        return None

    def get_owner(self, org_id: str, team_id: str) -> dict[str, Any] | None:
        if org_id != self.org_id:
            return None

        owned_services = [
            str(service["id"])
            for service in self.services()
            if team_id in service.get("owners", [])
        ]
        for team in self.teams():
            if team.get("id") == team_id:
                return {
                    **team,
                    "services": owned_services,
                    "metadata": team.get("metadata", {}),
                }

        if owned_services:
            return {
                "id": team_id,
                "services": owned_services,
                "metadata": {},
            }
        return None

    def get_repo_context(self, org_id: str, repository_id: str) -> dict[str, Any] | None:
        if org_id != self.org_id:
            return None

        normalized_repo = _normalize_repository_id(repository_id)
        for service in self.services():
            repositories = [
                primary_repository(service),
                *[parse_repository(str(repo)) for repo in service.get("repos", [])],
                *[dict(repo) for repo in service.get("repositories", [])],
            ]
            for repository in repositories:
                if _repository_matches(repository, normalized_repo):
                    owners = [
                        owner
                        for owner_id in service.get("owners", [])
                        if (owner := self.get_owner(org_id=org_id, team_id=str(owner_id))) is not None
                    ]
                    return {
                        "org_id": self.org_id,
                        "repository": _hydrate_repository(repository),
                        "service": service,
                        "owners": owners,
                        "environments": sorted(service.get("environments", {}).keys()),
                        "build_commands": service.get("build_commands", []),
                        "test_commands": service.get("test_commands", []),
                        "suggested_reviewers": service.get("suggested_reviewers", service.get("owners", [])),
                    }
        return None

    def get_dependencies(self, org_id: str, service_id: str) -> dict[str, Any] | None:
        service = self.get_service(org_id=org_id, service_id=service_id)
        if service is None:
            return None

        dependents = [
            str(candidate["id"])
            for candidate in self.services()
            if candidate.get("id") != service_id
            and service_id in [_dependency_target(dependency) for dependency in candidate.get("dependencies", [])]
        ]
        return {
            "org_id": self.org_id,
            "service_id": service_id,
            "dependencies": [
                _normalize_dependency(dependency)
                for dependency in service.get("dependencies", [])
            ],
            "dependents": sorted(dependents),
        }

    def ingest_health_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        normalized_snapshot = dict(snapshot)
        service_id = str(normalized_snapshot.get("service_id", "")).strip()
        if self.get_service(org_id=self.org_id, service_id=service_id) is None:
            raise CatalogValidationError(f"service_id '{service_id}' does not exist")

        environment = normalize_environment(str(normalized_snapshot.get("environment", "prod")))
        service = self.get_service(org_id=self.org_id, service_id=service_id)
        assert service is not None
        if environment not in service.get("environments", {}):
            raise CatalogValidationError(f"service_id '{service_id}' has no '{environment}' environment")

        normalized_snapshot["service_id"] = service_id
        normalized_snapshot["environment"] = environment
        normalized_snapshot.setdefault("summary", "")
        normalized_snapshot["signals"] = _normalize_health_signals(normalized_snapshot.get("signals"))
        self.catalog.setdefault("health", {})[_health_key(service_id, environment)] = normalized_snapshot
        return normalized_snapshot

    def get_health_summary(
        self,
        *,
        org_id: str,
        service_id: str,
        environment: str,
    ) -> dict[str, Any] | None:
        service = self.get_service(org_id=org_id, service_id=service_id)
        if service is None:
            return None
        normalized_environment = normalize_environment(environment)
        if normalized_environment not in service.get("environments", {}):
            return None

        snapshot = self.health_snapshots().get(_health_key(service_id, normalized_environment))
        if snapshot:
            normalized_snapshot = dict(snapshot)
            normalized_snapshot.setdefault("service_id", service_id)
            normalized_snapshot.setdefault("environment", normalized_environment)
            normalized_snapshot.setdefault("summary", "")
            normalized_snapshot.setdefault("checked_at", None)
            normalized_snapshot.setdefault("source", None)
            normalized_snapshot["signals"] = _normalize_health_signals(normalized_snapshot.get("signals"))
            return {
                "org_id": self.org_id,
                **normalized_snapshot,
            }
        return {
            "org_id": self.org_id,
            "service_id": service_id,
            "environment": normalized_environment,
            "status": "unknown",
            "summary": "No cached health snapshot is available.",
            "checked_at": None,
            "signals": {},
            "source": None,
        }

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
            if _matches_service(service, normalized_query, self.teams())
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
            "tool_context": build_tool_context(
                service,
                normalized_env,
                environment_config,
                recent_incidents=self.recent_incidents(
                    org_id=org_id,
                    service_id=str(service["id"]),
                    environment=normalized_env,
                ),
            ),
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
            results.extend(_search_service(service, normalized_query, result_type, self.teams()))
        results.extend(_search_code_index(self.code_index(), normalized_query, result_type))

        return sorted(results, key=lambda item: (-item["score"], item["type"], item["title"]))[:limit]

    def ingest_repo_index(
        self,
        *,
        org_id: str,
        repository: str,
        service_id: str | None,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if org_id != self.org_id:
            return None

        repo_context = self.get_repo_context(org_id=org_id, repository_id=repository)
        if repo_context is None:
            return None

        resolved_service_id = service_id or str(repo_context["service"]["id"])
        if self.get_service(org_id=org_id, service_id=resolved_service_id) is None:
            raise CatalogValidationError(f"service_id '{resolved_service_id}' does not exist")

        repository_name = str(repo_context["repository"].get("full_name") or repository)
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for entry in entries:
            reason = _code_index_rejection_reason(entry)
            if reason:
                rejected.append({
                    "path": entry.get("path", ""),
                    "reason": reason,
                })
                continue

            accepted.append(_normalize_code_index_entry(
                entry,
                repository=repository_name,
                service_id=resolved_service_id,
            ))

        existing = [
            entry for entry in self.code_index()
            if not (
                entry.get("repository") == repository_name
                and entry.get("service_id") == resolved_service_id
            )
        ]
        self.catalog["code_index"] = [*existing, *accepted]
        return {
            "status": "accepted" if not rejected else "accepted_with_rejections",
            "org_id": self.org_id,
            "repository": repository_name,
            "service_id": resolved_service_id,
            "indexed_count": len(accepted),
            "rejected_count": len(rejected),
            "rejected": rejected,
        }

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

    def recent_incidents(
        self,
        *,
        org_id: str,
        service_id: str,
        environment: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        if org_id != self.org_id:
            return []
        if self.get_service(org_id=org_id, service_id=service_id) is None:
            return []

        normalized_environment = normalize_environment(environment) if environment else None
        incidents = [
            incident
            for incident in self._incidents
            if incident.get("service_id") == service_id
            and (
                not normalized_environment
                or incident.get("environment") in {None, "", normalized_environment}
            )
        ]
        return sorted(
            incidents,
            key=lambda incident: str(incident.get("occurred_at", "")),
            reverse=True,
        )[:limit]

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


def _matches_service(
    service: dict[str, Any],
    normalized_query: str,
    teams: list[dict[str, Any]] | None = None,
) -> bool:
    aliases = [str(alias).lower() for alias in service.get("aliases", [])]
    repositories = [_repository_name(repository).lower() for repository in service.get("repositories", [])]
    return (
        str(service.get("id", "")).lower() == normalized_query
        or str(service.get("name", "")).lower() == normalized_query
        or normalized_query in aliases
        or normalized_query in _channel_terms(service.get("channels", []))
        or normalized_query in _owner_terms(service, teams or [])
        or normalized_query in repositories
        or normalized_query in [repo.rsplit("/", 1)[-1].lower() for repo in service.get("repos", [])]
    )


def _search_service(
    service: dict[str, Any],
    normalized_query: str,
    result_type: str,
    teams: list[dict[str, Any]] | None = None,
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
            *[str(channel) for channel in service.get("channels", [])],
            *_owner_terms(service, teams or []),
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
        add_result("owner", str(owner), str(owner), [str(owner), *_team_terms(str(owner), teams or []), service_name])

    for channel in service.get("channels", []):
        add_result("channel", str(channel), str(channel), [str(channel), service_name])

    for runbook in service.get("runbooks", []):
        add_result("runbook", str(runbook).rsplit("/", 1)[-1], str(runbook), [str(runbook), service_name])

    for dependency in service.get("dependencies", []):
        normalized_dependency = _normalize_dependency(dependency)
        add_result(
            "dependency",
            normalized_dependency["target"],
            normalized_dependency["target"],
            [
                normalized_dependency["target"],
                normalized_dependency["kind"],
                normalized_dependency.get("criticality", ""),
                service_name,
            ],
            {
                "kind": normalized_dependency["kind"],
                "criticality": normalized_dependency.get("criticality"),
            },
        )

    for playbook in service.get("playbooks", []):
        playbook_id = str(playbook.get("id", ""))
        title = str(playbook.get("title", playbook_id))
        summary = str(playbook.get("summary", ""))
        add_result(
            "playbook",
            title,
            playbook_id,
            [
                playbook_id,
                title,
                summary,
                *[str(step) for step in playbook.get("steps", [])],
                *[str(tag) for tag in playbook.get("tags", [])],
                service_name,
            ],
            {
                "summary": summary,
                "tags": playbook.get("tags", []),
            },
        )

    return results


def _search_code_index(
    entries: list[dict[str, Any]],
    normalized_query: str,
    result_type: str,
) -> list[dict[str, Any]]:
    if result_type.strip().lower() not in {"all", "code"}:
        return []

    results: list[dict[str, Any]] = []
    for entry in entries:
        score = _match_score(normalized_query, [
            str(entry.get("path", "")),
            str(entry.get("symbol", "")),
            str(entry.get("summary", "")),
            str(entry.get("language", "")),
            str(entry.get("kind", "")),
        ])
        if score == 0:
            continue
        path = str(entry.get("path", ""))
        symbol = str(entry.get("symbol") or path)
        results.append({
            "type": "code",
            "service_id": str(entry.get("service_id", "")),
            "title": symbol,
            "reference": f"{entry.get('repository', '')}:{path}",
            "score": score,
            "metadata": {
                "repository": entry.get("repository"),
                "path": path,
                "symbol": entry.get("symbol"),
                "language": entry.get("language"),
                "kind": entry.get("kind"),
                "summary": entry.get("summary"),
            },
        })
    return results


def _owner_terms(service: dict[str, Any], teams: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for owner_id in service.get("owners", []):
        owner = str(owner_id)
        terms.append(owner.lower())
        terms.extend(_team_terms(owner, teams))
    return terms


def _team_terms(team_id: str, teams: list[dict[str, Any]]) -> list[str]:
    for team in teams:
        if team.get("id") != team_id:
            continue
        values = [
            team.get("id", ""),
            team.get("name", ""),
            team.get("github_team", ""),
            team.get("slack_channel", ""),
            team.get("oncall", ""),
        ]
        return [
            term
            for value in values
            for term in _channel_terms([str(value)])
        ]
    return []


def _channel_terms(values: list[Any]) -> list[str]:
    terms: list[str] = []
    for value in values:
        normalized = str(value).strip().lower()
        if not normalized:
            continue
        terms.append(normalized)
        if normalized.startswith("#"):
            terms.append(normalized[1:])
    return terms


def _normalize_dependency(dependency: Any) -> dict[str, Any]:
    if isinstance(dependency, dict):
        target = str(dependency.get("target") or dependency.get("id") or "").strip()
        metadata = {
            key: value
            for key, value in dependency.items()
            if key not in {"target", "id", "kind", "criticality"}
        }
        return {
            "target": target,
            "kind": str(dependency.get("kind") or _infer_dependency_kind(target)),
            "criticality": dependency.get("criticality"),
            "metadata": metadata,
        }

    target = str(dependency).strip()
    return {
        "target": target,
        "kind": _infer_dependency_kind(target),
        "criticality": None,
        "metadata": {},
    }


def _dependency_target(dependency: Any) -> str:
    return _normalize_dependency(dependency)["target"]


def _health_key(service_id: str, environment: str) -> str:
    return f"{service_id}:{environment}"


def _normalize_health_signals(signals: Any) -> dict[str, Any]:
    if isinstance(signals, dict):
        return signals
    if isinstance(signals, list):
        normalized: dict[str, Any] = {}
        for index, signal in enumerate(signals):
            if isinstance(signal, dict):
                name = str(signal.get("name") or f"signal_{index}")
                normalized[name] = {
                    key: value
                    for key, value in signal.items()
                    if key != "name"
                }
            else:
                normalized[f"signal_{index}"] = signal
        return normalized
    return {}


def _infer_dependency_kind(target: str) -> str:
    normalized = target.lower()
    if "postgres" in normalized or "mysql" in normalized or "db" in normalized:
        return "database"
    if "redis" in normalized or "cache" in normalized:
        return "cache"
    if "queue" in normalized or "kafka" in normalized or "pubsub" in normalized:
        return "queue"
    return "external"


SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]


def _normalize_code_index_entry(
    entry: dict[str, Any],
    *,
    repository: str,
    service_id: str,
) -> dict[str, Any]:
    return {
        "repository": repository,
        "service_id": service_id,
        "path": str(entry.get("path", "")).strip(),
        "symbol": entry.get("symbol"),
        "summary": str(entry.get("summary", "")),
        "language": entry.get("language"),
        "kind": entry.get("kind"),
        "metadata": entry.get("metadata", {}),
    }


def _code_index_rejection_reason(entry: dict[str, Any]) -> str | None:
    if not str(entry.get("path", "")).strip():
        return "path is required"
    if _contains_secret(entry):
        return "entry appears to contain a secret"
    return None


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_secret(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


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

    for team_index, team in enumerate(catalog.get("teams", [])):
        errors.extend(_validate_team(team, f"teams[{team_index}]"))

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

        for list_field in ["channels", "build_commands", "test_commands", "suggested_reviewers"]:
            if list_field in service and not isinstance(service.get(list_field), list):
                errors.append(f"{prefix}.{list_field} must be a list")

        for playbook_index, playbook in enumerate(service.get("playbooks", [])):
            errors.extend(_validate_playbook(playbook, f"{prefix}.playbooks[{playbook_index}]"))

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


def catalog_warnings(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    teams = {
        str(team.get("id"))
        for team in catalog.get("teams", [])
        if isinstance(team, dict) and team.get("id")
    }

    for index, service in enumerate(catalog.get("services", [])):
        if not isinstance(service, dict):
            continue
        service_id = str(service.get("id", f"services[{index}]"))
        prefix = f"services[{index}]"
        if not service.get("runbooks"):
            warnings.append(_catalog_warning(prefix, service_id, "missing_runbooks", "service has no runbooks"))
        if not service.get("playbooks"):
            warnings.append(_catalog_warning(prefix, service_id, "missing_playbooks", "service has no troubleshooting playbooks"))
        if not service.get("test_commands"):
            warnings.append(_catalog_warning(prefix, service_id, "missing_test_commands", "service has no test commands"))
        if not service.get("channels"):
            warnings.append(_catalog_warning(prefix, service_id, "missing_channels", "service has no team or service channels"))
        for owner in service.get("owners", []):
            if teams and owner not in teams:
                warnings.append(_catalog_warning(
                    prefix,
                    service_id,
                    "unknown_owner",
                    f"owner '{owner}' has no team metadata",
                ))

        for environment_name, environment_config in service.get("environments", {}).items():
            if not isinstance(environment_config, dict):
                continue
            env_prefix = f"{prefix}.environments.{environment_name}"
            if not environment_config.get("observability"):
                warnings.append(_catalog_warning(
                    env_prefix,
                    service_id,
                    "missing_observability",
                    f"environment '{environment_name}' has no observability targets",
                ))
            if not environment_config.get("ci"):
                warnings.append(_catalog_warning(
                    env_prefix,
                    service_id,
                    "missing_ci",
                    f"environment '{environment_name}' has no CI/deploy metadata",
                ))

    return warnings


def _catalog_warning(path: str, service_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "path": path,
        "service_id": service_id,
        "code": code,
        "message": message,
    }


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


def _validate_team(team: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(team, dict):
        return [f"{prefix} must be an object"]
    if not str(team.get("id", "")).strip():
        errors.append(f"{prefix}.id is required")
    for list_field in ["members"]:
        if list_field in team and not isinstance(team.get(list_field), list):
            errors.append(f"{prefix}.{list_field} must be a list")
    return errors


def _validate_playbook(playbook: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(playbook, dict):
        return [f"{prefix} must be an object"]
    if not str(playbook.get("id", "")).strip():
        errors.append(f"{prefix}.id is required")
    if not str(playbook.get("title", "")).strip():
        errors.append(f"{prefix}.title is required")
    for list_field in ["steps", "tags"]:
        if list_field in playbook and not isinstance(playbook.get(list_field), list):
            errors.append(f"{prefix}.{list_field} must be a list")
    return errors


def build_tool_context(
    service: dict[str, Any],
    environment: str,
    environment_config: dict[str, Any],
    recent_incidents: list[dict[str, Any]] | None = None,
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
        "channels": service.get("channels", []),
        "repository": repository,
        "runtime": runtime,
        "observability": observability,
        "ci": ci,
        "runbooks": service.get("runbooks", []),
        "dependencies": service.get("dependencies", []),
        "playbooks": service.get("playbooks", []),
        "recent_incidents": recent_incidents or [],
        "build_commands": service.get("build_commands", []),
        "test_commands": service.get("test_commands", []),
        "suggested_reviewers": service.get("suggested_reviewers", service.get("owners", [])),
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
                "repository": repository_name,
                "ref": repository.get("default_branch"),
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


def _normalize_repository_id(value: str) -> str:
    repository = parse_repository(value)
    if repository.get("full_name"):
        return str(repository["full_name"]).lower()
    return value.strip().removesuffix(".git").lower()


def _repository_matches(repository: dict[str, Any], normalized_repo: str) -> bool:
    hydrated = _hydrate_repository(repository)
    candidates = {
        str(hydrated.get("full_name", "")).lower(),
        str(hydrated.get("url", "")).removesuffix(".git").lower(),
        str(hydrated.get("name", "")).lower(),
    }
    return normalized_repo in candidates


def _hydrate_repository(repository: dict[str, Any]) -> dict[str, Any]:
    if repository.get("full_name") or not repository.get("owner") or not repository.get("name"):
        return dict(repository)

    hydrated = dict(repository)
    host = str(hydrated.get("host", "github.com")).strip() or "github.com"
    owner = str(hydrated["owner"]).strip()
    name = str(hydrated["name"]).strip()
    hydrated.setdefault("provider", "github" if host == "github.com" else host)
    hydrated.setdefault("host", host)
    hydrated.setdefault("full_name", f"{owner}/{name}")
    hydrated.setdefault("url", f"https://{host}/{owner}/{name}")
    return hydrated


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
