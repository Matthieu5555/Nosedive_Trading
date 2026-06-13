"""/api/indices — the index selector is driven by the registry's enabled set, not a hard-coded list.

Pins the contract the web selector depends on: the endpoint returns exactly the registry's
``enabled`` entries (symbol + display name) loaded from the shipped ``configs/universe.yaml``,
so parking an index (``enabled: false``) drops it from the selector with no front-end change.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

# apps/frontend/tests/ → parents[3] is the repo root, where the real configs/ live.
_REPO_CONFIGS = Path(__file__).resolve().parents[3] / "configs"


@pytest.fixture
def shipped_configs_client(tmp_path: Path) -> Iterator[TestClient]:
    """A BFF client whose context points at the real shipped configs/ (empty tmp store)."""
    ctx = AppContext(
        store_root=tmp_path / "data",
        configs_dir=_REPO_CONFIGS,
        store=ParquetStore(tmp_path / "data"),
    )
    with TestClient(create_app(ctx)) as client:
        yield client


def test_indices_lists_only_the_enabled_registry_set(shipped_configs_client: TestClient) -> None:
    payload = shipped_configs_client.get("/api/indices").json()
    symbols = [item["symbol"] for item in payload["indices"]]
    # SX5E is the single live focus pipeline; SPX is parked (enabled:false) so it must NOT appear
    # in the selector even though it is still in the registry (T-index-only-refactor).
    assert symbols == ["SX5E"]
    assert "SPX" not in symbols
    # Each entry carries a display name for the selector label.
    assert all(item.get("name") for item in payload["indices"])
    assert payload["indices"][0]["name"] == "EURO STOXX 50"


def test_indices_is_empty_not_500_when_no_registry(infra_client: TestClient) -> None:
    # The default test context points at an empty tmp configs dir (no universe.yaml). The
    # endpoint degrades to a labeled empty list, never a 500 — the selector renders empty/disabled.
    response = infra_client.get("/api/indices")
    assert response.status_code == 200
    assert response.json() == {"indices": []}
