from __future__ import annotations

from dataclasses import dataclass


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
