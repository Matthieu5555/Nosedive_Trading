from __future__ import annotations

import math
import time
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


def _option_key(seed: ModuleType, strike: float, right: str) -> str:
    return InstrumentKey(
        underlying_symbol=seed.MEMBER_AAA,
        security_type="OPT",
        exchange="SMART",
        currency="USD",
        multiplier=100.0,
        broker_contract_id=f"o-{right}-{strike:g}",
        expiry=seed.EXPIRY,
        strike=strike,
        option_right=right,
    ).canonical()


def _quote_snapshot(
    seed: ModuleType,
    *,
    strike: float,
    right: str,
    bid: float | None,
    ask: float | None,
    volume: float | None,
) -> tables.MarketStateSnapshot:
    return tables.MarketStateSnapshot(
        snapshot_ts=seed.AS_OF,
        instrument_key=_option_key(seed, strike, right),
        reference_spot=seed.AN_FORWARD,
        bid=bid if bid is not None else 0.0,
        ask=ask if ask is not None else 0.0,
        last=(bid if bid is not None else 0.0),
        spread_pct=0.0,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=seed.TRADE_DATE,
        underlying=seed.MEMBER_AAA,
        provenance=seed.prov(f"quote:{right}:{strike:g}"),
        volume=volume,
    )


def _analytics_store_with_quotes(
    root: Path, seed: ModuleType, snapshots: list[tables.MarketStateSnapshot]
) -> AppContext:
    store = ParquetStore(root)
    store.write(
        "projected_option_analytics",
        [
            seed.analytics_cell(
                delta_band="30dp",
                target_delta=seed.AN_PUT_DELTA,
                log_moneyness=seed.AN_PUT_LOGM,
                implied_vol=seed.AN_PUT_IV,
                delta=seed.AN_PUT_DELTA,
                dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
            ),
            seed.analytics_cell(
                delta_band="30dc",
                target_delta=seed.AN_CALL_DELTA,
                log_moneyness=seed.AN_CALL_LOGM,
                implied_vol=seed.AN_CALL_IV,
                delta=seed.AN_CALL_DELTA,
                dollar_delta=seed.AN_CALL_DOLLAR_DELTA,
            ),
        ],
    )
    store.write(
        "surface_parameters",
        [
            seed.surface_parameters_row(
                seed.MEMBER_AAA,
                SurfaceFitDiagnostics(
                    rmse=0.0008, n_points=9, arb_free=True, bound_hits=(), converged=True,
                ),
            )
        ],
    )
    if snapshots:
        store.write("market_state_snapshots", snapshots)
    return AppContext(
        store_root=root,
        configs_dir=root.parent / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )


def _points_by_band(payload: dict) -> dict[str, dict]:
    points = payload["maturities"][0]["points"]
    return {point["delta_band"]: point for point in points}


def test_run_id_selects_a_specific_fetch_of_the_same_trade_date(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Two fetches of one trade date, distinguished by implied vol. With no run_id the read resolves
    # the newest fetch; an explicit run_id pins the analytics read to exactly that fetch's data.
    root = tmp_path / "data"
    store = ParquetStore(root)
    iv_a, iv_b = 0.2000, 0.3500

    def cell(iv: float) -> tables.ProjectedOptionAnalytics:
        return seed.analytics_cell(
            delta_band="30dp",
            target_delta=seed.AN_PUT_DELTA,
            log_moneyness=seed.AN_PUT_LOGM,
            implied_vol=iv,
            delta=seed.AN_PUT_DELTA,
            dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
        )

    store.write("projected_option_analytics", [cell(iv_a)], run_id="fetch-A")
    time.sleep(0.01)  # distinct run-dir mtimes so "newest" is unambiguous
    store.write("projected_option_analytics", [cell(iv_b)], run_id="fetch-B")

    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )

    def implied_vol(params: dict[str, str]) -> float:
        payload = client.get("/api/analytics", params=params).json()
        return payload["maturities"][0]["smile"]["implied_vols"][0]

    base = {"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()}
    with TestClient(create_app(ctx)) as client:
        assert implied_vol(base) == pytest.approx(iv_b)  # newest fetch by default
        assert implied_vol({**base, "run_id": "fetch-A"}) == pytest.approx(iv_a)
        assert implied_vol({**base, "run_id": "fetch-B"}) == pytest.approx(iv_b)


def test_analytics_reads_back_surface_and_dollar_greeks(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert payload["underlying"] == seed.MEMBER_AAA
    assert payload["n_maturities"] == 1
    maturity = payload["maturities"][0]
    assert maturity["maturity_years"] == pytest.approx(0.25)
    assert maturity["smile"]["axis_type"] == "delta"
    assert maturity["smile"]["deltas"] == [
        pytest.approx(seed.AN_PUT_DELTA),
        pytest.approx(seed.AN_CALL_DELTA),
    ]
    assert maturity["smile"]["implied_vols"] == [
        pytest.approx(seed.AN_PUT_IV),
        pytest.approx(seed.AN_CALL_IV),
    ]
    assert maturity["surface_slice"]["svi_b"] == pytest.approx(seed.SVI_B)
    put_point = maturity["points"][0]
    assert put_point["forward_price"] == pytest.approx(seed.AN_FORWARD)
    assert put_point["metrics"]["delta"]["dollar"] == pytest.approx(seed.AN_PUT_DOLLAR_DELTA)


def _forward_curve_point(
    seed: ModuleType, *, implied_rate: float | None, implied_carry: float | None,
    implied_dividend: float | None,
) -> tables.ForwardCurvePoint:
    return tables.ForwardCurvePoint(
        snapshot_ts=seed.AS_OF,
        underlying=seed.MEMBER_AAA,
        maturity_years=0.25,
        expiry_date=seed.EXPIRY,
        day_count="ACT/365",
        forward_price=seed.AN_FORWARD,
        diagnostics=tables.ForwardDiagnostics(
            method="parity", candidate_count=5, residual_mad=0.01, quality_label="good"
        ),
        source_snapshot_ts=seed.AS_OF,
        provenance=seed.prov("forward:AAA"),
        implied_rate=implied_rate,
        implied_carry=implied_carry,
        implied_dividend=implied_dividend,
    )


def _seeded_client_with_forward(
    tmp_path: Path, seed: ModuleType, point: tables.ForwardCurvePoint
) -> AppContext:
    root = tmp_path / "data"
    seed.seed_store(root)
    store = ParquetStore(root)
    store.write("forward_curve", [point])
    return AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )


def test_analytics_payload_surfaces_explicit_rate_carry_dividend_per_tenor(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Eq 5 hand value (independent of any code under test): r=0.04, F=195, S=192,
    # T=0.25 -> carry = ln(F/S)/T, dividend = r - carry. The BFF must pass these through
    # verbatim (no recompute), so the asserted carry/dividend are derived here.
    rate = 0.04
    carry = math.log(195.0 / 192.0) / 0.25
    dividend = rate - carry
    point = _forward_curve_point(
        seed, implied_rate=rate, implied_carry=carry, implied_dividend=dividend
    )
    ctx = _seeded_client_with_forward(tmp_path, seed, point)
    with TestClient(create_app(ctx)) as client:
        maturity = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()["maturities"][0]
    diag = maturity["rate_diagnostics"]
    assert diag is not None
    assert diag["implied_rate"] == pytest.approx(rate)
    assert diag["implied_carry"] == pytest.approx(carry)
    assert diag["implied_dividend"] == pytest.approx(dividend)
    assert diag["forward_price"] == pytest.approx(seed.AN_FORWARD)
    assert diag["rate_unit"] == "/yr (annualized, continuous)"
    assert diag["implied_dividend"] == pytest.approx(diag["implied_rate"] - diag["implied_carry"])


def test_analytics_rate_diagnostics_carry_parity_implied_rate_when_config_rate_is_none(
    tmp_path: Path, seed: ModuleType
) -> None:
    # rate: null path -> the persisted point already carries the parity-implied r (computed by
    # infra, not the BFF). The serializer simply surfaces whatever the contract holds.
    parity_rate = 0.031
    carry = math.log(195.0 / 192.0) / 0.25
    point = _forward_curve_point(
        seed, implied_rate=parity_rate, implied_carry=carry,
        implied_dividend=parity_rate - carry,
    )
    ctx = _seeded_client_with_forward(tmp_path, seed, point)
    with TestClient(create_app(ctx)) as client:
        diag = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()["maturities"][0]["rate_diagnostics"]
    assert diag["implied_rate"] == pytest.approx(parity_rate)


def test_analytics_rate_diagnostics_is_null_when_no_forward_curve_seeded(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    maturity = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]
    assert "rate_diagnostics" in maturity
    assert maturity["rate_diagnostics"] is None


def test_analytics_payload_uses_blueprint_field_names(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    point = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]
    for field in ("forward_price", "implied_vol", "log_moneyness"):
        assert field in point, f"blueprint field {field!r} must be in the analytics payload"
    assert set(point["metrics"]) == {"delta", "gamma", "vega", "rt_vega", "theta", "rho"}
    assert "raw" in point["metrics"]["delta"] and "dollar" in point["metrics"]["delta"]


def test_dollar_greeks_carry_unit_strings(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    metrics = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]["metrics"]
    assert metrics["delta"]["unit"] == seed.AN_DOLLAR_DELTA_UNIT
    assert metrics["gamma"]["unit"] == seed.AN_DOLLAR_GAMMA_UNIT
    assert metrics["vega"]["unit"] == seed.AN_DOLLAR_VEGA_UNIT
    assert metrics["theta"]["unit"] == seed.AN_DOLLAR_THETA_UNIT
    assert metrics["rho"]["unit"] == seed.AN_DOLLAR_RHO_UNIT
    assert metrics["rt_vega"]["raw"] == pytest.approx(seed.AN_RT_VEGA)
    assert metrics["rt_vega"]["dollar"] == pytest.approx(seed.AN_DOLLAR_RT_VEGA)
    assert metrics["rt_vega"]["unit"] == seed.AN_DOLLAR_RT_VEGA_UNIT
    for name in ("delta", "gamma", "vega", "rt_vega", "theta", "rho"):
        assert metrics[name]["unit"], f"{name} must carry a non-empty unit string"


def test_mirror_greeks_serialized_in_analytics_payload(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    point = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]
    assert "price_mirror" in point
    assert point["price_mirror"] is None
    assert "mirror_metrics" in point
    mirror = point["mirror_metrics"]
    assert set(mirror) == {"delta", "theta", "rho"}
    for greek in ("delta", "theta", "rho"):
        assert "raw" in mirror[greek]
        assert "dollar" in mirror[greek]
        assert "unit" in mirror[greek]
        assert mirror[greek]["raw"] is None


def test_analytics_unknown_ticker_is_empty_not_500(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/analytics", params={"underlying": "NOPE"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_maturities"] == 0
    assert payload["maturities"] == []


def test_analytics_bad_trade_date_is_labeled_400(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.get(
        "/api/analytics", params={"underlying": seed.MEMBER_AAA, "trade_date": "nope"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_trade_date", "trade_date": "nope"}


def test_analytics_falls_back_to_surface_grid_when_projection_empty(
    tmp_path: Path, seed: ModuleType
) -> None:
    underlying = "GRIDONLY"
    maturity_years = 0.25
    grid_rows = [(0.10, 0.012), (-0.10, 0.020), (0.00, 0.010)]
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write(
        "surface_grid",
        [
            tables.SurfaceGrid(
                snapshot_ts=seed.AS_OF,
                underlying=underlying,
                maturity_years=maturity_years,
                moneyness_bucket=bucket,
                model_version="svi-readback",
                total_variance=variance,
                source_snapshot_ts=seed.AS_OF,
                provenance=seed.prov(f"grid:{bucket}"),
            )
            for bucket, variance in grid_rows
        ],
    )
    store.write(
        "surface_parameters",
        [
            tables.SurfaceParameters(
                snapshot_ts=seed.AS_OF,
                underlying=underlying,
                maturity_years=maturity_years,
                model_version="svi-readback",
                svi_a=seed.SVI_A,
                svi_b=seed.SVI_B,
                svi_rho=seed.SVI_RHO,
                svi_m=seed.SVI_M,
                svi_sigma=seed.SVI_SIGMA,
                expiry_date=seed.EXPIRY,
                day_count="ACT/365",
                diagnostics=SurfaceFitDiagnostics(rmse=0.0008, n_points=9, arb_free=True),
                source_snapshot_ts=seed.AS_OF,
                provenance=seed.prov("surface:GRIDONLY"),
            )
        ],
    )
    app_ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=underlying,
    )
    with TestClient(create_app(app_ctx)) as client:
        payload = client.get(
            "/api/analytics",
            params={"underlying": underlying, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()

    assert payload["source"] == "surface_grid"
    assert payload["n_maturities"] == 1
    maturity = payload["maturities"][0]
    assert maturity["maturity_years"] == pytest.approx(maturity_years)
    ordered = sorted(grid_rows)
    assert maturity["smile"]["axis_type"] == "moneyness"
    assert "deltas" not in maturity["smile"]
    assert maturity["smile"]["moneyness_buckets"] == [
        pytest.approx(bucket) for bucket, _ in ordered
    ]
    assert maturity["smile"]["log_moneyness"] == [pytest.approx(bucket) for bucket, _ in ordered]
    assert maturity["smile"]["implied_vols"] == [
        pytest.approx(math.sqrt(variance / maturity_years)) for _, variance in ordered
    ]
    assert maturity["surface_slice"]["svi_b"] == pytest.approx(seed.SVI_B)
    assert maturity["points"] == []


def test_analytics_prefers_projection_over_grid_fallback(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert payload["source"] == "projected_option_analytics"
    assert payload["maturities"][0]["points"], "rich per-cell points must be present"


def test_dense_surface_absent_for_a_single_fitted_slice(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert "surface" in payload
    assert payload["surface"] is None


def test_quote_block_always_present_even_without_snapshots(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # Byte-identical-when-absent: the seeded store banks no option snapshots under MEMBER_AAA,
    # so every cell carries a quote block whose bid/ask/volume are null.
    points = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"]
    assert points
    for point in points:
        assert point["quote"] == {"bid": None, "ask": None, "volume": None}


def test_two_sided_quote_threads_onto_the_matching_cell(
    tmp_path: Path, seed: ModuleType
) -> None:
    # The put cell projects to strike AN_FORWARD*(1+AN_PUT_LOGM); the nearest banked put snapshot
    # for the fitted expiry carries the quote the BFF must surface verbatim (no recompute).
    put_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_PUT_LOGM), 2)
    call_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_CALL_LOGM), 2)
    put_bid, put_ask, put_volume = 4.10, 4.40, 1875.0
    call_bid, call_ask, call_volume = 3.05, 3.25, 920.0
    snapshots = [
        _quote_snapshot(
            seed, strike=put_strike + 5.0, right="P", bid=9.9, ask=10.1, volume=1.0
        ),
        _quote_snapshot(
            seed, strike=put_strike, right="P", bid=put_bid, ask=put_ask, volume=put_volume
        ),
        _quote_snapshot(
            seed, strike=call_strike, right="C", bid=call_bid, ask=call_ask, volume=call_volume
        ),
    ]
    app_ctx = _analytics_store_with_quotes(tmp_path / "data", seed, snapshots)
    with TestClient(create_app(app_ctx)) as client:
        payload = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()
    by_band = _points_by_band(payload)
    assert by_band["30dp"]["quote"]["bid"] == pytest.approx(put_bid)
    assert by_band["30dp"]["quote"]["ask"] == pytest.approx(put_ask)
    assert by_band["30dp"]["quote"]["volume"] == pytest.approx(put_volume)
    assert by_band["30dc"]["quote"]["bid"] == pytest.approx(call_bid)
    assert by_band["30dc"]["quote"]["ask"] == pytest.approx(call_ask)
    assert by_band["30dc"]["quote"]["volume"] == pytest.approx(call_volume)


def test_one_sided_or_unmatched_quote_omits_cleanly(
    tmp_path: Path, seed: ModuleType
) -> None:
    # A put snapshot with no ask and null volume threads its bid through with ask/volume null; the
    # call cell has no banked snapshot of its right, so its quote block stays fully null.
    put_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_PUT_LOGM), 2)
    snapshots = [
        _quote_snapshot(
            seed, strike=put_strike, right="P", bid=4.10, ask=None, volume=None
        ),
    ]
    app_ctx = _analytics_store_with_quotes(tmp_path / "data", seed, snapshots)
    with TestClient(create_app(app_ctx)) as client:
        payload = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()
    by_band = _points_by_band(payload)
    assert by_band["30dp"]["quote"]["bid"] == pytest.approx(4.10)
    assert by_band["30dp"]["quote"]["ask"] == pytest.approx(0.0)
    assert by_band["30dp"]["quote"]["volume"] is None
    assert by_band["30dc"]["quote"] == {"bid": None, "ask": None, "volume": None}
