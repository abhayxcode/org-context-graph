from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        }


def _matches_service(service: dict[str, Any], normalized_query: str) -> bool:
    aliases = [str(alias).lower() for alias in service.get("aliases", [])]
    return (
        str(service.get("id", "")).lower() == normalized_query
        or str(service.get("name", "")).lower() == normalized_query
        or normalized_query in aliases
        or normalized_query in [repo.rsplit("/", 1)[-1].lower() for repo in service.get("repos", [])]
    )
