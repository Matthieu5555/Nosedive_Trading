"""Risk router tests: real store-backed aggregates + scenario PnL.

The real risk router reads ``risk_aggregates`` and ``scenario_results`` from the
ParquetStore. An empty store returns well-formed empty payloads (never a 500).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_risk_empty_aggregates_are_well_formed(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/risk").json()
    assert payload["n_aggregates"] == 0
    assert payload["aggregates"] == []
    assert payload["portfolio_id"] is None


def test_risk_empty_scenarios_are_well_formed(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/risk/scenarios").json()
    assert payload["n_cells"] == 0
    assert payload["cells"] == []


def test_risk_empty_portfolio_list(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/risk/portfolios").json()
    assert payload["portfolios"] == []


def test_risk_portfolio_filter_on_empty_store(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/risk", params={"portfolio_id": "unknown"}).json()
    assert payload["n_aggregates"] == 0
    assert payload["portfolio_id"] == "unknown"
