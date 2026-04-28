from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from org_context_graph.storage import JsonCatalogStore, MemoryCatalogStore


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "service-catalog.json"


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf8"))

    def test_memory_store_copies_catalog(self) -> None:
        store = MemoryCatalogStore(self.catalog)
        loaded = store.load()
        loaded["org_id"] = "mutated"

        self.assertEqual(store.load()["org_id"], "default")

    def test_json_store_loads_and_saves_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text(json.dumps(self.catalog), encoding="utf8")
            store = JsonCatalogStore(path)

            loaded = store.load()
            loaded["incidents"] = [{"id": "incident-1", "service_id": "backend"}]
            store.save(loaded)

            reloaded = JsonCatalogStore(path).load()
            self.assertEqual(reloaded["incidents"][0]["id"], "incident-1")
            self.assertFalse(path.with_name("catalog.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
