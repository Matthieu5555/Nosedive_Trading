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
    surface = seeded_client.get("/api/risk/scenarios").json()["surface"]
    assert surface["n_cells"] == 0
    assert surface["spot_shock"] == [] and surface["scenario_pnl"] == []
    assert surface["unit"]


def test_no_named_scenarios_is_a_labelled_empty_list(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/risk/scenarios").json()
    assert payload["named"] == []
    assert payload["n_named"] == 0


def test_named_scenarios_bucket_legs_per_scenario(
    named_client: TestClient, seed: ModuleType
) -> None:
    payload = named_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()
    assert payload["n_named"] == 2
    by_id = {item["scenario_id"]: item for item in payload["named"]}
    assert set(by_id) == {"named_2008", "named_covid-2020"}

    crisis = by_id["named_2008"]
    assert crisis["label"] == "2008"
    assert crisis["n_legs"] == 2
    assert crisis["scenario_pnl"] == pytest.approx(seed.NAMED_2008_PNL)
    assert crisis["spot_shock"] == pytest.approx(seed.NAMED_2008_SPOT)
    assert crisis["vol_shock"] == pytest.approx(seed.NAMED_2008_VOL)
    assert crisis["rate_shock"] == pytest.approx(seed.NAMED_2008_RATE)
    assert crisis["unit"]

    covid = by_id["named_covid-2020"]
    assert covid["label"] == "covid-2020"
    assert covid["scenario_pnl"] == pytest.approx(seed.NAMED_COVID_PNL)


def test_named_scenarios_sorted_by_id(
    named_client: TestClient, seed: ModuleType
) -> None:
    named = named_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["named"]
    ids = [item["scenario_id"] for item in named]
    assert ids == sorted(ids)


def test_stress_surface_reads_back_basket_cells(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    assert surface["spot_shock"] == pytest.approx(seed.SURFACE_SPOT_AXIS)
    assert surface["vol_shock"] == pytest.approx(seed.SURFACE_VOL_AXIS)
    for i, spot_shock in enumerate(seed.SURFACE_SPOT_AXIS):
        for j, vol_shock in enumerate(seed.SURFACE_VOL_AXIS):
            assert surface["scenario_pnl"][i][j] == pytest.approx(
                seed.SURFACE_TOTALS[(spot_shock, vol_shock)]
            )
    assert surface["scenario_version"] == seed.SURFACE_VERSION
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
    from algotrading.frontend.serializers import scenario_surface_to_dict

    rows = [
        seed.surface_cell(s, v, seed.SURFACE_TOTALS[(s, v)], seed.CALL_100.canonical())
        for s in seed.SURFACE_SPOT_AXIS
        for v in seed.SURFACE_VOL_AXIS
        if (s, v) != (-0.5, 0.5)
    ]
    surface = scenario_surface_to_dict(rows)
    i, j = seed.SURFACE_SPOT_AXIS.index(-0.5), seed.SURFACE_VOL_AXIS.index(0.5)
    assert surface["scenario_pnl"][i][j] is None
    assert surface["has_holes"] is True
    assert surface["n_holes"] == 1
    assert surface["scenario_pnl"][seed.SURFACE_SPOT_AXIS.index(0.5)][
        seed.SURFACE_VOL_AXIS.index(0.0)
    ] == pytest.approx(4000.0)


def test_surface_payload_uses_blueprint_field_names(
    surface_client: TestClient, seed: ModuleType
) -> None:
    surface = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()["surface"]
    assert {"spot_shock", "vol_shock", "scenario_pnl"}.issubset(surface)
    assert "pnl" not in surface and "z" not in surface


def test_cells_list_is_intact_for_2c_alongside_the_surface(
    surface_client: TestClient, seed: ModuleType
) -> None:
    payload = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()
    scenario_ids = {cell["scenario_id"] for cell in payload["cells"]}
    assert "spot_-0.0500" in scenario_ids
    assert any(sid.startswith("surf_") for sid in scenario_ids)
    assert payload["n_cells"] == 11


def test_empty_basket_is_a_labelled_empty_surface_not_500(surface_client: TestClient) -> None:
    response = surface_client.get("/api/risk/scenarios", params={"portfolio_id": "nope"})
    assert response.status_code == 200
    surface = response.json()["surface"]
    assert surface["spot_shock"] == [] and surface["vol_shock"] == []
    assert surface["scenario_pnl"] == [] and surface["n_cells"] == 0
    assert surface["unit"]


def test_metrics_carry_a_unit_string_and_the_raw_value_beside_each_dollar(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/risk/metrics", params={"underlying": seed.UNDERLYING}
    ).json()
    assert payload["n_results"] == 1
    metrics = payload["results"][0]["metrics"]
    assert metrics["gamma"]["unit"] == "$ per 1% move"
    assert metrics["theta"]["unit"] == "$ per calendar day"
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
    expected = (
        seed.GMMA_RAW_GAMMA * seed.GMMA_SPOT * seed.GMMA_SPOT * seed.GMMA_MULT * seed.GMMA_QTY
        / 100.0
    )
    assert expected == pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED)

    payload = seeded_client.get(
        "/api/risk/metrics", params={"underlying": seed.GAMMA_UNDERLYING}
    ).json()
    assert payload["n_results"] == 1
    gamma = payload["results"][0]["metrics"]["gamma"]
    assert gamma["dollar"] == pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED)
    assert gamma["unit"] == "$ per 1% move"
    assert gamma["raw"] == pytest.approx(seed.GMMA_RAW_GAMMA)
    assert gamma["dollar"] != pytest.approx(seed.GMMA_DOLLAR_GAMMA_ONE_DOLLAR)


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
