from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_EXPECTED_BACKLOG = ["universe_refresh", "collection", "analytics", "reconciliation", "qc"]

_QC_TRADE_DATE = date(2026, 5, 30)
_QC_RUN_TS = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)


def _qc_result(qc_status: str) -> tables.QcResult:
    return tables.QcResult(
        run_id="run-health-test",
        check_name="spread_health",
        target_key="AAPL",
        run_ts=_QC_RUN_TS,
        qc_status=qc_status,
        severity="warning",
        measured_value=0.002,
        threshold_version="qc-v1",
        context="{}",
    )


@pytest.fixture
def qc_seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("qc_results", [_qc_result("pass")])
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=store,
    )
    with TestClient(create_app(ctx)) as client:
        yield client


@pytest.fixture
def qc_failing_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("qc_results", [_qc_result("fail")])
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=store,
    )
    with TestClient(create_app(ctx)) as client:
        yield client


def test_health_reflects_surfaces_and_scenarios_after_persist(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/health", params={"trade_date": seed.TRADE_DATE.isoformat()}
    ).json()
    assert payload["trade_date"] == seed.TRADE_DATE.isoformat()
    assert payload["data_flowing"] == "ok"
    assert payload["surfaces_building"] == "ok"
    assert payload["scenarios_current"] == "current"


def test_health_reports_unhealthy_no_data_on_empty_store(infra_client: TestClient) -> None:
    response = infra_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_healthy"] is False
    assert body["data_flowing"] == "no_data"
    assert "trade_date" in body
    assert body["backlog"] == _EXPECTED_BACKLOG
    assert "note" not in body


def test_health_bad_trade_date_returns_typed_400(infra_client: TestClient) -> None:
    response = infra_client.get("/api/health", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_trade_date"
    assert body["trade_date"] == "not-a-date"


def test_health_with_passing_qc_result_does_not_raise(qc_seeded_client: TestClient) -> None:
    response = qc_seeded_client.get(
        "/api/health", params={"trade_date": _QC_TRADE_DATE.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade_date"] == _QC_TRADE_DATE.isoformat()
    assert body.get("qc_status") != "failing"


def test_health_with_failing_qc_result_reports_failing(qc_failing_client: TestClient) -> None:
    response = qc_failing_client.get(
        "/api/health", params={"trade_date": _QC_TRADE_DATE.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade_date"] == _QC_TRADE_DATE.isoformat()
    assert body.get("qc_status") == "failing"
