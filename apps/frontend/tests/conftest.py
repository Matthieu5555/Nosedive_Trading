"""Shared fixtures for the frontend BFF test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from algotrading.frontend import runner
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    """An AppContext wired to an empty tmp store and a tmp configs dir."""
    store_root = tmp_path / "data"
    configs_dir = tmp_path / "configs"
    return AppContext(
        store_root=store_root,
        configs_dir=configs_dir,
        store=ParquetStore(store_root),
    )


@pytest.fixture
def infra_client(ctx: AppContext) -> Iterator[TestClient]:
    """TestClient over the infra-wired BFF; JOB_STORE cleared between tests."""
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as client:
        yield client
