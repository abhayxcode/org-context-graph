from __future__ import annotations

from typing import Any

import yaml


class CatalogParseError(ValueError):
    pass


def parse_catalog_yaml(payload: str, *, default_org_id: str = "default") -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        raise CatalogParseError(str(error)) from error

    if not isinstance(parsed, dict):
        raise CatalogParseError("YAML catalog must be an object")

    if "services" in parsed:
        catalog = dict(parsed)
        catalog.setdefault("org_id", default_org_id)
        return catalog

    if "id" in parsed:
        return {
            "org_id": default_org_id,
            "services": [parsed],
        }

    raise CatalogParseError("YAML catalog must define services or a service id")
