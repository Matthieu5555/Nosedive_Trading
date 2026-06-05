"""Config router tests: listing, reading, path traversal guard, and typed errors."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore


@pytest.fixture
def config_ctx(tmp_path: Path) -> AppContext:
    """Context with a pre-populated configs dir."""
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
    assert "ignore.txt" not in names  # filtered out


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
    # A traversal attempt is reduced to a bare name; the bare name isn't a config suffix.
    response = config_client.get("/api/config/../../../etc/passwd")
    assert response.status_code in (400, 404)  # Either is safe; must not be 200.


def test_config_empty_dir_returns_empty_list(infra_client: TestClient) -> None:
    # infra_client uses a ctx with a non-existent configs_dir → no files.
    payload = infra_client.get("/api/config").json()
    assert payload["files"] == []
