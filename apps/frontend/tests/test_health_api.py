"""Health router tests: the real operator dashboard over the store + run-state ledger.

The router builds a ``DashboardStatus`` from ``orchestration.build_dashboard`` over the
store's partitions and the run-state ledger. Against an empty tmp store the day is
legitimately unhealthy (no data flowing, every EOD stage in backlog), and a malformed
``trade_date`` is a typed 400. Expected values come from the dashboard contract
(``infra/orchestration/dashboard.py`` + ``run_state.EOD_STAGES``), not the router output.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# The five EOD stages, in order — the dashboard's backlog on a day where nothing ran.
# Source: algotrading.infra.orchestration.run_state.EOD_STAGES (the contract, not the BFF).
_EXPECTED_BACKLOG = ["universe_refresh", "collection", "analytics", "reconciliation", "qc"]


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
