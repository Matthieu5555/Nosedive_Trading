from __future__ import annotations

import math
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
    assert set(point["metrics"]) == {
        "delta", "gamma", "vega", "rt_vega", "theta", "rho", "vanna", "volga", "charm"
    }
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
    # The per-side block is always present (additive contract), and empty when nothing was captured.
    assert set(payload["sides"]) == {"put", "call", "combined"}
    assert payload["sides"]["combined"] == []
    assert payload["sides_available"] == []
    assert payload["surfaces_by_side"]["call"] is None


def _per_side_store(root: Path, seed: ModuleType) -> AppContext:
    """Two maturities x three sides, with call and put carrying genuinely different IV per band.

    The captured SX5E store really holds distinct call/put/combined cells (the two wings have
    different skew); this mirrors that shape so the per-side payload and dense grids are exercised
    against real per-side data, never a re-slice of one combined set. Each maturity carries five
    distinct log-moneyness points (>= MIN_POINTS_FOR_SVI) so the unified request-time SVI refit can
    fit every slice.
    """
    store = ParquetStore(root)
    bands = [
        ("10dp", -0.10, -0.18),
        ("30dp", -0.30, -0.09),
        ("atm", 0.50, 0.0),
        ("30dc", 0.30, 0.09),
        ("10dc", 0.10, 0.18),
    ]
    side_iv = {"put": 0.31, "call": 0.21, "combined": 0.26}
    cells = []
    for maturity, tenor in ((0.25, "3m"), (0.75, "9m")):
        for side, base_iv in side_iv.items():
            for band, target, logm in bands:
                cells.append(
                    seed.analytics_cell(
                        delta_band=band,
                        target_delta=target,
                        log_moneyness=logm,
                        # IV walks with the wing so call != put at the same band (real skew shape).
                        implied_vol=base_iv + 0.05 * logm,
                        delta=target,
                        dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
                        surface_side=side,
                        maturity_years=maturity,
                        tenor_label=tenor,
                    )
                )
    store.write("projected_option_analytics", cells)
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
    return AppContext(
        store_root=root,
        configs_dir=root.parent / "configs",
        store=ParquetStore(root),
        default_underlying=seed.MEMBER_AAA,
    )


def test_analytics_serializes_per_side_maturities_and_dense_grids(
    tmp_path: Path, seed: ModuleType
) -> None:
    ctx = _per_side_store(tmp_path / "data", seed)
    with TestClient(create_app(ctx)) as client:
        payload = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()

    # All three sides are present and populated, with the combined view byte-identical to the
    # backward-compatible top-level `maturities`.
    assert sorted(payload["sides_available"]) == ["call", "combined", "put"]
    assert payload["sides"]["combined"] == payload["maturities"]
    for side in ("put", "call", "combined"):
        assert len(payload["sides"][side]) == 2  # two maturities per side

    # Call and put carry genuinely different IV at the same band/maturity (the skew asymmetry).
    call_front = payload["sides"]["call"][0]["smile"]["implied_vols"]
    put_front = payload["sides"]["put"][0]["smile"]["implied_vols"]
    assert call_front != put_front

    # Each side gets its own dense 3D grid from the SAME unified clamped-SVI reconstruction (the
    # default n_maturities x n_moneyness dense grid), shaped maturities x log-moneyness.
    grid_shapes = set()
    for side in ("put", "call", "combined"):
        dense = payload["surfaces_by_side"][side]
        assert dense is not None
        n_mat = len(dense["maturity_years"])
        n_k = len(dense["log_moneyness"])
        assert n_mat >= 2
        assert n_k >= 2
        assert len(dense["implied_vol"]) == n_mat
        assert all(len(row) == n_k for row in dense["implied_vol"])
        grid_shapes.add((n_mat, n_k))

        # No served dense IV cell exceeds a sane bound: the clamp NaN-holes the wings instead of
        # extrapolating them into 38-242% IV, so every FINITE cell is a real, in-window level.
        for row in dense["implied_vol"]:
            for value in row:
                if value is None or not math.isfinite(value):
                    continue
                assert 0.0 <= value <= 0.60, f"dense IV cell {value} exceeds the 0.60 bound"

    # Combined / call / put all share the SAME unified grid shape (one method, one reconstruction).
    assert len(grid_shapes) == 1

    # The top-level `surface` is the combined per-side dense, byte-identical (one method, one source).
    assert payload["surface"] == payload["surfaces_by_side"]["combined"]
    assert payload["surface"] is not None


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


def test_coverage_block_counts_two_sided_against_the_captured_chain(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Hand-counted oracle (MAT-LEGIBILITY-coverage-headline): 5 option snapshots — 3 two-sided
    # (both bid>0 and ask>0), 2 one-sided (ask-only, bid coerced to 0 → not two-sided). So
    # option_rows=5, two_sided=3, excluded=2, two_sided_fraction=3/5=0.6. The block is computed
    # once in the BFF (grounding.coverage_from_snapshots) and shared with the assistant frame.
    put_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_PUT_LOGM), 2)
    call_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_CALL_LOGM), 2)
    snapshots = [
        _quote_snapshot(seed, strike=put_strike, right="P", bid=4.1, ask=4.4, volume=10.0),
        _quote_snapshot(seed, strike=call_strike, right="C", bid=3.0, ask=3.2, volume=10.0),
        _quote_snapshot(seed, strike=call_strike + 50.0, right="C", bid=1.0, ask=1.2, volume=5.0),
        _quote_snapshot(seed, strike=call_strike + 100.0, right="C", bid=None, ask=0.6, volume=1.0),
        _quote_snapshot(seed, strike=put_strike - 50.0, right="P", bid=None, ask=2.0, volume=1.0),
    ]
    app_ctx = _analytics_store_with_quotes(tmp_path / "data", seed, snapshots)
    with TestClient(create_app(app_ctx)) as client:
        coverage = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()["coverage"]
    assert coverage == {
        "option_rows": 5,
        "two_sided": 3,
        "excluded": 2,
        "two_sided_fraction": pytest.approx(0.6),
    }


def test_coverage_block_is_null_when_no_option_snapshots(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # Additive-nullable: the seeded store banks no option snapshots under MEMBER_AAA, so the
    # coverage block is null (the headline degrades to "couverture indisponible"), not a 500.
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert payload["coverage"] is None


def test_close_instant_is_null_for_an_index_outside_the_registry(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # MEMBER_AAA is a synthetic test index with no registry entry, so the close instant cannot be
    # resolved — the field is present and null (the front degrades to a date-only as-of), never a
    # guessed instant and never a 500.
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert payload["close_instant"] is None


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


def test_off_grid_projected_strike_does_not_bind_a_far_listed_quote(
    tmp_path: Path, seed: ModuleType
) -> None:
    put_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_PUT_LOGM), 2)
    far_put = round(put_strike * (1.0 + 0.05), 2)
    near_put = round(put_strike * (1.0 + 0.001), 2)
    snapshots = [
        _quote_snapshot(seed, strike=far_put, right="P", bid=9.9, ask=10.1, volume=5.0),
    ]
    far_ctx = _analytics_store_with_quotes(tmp_path / "far", seed, snapshots)
    with TestClient(create_app(far_ctx)) as client:
        far_payload = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()
    far_band = _points_by_band(far_payload)
    assert far_band["30dp"]["quote"] == {"bid": None, "ask": None, "volume": None}, (
        "a listed strike 5% off the projected delta-band strike must not be presented as its quote"
    )

    near_ctx = _analytics_store_with_quotes(
        tmp_path / "near",
        seed,
        [_quote_snapshot(seed, strike=near_put, right="P", bid=4.1, ask=4.4, volume=12.0)],
    )
    with TestClient(create_app(near_ctx)) as client:
        near_payload = client.get(
            "/api/analytics",
            params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
        ).json()
    near_band = _points_by_band(near_payload)
    assert near_band["30dp"]["quote"]["bid"] == pytest.approx(4.1), (
        "a listed strike within 0.1% of the projected strike is the legitimate match"
    )


def test_smile_axis_dedups_the_atm_put_pillar_but_keeps_it_in_points(
    seed: ModuleType,
) -> None:
    from algotrading.frontend.routers.analytics import _group_by_maturity

    cells = [
        seed.analytics_cell(
            delta_band="02dp", target_delta=-0.02, log_moneyness=-0.04,
            implied_vol=0.22, delta=-0.02, dollar_delta=-1.0,
        ),
        seed.analytics_cell(
            delta_band="atm", target_delta=0.0, log_moneyness=0.0,
            implied_vol=0.20, delta=0.50, dollar_delta=10.0,
        ),
        seed.analytics_cell(
            delta_band="atmp", target_delta=0.0, log_moneyness=0.0,
            implied_vol=0.20, delta=-0.50, dollar_delta=-10.0,
        ),
        seed.analytics_cell(
            delta_band="02dc", target_delta=0.02, log_moneyness=0.04,
            implied_vol=0.21, delta=0.02, dollar_delta=1.0,
        ),
    ]
    entry = _group_by_maturity(cells, [], [], [])[0]
    assert entry["smile"]["deltas"] == [
        pytest.approx(-0.02), pytest.approx(0.0), pytest.approx(0.02)
    ]
    assert entry["smile"]["implied_vols"] == [
        pytest.approx(0.22), pytest.approx(0.20), pytest.approx(0.21)
    ]
    assert len(entry["points"]) == 4
    assert {p["delta_band"] for p in entry["points"]} == {"02dp", "atm", "atmp", "02dc"}


def test_off_grid_reading_tenor_still_binds_its_slice_forward_and_quote(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Regression for the blank bid / ask / spread / volume columns the PM saw on the Price structure
    # table. The projected analytics live on a fixed READING-TENOR grid (10d / 1m / 3m ...), while the
    # fitted slices, forwards and listed-option snapshots live on the captured EXPIRY maturities. The
    # two grids never share a maturity key, so the old exact-key join silently left every cell with no
    # slice, no forward and no quote. Here the cell sits at 0.2466y (a reading tenor) while the slice /
    # forward / quote-expiry are at 0.25y; the nearest-maturity join must still thread all three
    # through. Built directly (not via _analytics_store_with_quotes, which seeds an on-grid cell).
    from algotrading.frontend.routers.analytics import _group_by_maturity

    off_grid_maturity = 0.2466  # ~90 calendar days, the 3m reading tenor, off the 0.25y slice
    put_strike = round(seed.AN_FORWARD * (1.0 + seed.AN_PUT_LOGM), 2)
    put_bid, put_ask, put_volume = 4.10, 4.40, 1875.0
    cells = [
        seed.analytics_cell(
            delta_band="30dp",
            target_delta=seed.AN_PUT_DELTA,
            log_moneyness=seed.AN_PUT_LOGM,
            implied_vol=seed.AN_PUT_IV,
            delta=seed.AN_PUT_DELTA,
            dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
            maturity_years=off_grid_maturity,
        ),
    ]
    slices = [
        seed.surface_parameters_row(
            seed.MEMBER_AAA,
            SurfaceFitDiagnostics(
                rmse=0.0008, n_points=9, arb_free=True, bound_hits=(), converged=True
            ),
        )
    ]
    snapshots = [
        _quote_snapshot(
            seed, strike=put_strike, right="P", bid=put_bid, ask=put_ask, volume=put_volume
        )
    ]
    forwards = [
        _forward_curve_point(
            seed, implied_rate=0.031, implied_carry=0.0, implied_dividend=0.02
        )
    ]

    entry = _group_by_maturity(cells, slices, snapshots, forwards)[0]
    point = next(p for p in entry["points"] if p["delta_band"] == "30dp")
    assert point["quote"]["bid"] == pytest.approx(put_bid)
    assert point["quote"]["ask"] == pytest.approx(put_ask)
    assert point["quote"]["volume"] == pytest.approx(put_volume)
    # The fitted slice and the per-tenor rate diagnostic must also bind through the nearest join, so
    # the surface-fit pill and Rate diagnostics panel are populated, not the broken "fit not
    # available" / "projection gap" state.
    assert entry["surface_slice"] is not None
    assert entry["rate_diagnostics"] is not None


def test_reading_tenor_with_no_captured_neighbour_stays_unbound(
    seed: ModuleType,
) -> None:
    # The honest gap: a reading tenor far from any captured maturity (a 3y read against a chain that
    # stops at 3m) must NOT be yoked to the distant slice / forward. The cell keeps a null quote
    # block and a null surface_slice rather than a mismatched join.
    from algotrading.frontend.routers.analytics import _group_by_maturity

    far_cell = [
        seed.analytics_cell(
            delta_band="30dp",
            target_delta=seed.AN_PUT_DELTA,
            log_moneyness=seed.AN_PUT_LOGM,
            implied_vol=seed.AN_PUT_IV,
            delta=seed.AN_PUT_DELTA,
            dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
            maturity_years=3.0,
            tenor_label="3y",
        ),
    ]
    slices = [
        seed.surface_parameters_row(
            seed.MEMBER_AAA,
            SurfaceFitDiagnostics(
                rmse=0.0008, n_points=9, arb_free=True, bound_hits=(), converged=True
            ),
        )
    ]
    entry = _group_by_maturity(far_cell, slices, [], [])[0]
    assert entry["surface_slice"] is None
    point = entry["points"][0]
    assert point["quote"] == {"bid": None, "ask": None, "volume": None}
