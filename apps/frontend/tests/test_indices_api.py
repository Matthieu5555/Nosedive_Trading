from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_REPO_CONFIGS = Path(__file__).resolve().parents[3] / "configs"


@pytest.fixture
def shipped_configs_client(tmp_path: Path) -> Iterator[TestClient]:
    ctx = AppContext(
        store_root=tmp_path / "data",
        configs_dir=_REPO_CONFIGS,
        store=ParquetStore(tmp_path / "data"),
    )
    with TestClient(create_app(ctx)) as client:
        yield client


@pytest.fixture
def drifted_configs_client(tmp_path: Path) -> Iterator[TestClient]:
    configs_dir = tmp_path / "configs"
    shutil.copytree(_REPO_CONFIGS, configs_dir)
    universe = configs_dir / "universe.yaml"
    text = universe.read_text(encoding="utf-8")
    drifted = text.replace(
        "  strike_window_pct: 0.35",
        "  strike_window_pct: 0.35\n  field_from_a_newer_schema: 1",
    )
    assert drifted != text, "expected to anchor the injected field under strike_selection"
    universe.write_text(drifted, encoding="utf-8")
    ctx = AppContext(
        store_root=tmp_path / "data",
        configs_dir=configs_dir,
        store=ParquetStore(tmp_path / "data"),
    )
    with TestClient(create_app(ctx)) as client:
        yield client


def test_indices_lists_only_the_enabled_registry_set(shipped_configs_client: TestClient) -> None:
    payload = shipped_configs_client.get("/api/indices").json()
    symbols = [item["symbol"] for item in payload["indices"]]
    assert symbols == ["SX5E"]
    assert "SPX" not in symbols
    assert all(item.get("name") for item in payload["indices"])
    assert payload["indices"][0]["name"] == "EURO STOXX 50"
    assert payload["indices"][0]["currency"] == "EUR"


def test_indices_is_empty_not_500_when_no_registry(infra_client: TestClient) -> None:
    response = infra_client.get("/api/indices")
    assert response.status_code == 200
    assert response.json() == {"indices": []}


def test_indices_degrades_to_empty_not_500_on_schema_drift(
    drifted_configs_client: TestClient,
) -> None:
    response = drifted_configs_client.get("/api/indices")
    assert response.status_code == 200
    assert response.json() == {"indices": []}
