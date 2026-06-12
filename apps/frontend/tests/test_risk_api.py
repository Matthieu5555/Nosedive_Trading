"""Risk router tests: store-backed aggregates, metrics, and the WS 2B scenario surface.

The seeded cases persist real ``risk_aggregates`` / ``pricing_results`` /
``scenario_results`` rows through ``ParquetStore.write`` (the conftest seed) and assert
the router reads *those* hand-chosen values back unchanged. An empty store returns
well-formed empty payloads (never a 500).
"""

from __future__ import annotations

from types import ModuleType

import pytest
from fastapi.testclient import TestClient


def test_risk_router_reads_back_persisted_aggregate(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/risk").json()
    assert payload["n_aggregates"] == 1
    agg = payload["aggregates"][0]
    assert agg["portfolio_id"] == seed.PORTFOLIO_ID
    assert agg["group_key"] == seed.UNDERLYING
    assert agg["net_delta"] == pytest.approx(seed.NET_DELTA)
    assert agg["net_vega"] == pytest.approx(seed.NET_VEGA)
    assert agg["provenance"]["config_hashes"] == {"cfg": "cfg-readback"}


def test_risk_portfolios_lists_the_persisted_portfolio(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/risk/portfolios").json()
    assert payload["portfolios"] == [seed.PORTFOLIO_ID]


def test_risk_scenarios_read_back_persisted_cell(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get("/api/risk/scenarios").json()
    assert payload["n_cells"] == 1
    cell = payload["cells"][0]
    assert cell["scenario_id"] == "spot-down-10"
    assert cell["spot_shock"] == pytest.approx(-0.10)
    assert cell["scenario_pnl"] == pytest.approx(seed.SCENARIO_PNL)


def test_risk_portfolio_filter_selects_the_seeded_portfolio(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    hit = seeded_client.get("/api/risk", params={"portfolio_id": seed.PORTFOLIO_ID}).json()
    assert hit["n_aggregates"] == 1
    miss = seeded_client.get("/api/risk", params={"portfolio_id": "nope"}).json()
    assert miss["n_aggregates"] == 0


def test_families_only_store_has_a_labelled_empty_surface(seeded_client: TestClient) -> None:
    # The default seed holds only a families cell ("spot-down-10"), no surf_ cells: the cells
    # list is unchanged (2C) and the additive surface is empty-but-labelled (not absent, not 500).
    surface = seeded_client.get("/api/risk/scenarios").json()["surface"]
    assert surface["n_cells"] == 0
    assert surface["spot_shock"] == [] and surface["scenario_pnl"] == []
    assert surface["unit"]  # still carries its PnL unit label


# --- WS 2B: the (spot × vol) stress surface reshaped over scenario_results ----
# surface_client (conftest) seeds a 3×3 cartesian surface persisted as per-contract
# scenario_results cells, plus one families cell, all under one portfolio. The independent
# oracle is "what we wrote in": SURFACE_TOTALS, the portfolio total per (spot, vol) cell.


def test_stress_surface_reads_back_basket_cells(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    assert surface["spot_shock"] == pytest.approx(seed.SURFACE_SPOT_AXIS)
    assert surface["vol_shock"] == pytest.approx(seed.SURFACE_VOL_AXIS)
    # The z-grid is spot-major, summed per cell, and equals the independent oracle.
    for i, spot_shock in enumerate(seed.SURFACE_SPOT_AXIS):
        for j, vol_shock in enumerate(seed.SURFACE_VOL_AXIS):
            assert surface["scenario_pnl"][i][j] == pytest.approx(
                seed.SURFACE_TOTALS[(spot_shock, vol_shock)]
            )
    assert surface["scenario_version"] == seed.SURFACE_VERSION
    # 8 single-contract cells + 2 contracts on the centre cell = 10 surface cells.
    assert surface["n_cells"] == 10


def test_surface_centre_cell_sums_contracts_to_zero(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    ci = seed.SURFACE_SPOT_AXIS.index(0.0)
    cj = seed.SURFACE_VOL_AXIS.index(0.0)
    assert surface["scenario_pnl"][ci][cj] == pytest.approx(0.0)


def test_full_surface_carries_no_holes_flag(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    assert surface["has_holes"] is False
    assert surface["n_holes"] == 0


def test_missing_surface_cell_is_a_labeled_hole_not_a_zero(seed: ModuleType) -> None:
    # F-BFF-03: a non-rectangular cell set (one (spot, vol) combination genuinely absent)
    # must serialize the hole as None + has_holes, never a silent 0.0 — a zero PnL is a
    # real quote ("this stress costs nothing"), which an absent cell is not.
    from algotrading.frontend.serializers import scenario_surface_to_dict

    rows = [
        seed.surface_cell(s, v, seed.SURFACE_TOTALS[(s, v)], seed.CALL_100.canonical())
        for s in seed.SURFACE_SPOT_AXIS
        for v in seed.SURFACE_VOL_AXIS
        if (s, v) != (-0.5, 0.5)  # the absent cell
    ]
    surface = scenario_surface_to_dict(rows)
    i, j = seed.SURFACE_SPOT_AXIS.index(-0.5), seed.SURFACE_VOL_AXIS.index(0.5)
    assert surface["scenario_pnl"][i][j] is None
    assert surface["has_holes"] is True
    assert surface["n_holes"] == 1
    # No 0.0 masquerades as the missing quote; the real cells are untouched.
    assert surface["scenario_pnl"][seed.SURFACE_SPOT_AXIS.index(0.5)][
        seed.SURFACE_VOL_AXIS.index(0.0)
    ] == pytest.approx(4000.0)


def test_surface_payload_uses_blueprint_field_names(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    # ADR 0029 names — the axes are spot_shock/vol_shock, the z-grid is scenario_pnl.
    assert {"spot_shock", "vol_shock", "scenario_pnl"}.issubset(surface)
    assert "pnl" not in surface and "z" not in surface  # never the invented names


def test_cells_list_is_intact_for_2c_alongside_the_surface(
    surface_client: TestClient, seed: ModuleType
) -> None:
    payload = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()
    scenario_ids = {cell["scenario_id"] for cell in payload["cells"]}
    assert "spot_-0.0500" in scenario_ids  # the families cell (2C's read) survives
    assert any(sid.startswith("surf_") for sid in scenario_ids)  # surface cells are cells too
    assert payload["n_cells"] == 11  # 10 surface + 1 families, per-contract


def test_empty_basket_is_a_labelled_empty_surface_not_500(surface_client: TestClient) -> None:
    response = surface_client.get("/api/risk/scenarios", params={"portfolio_id": "nope"})
    assert response.status_code == 200
    surface = response.json()["surface"]
    assert surface["spot_shock"] == [] and surface["vol_shock"] == []
    assert surface["scenario_pnl"] == [] and surface["n_cells"] == 0
    assert surface["unit"]  # still labelled


def test_metrics_carry_a_unit_string_and_the_raw_value_beside_each_dollar(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # The BFF metric contract (P0.2 / OQ-1, ADR 0036): every dollar metric the front reads
    # back carries a non-empty unit string of the pinned convention and the raw per-unit
    # Greek beside it — never a bare float. This is the BFF<->infra drift guard.
    payload = seeded_client.get(
        "/api/risk/metrics", params={"underlying": seed.UNDERLYING}
    ).json()
    assert payload["n_results"] == 1
    metrics = payload["results"][0]["metrics"]
    # Gamma quoted per 1% move; theta per calendar day (the pinned defaults).
    assert metrics["gamma"]["unit"] == "$ per 1% move"
    assert metrics["theta"]["unit"] == "$ per calendar day"
    # The stored dollar_gamma is already in one_pct units (ADR 0036: Γ·S²/100 per 1% move);
    # the BFF passes it through unchanged under the matching label — no rescaling at the seam.
    # Every dollar metric has a non-empty unit string and the raw per-unit value beside it.
    for name, raw, dollar in [
        ("delta", 0.55, seed.PR_DOLLAR_DELTA),
        ("gamma", 0.02, seed.PR_DOLLAR_GAMMA),
        ("vega", 0.10, seed.PR_DOLLAR_VEGA),
        ("theta", -0.01, seed.PR_DOLLAR_THETA),
        ("rho", 0.03, seed.PR_DOLLAR_RHO),
    ]:
        metric = metrics[name]
        assert metric["unit"], f"{name} must carry a non-empty unit string"
        assert metric["raw"] == pytest.approx(raw)
        assert metric["dollar"] == pytest.approx(dollar)


def test_metrics_dollar_gamma_value_matches_its_one_pct_label(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # The adversarial value-vs-label seam (audit M5). The pricing engine stores dollar_gamma in
    # the one_pct convention (ADR 0036: Γ·S²/100 per 1% move), and /api/risk/metrics labels it
    # "$ per 1% move". The BFF must pass the stored value through unchanged — no second /100.
    # The expected value is hand-derived from round inputs to rule out a trivially-passing fixture:
    #
    #   gamma = 0.04, spot = 200, mult = 100, qty = 1
    #   per-1%  dollar_gamma (stored & served) = 0.04 * 200**2 * 100 * 1 / 100 = 1600.0
    #
    # A serializer that divides by 100 again would return 16.0 and fail this assertion.
    # A serializer that passes the per-$1 number (160000.0) through also fails the != guard.
    expected = (
        seed.GMMA_RAW_GAMMA * seed.GMMA_SPOT * seed.GMMA_SPOT * seed.GMMA_MULT * seed.GMMA_QTY
        / 100.0
    )
    assert expected == pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED)  # 1600.0, paper-derived

    payload = seeded_client.get(
        "/api/risk/metrics", params={"underlying": seed.GAMMA_UNDERLYING}
    ).json()
    assert payload["n_results"] == 1
    gamma = payload["results"][0]["metrics"]["gamma"]
    # Value and label agree on the one_pct convention: the served number is the per-1% value...
    assert gamma["dollar"] == pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED)
    # ...and its label truthfully describes that convention.
    assert gamma["unit"] == "$ per 1% move"
    # The raw per-unit Greek is untouched (the dollar layer is a separate field, not derived here).
    assert gamma["raw"] == pytest.approx(seed.GMMA_RAW_GAMMA)
    # Guard against a self-consistent-but-wrong serializer that passes the per-$1 number through:
    assert gamma["dollar"] != pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_DOLLAR)


# --- empty-store cases ------------------------------------------------------------------


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
