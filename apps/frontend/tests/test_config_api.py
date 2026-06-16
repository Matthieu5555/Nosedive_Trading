from __future__ import annotations

from pathlib import Path

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


@pytest.fixture
def config_ctx(tmp_path: Path) -> AppContext:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "default.toml").write_text('[meta]\nversion = "test"\n', encoding="utf-8")
    (configs_dir / "extra.yaml").write_text("key: value\n", encoding="utf-8")
    (configs_dir / "ignore.txt").write_text("not a config", encoding="utf-8")
    return AppContext(
        store_root=tmp_path / "data",
        configs_dir=configs_dir,
        store=ParquetStore(tmp_path / "data"),
    )


@pytest.fixture
def config_client(config_ctx: AppContext) -> TestClient:
    return TestClient(create_app(config_ctx))


def test_config_lists_only_config_files(config_client: TestClient) -> None:
    payload = config_client.get("/api/config").json()
    names = payload["files"]
    assert "default.toml" in names
    assert "extra.yaml" in names
    assert "ignore.txt" not in names


def test_config_reads_existing_file(config_client: TestClient) -> None:
    payload = config_client.get("/api/config/default.toml").json()
    assert payload["filename"] == "default.toml"
    assert "version" in payload["content"]


def test_config_missing_file_returns_404(config_client: TestClient) -> None:
    response = config_client.get("/api/config/noexist.toml")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_config_unsupported_suffix_returns_400(config_client: TestClient) -> None:
    response = config_client.get("/api/config/settings.txt")
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_config"


def test_config_path_traversal_is_blocked(config_client: TestClient) -> None:
    response = config_client.get("/api/config/../../../etc/passwd")
    assert response.status_code in (400, 404)


def test_config_empty_dir_returns_empty_list(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/config").json()
    assert payload["files"] == []


_EXPECTED_DEFAULT_BANDS = (
    [f"{c:02d}dp" for c in range(30, 0, -2)]
    + ["atm", "atmp"]
    + [f"{c:02d}dc" for c in range(2, 31, 2)]
)


def test_delta_bands_falls_back_to_default_axis(config_client: TestClient) -> None:
    response = config_client.get("/api/config/delta-bands")
    assert response.status_code == 200
    assert response.json()["delta_bands"] == _EXPECTED_DEFAULT_BANDS


def test_delta_bands_axis_is_ordered_put_to_call(config_client: TestClient) -> None:
    bands = config_client.get("/api/config/delta-bands").json()["delta_bands"]
    assert len(bands) == 32
    assert bands[0] == "30dp"
    assert bands[14] == "02dp"
    assert bands[15] == "atm"
    assert bands[16] == "atmp"
    assert bands[17] == "02dc"
    assert bands[-1] == "30dc"


def test_delta_bands_no_config_dir_still_populated(infra_client: TestClient) -> None:
    bands = infra_client.get("/api/config/delta-bands").json()["delta_bands"]
    assert bands == _EXPECTED_DEFAULT_BANDS


def test_delta_bands_from_real_config_bundle(tmp_path: Path) -> None:
    repo_configs = Path(__file__).resolve().parents[3] / "configs"
    if not repo_configs.is_dir():
        pytest.skip("repo configs/ bundle not present")
    ctx = AppContext(
        store_root=tmp_path / "data",
        configs_dir=repo_configs,
        store=ParquetStore(tmp_path / "data"),
    )
    bands = TestClient(create_app(ctx)).get("/api/config/delta-bands").json()["delta_bands"]
    assert bands[0] == "30dp"
    assert "atm" in bands and "atmp" in bands
    assert bands[-1] == "30dc"
    assert len(set(bands)) == len(bands)
