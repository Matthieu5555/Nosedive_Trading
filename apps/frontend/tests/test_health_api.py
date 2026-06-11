"""Health router tests: the real operator dashboard over the store + run-state ledger.

The router builds a ``DashboardStatus`` from ``orchestration.build_dashboard`` over the
store's partitions and the run-state ledger. Against an empty tmp store the day is
legitimately unhealthy (no data flowing, every EOD stage in backlog), and a malformed
``trade_date`` is a typed 400. Expected values come from the dashboard contract
(``infra/orchestration/dashboard.py`` + ``run_state.EOD_STAGES``), not the router output.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.frontend import runner
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

# The five EOD stages, in order — the dashboard's backlog on a day where nothing ran.
# Source: algotrading.infra.orchestration.run_state.EOD_STAGES (the contract, not the BFF).
_EXPECTED_BACKLOG = ["universe_refresh", "collection", "analytics", "reconciliation", "qc"]

_QC_TRADE_DATE = date(2026, 5, 30)
_QC_RUN_TS = datetime(2026, 5, 30, 16, 0, tzinfo=UTC)


def _qc_result(qc_status: str) -> tables.QcResult:
    """Build a minimal QcResult row with the given qc_status."""
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
    """TestClient wired to a store pre-seeded with a passing QcResult row."""
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("qc_results", [_qc_result("pass")])
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=store,
    )
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as client:
        yield client


@pytest.fixture
def qc_failing_client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient wired to a store pre-seeded with a failing QcResult row."""
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("qc_results", [_qc_result("fail")])
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=store,
    )
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as client:
        yield client


def test_health_reports_unhealthy_no_data_on_empty_store(infra_client: TestClient) -> None:
    response = infra_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_healthy"] is False
    assert body["data_flowing"] == "no_data"
    assert "trade_date" in body
    # Nothing has run, so every EOD stage is still in the backlog, and the live path
    # carries no stub "note" (that field only ever appeared on the removed fallback).
    assert body["backlog"] == _EXPECTED_BACKLOG
    assert "note" not in body


def test_health_bad_trade_date_returns_typed_400(infra_client: TestClient) -> None:
    response = infra_client.get("/api/health", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_trade_date"
    assert body["trade_date"] == "not-a-date"


def test_health_with_passing_qc_result_does_not_raise(qc_seeded_client: TestClient) -> None:
    # Regression guard for F-BFF-02: _qc_status_for used to read ``row.status`` but the
    # QcResult contract field is ``qc_status`` (ADR 0029). That caused an AttributeError on
    # any trade_date that has stored qc_results rows. The fix reads ``row.qc_status``.
    # This test writes a real QcResult row and asserts the route returns HTTP 200 with no
    # exception — the pre-fix code would raise AttributeError inside the route handler.
    response = qc_seeded_client.get(
        "/api/health", params={"trade_date": _QC_TRADE_DATE.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade_date"] == _QC_TRADE_DATE.isoformat()
    # A PASS qc_status must not be collapsed to QC_FAILING ("failing") in the dashboard.
    assert body.get("qc_status") != "failing"


def test_health_with_failing_qc_result_reports_failing(qc_failing_client: TestClient) -> None:
    # A stored ``qc_status="fail"`` row must be reflected as QC failing in the dashboard.
    # Also exercises the fixed ``row.qc_status`` read path with a failure status.
    response = qc_failing_client.get(
        "/api/health", params={"trade_date": _QC_TRADE_DATE.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade_date"] == _QC_TRADE_DATE.isoformat()
    assert body.get("qc_status") == "failing"
