from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

from algotrading.frontend.app import create_app
from fastapi.testclient import TestClient

_TEST_MODULE = Path(__file__).parents[1] / "test_positions_api.py"
_GOLDEN = Path(__file__).parent / "positions_book.json"


def _load_test_module() -> object:
    spec = importlib.util.spec_from_file_location("positions_golden_seed", _TEST_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_TEST_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regenerate() -> None:
    module = _load_test_module()
    root = Path(tempfile.mkdtemp()) / "data"
    ctx = module._seeded_context(root)  # type: ignore[attr-defined]
    with TestClient(create_app(ctx)) as client:
        body = client.get("/api/positions").json()
    body.pop("source_ts")
    _GOLDEN.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    regenerate()
