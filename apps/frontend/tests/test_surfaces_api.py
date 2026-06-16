from __future__ import annotations

from types import ModuleType

import pytest
from fastapi.testclient import TestClient


def test_surfaces_router_reads_back_persisted_svi_slice(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/surfaces", params={"underlying": seed.UNDERLYING}).json()
    assert payload["underlying"] == seed.UNDERLYING
    assert payload["n_slices"] == 1
    slice_row = payload["slices"][0]
    assert slice_row["maturity_years"] == pytest.approx(seed.MATURITY_YEARS)
    assert slice_row["svi_b"] == pytest.approx(seed.SVI_B)
    assert slice_row["svi_sigma"] == pytest.approx(seed.SVI_SIGMA)
    assert slice_row["diagnostics"]["arb_free"] is False
    assert slice_row["diagnostics"]["bound_hits"] == ["rho_lower"]
    assert slice_row["diagnostics"]["converged"] is False
    assert slice_row["degenerate"] is True
    assert slice_row["degenerate_reasons"] == [
        "param_at_bound:rho_lower", "not_converged", "butterfly_arbitrage",
    ]
    assert slice_row["provenance"]["code_version"] == "readback-test"
    assert slice_row["provenance"]["stamp_hash"]


def test_surfaces_router_does_not_flag_a_clean_slice(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/surfaces", params={"underlying": seed.MEMBER_AAA}).json()
    slice_row = payload["slices"][0]
    assert slice_row["diagnostics"]["bound_hits"] == []
    assert slice_row["diagnostics"]["converged"] is True
    assert slice_row["degenerate"] is False
    assert slice_row["degenerate_reasons"] == []


def test_surfaces_underlyings_lists_the_persisted_underlying(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/surfaces/underlyings").json()
    assert payload["underlyings"] == [seed.MEMBER_AAA, seed.UNDERLYING]


def test_surfaces_empty_for_unknown_underlying(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces", params={"underlying": "ZZZZ"}).json()
    assert payload["n_slices"] == 0
    assert payload["slices"] == []


def test_surfaces_empty_for_no_underlying(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces").json()
    assert payload["n_slices"] == 0
    assert payload["slices"] == []
    assert "underlying" in payload


def test_surfaces_underlyings_empty_on_empty_store(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/surfaces/underlyings").json()
    assert payload["underlyings"] == []


def test_surfaces_bad_trade_date_returns_400(infra_client: TestClient) -> None:
    response = infra_client.get("/api/surfaces", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_trade_date"
