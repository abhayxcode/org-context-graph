from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol


class CatalogStore(Protocol):
    def load(self) -> dict[str, Any]:
        ...

    def save(self, catalog: dict[str, Any]) -> None:
        ...


class JsonCatalogStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf8"))

    def save(self, catalog: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(
            json.dumps(catalog, indent=2, sort_keys=True) + "\n",
            encoding="utf8",
        )
        temp_path.replace(self.path)


class MemoryCatalogStore:
    def __init__(self, catalog: dict[str, Any]):
        self.catalog = deepcopy(catalog)

    def load(self) -> dict[str, Any]:
        return deepcopy(self.catalog)

    def save(self, catalog: dict[str, Any]) -> None:
        self.catalog = deepcopy(catalog)
