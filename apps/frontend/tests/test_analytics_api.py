"""Analytics router tests: the projected tenor × delta-band grid for one ticker/day (WS 1F/1I).

The seeded cases persist real ``projected_option_analytics`` + ``surface_parameters`` rows
(the conftest seed: a 30Δ put + 30Δ call on one maturity) and assert the router groups and
echoes *those* hand-chosen values back — smile ordered by delta, dollar Greeks with their
stored unit strings, ADR 0029 field names. The surface-grid fallback case seeds its own
store inline.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


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
    # Smile ordered by delta: the 30Δ put (-0.30) first, the 30Δ call (+0.30) last. The
    # axis says what it is (F-BFF-04): the rich projection's x-axis is signed deltas.
    assert maturity["smile"]["axis_type"] == "delta"
    assert maturity["smile"]["deltas"] == [
        pytest.approx(seed.AN_PUT_DELTA),
        pytest.approx(seed.AN_CALL_DELTA),
    ]
    assert maturity["smile"]["implied_vols"] == [
        pytest.approx(seed.AN_PUT_IV),
        pytest.approx(seed.AN_CALL_IV),
    ]
    # The fitted SVI slice for the 3D surface is attached.
    assert maturity["surface_slice"]["svi_b"] == pytest.approx(seed.SVI_B)
    # Dollar Greeks read back on the band points.
    put_point = maturity["points"][0]
    assert put_point["forward_price"] == pytest.approx(seed.AN_FORWARD)
    assert put_point["metrics"]["delta"]["dollar"] == pytest.approx(seed.AN_PUT_DOLLAR_DELTA)


def test_analytics_payload_uses_blueprint_field_names(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # Field-name conformance (ADR 0029): the analytics payload uses forward_price / implied_vol /
    # log_moneyness / dollar_*. A renamed contract field turns this red.
    point = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]
    for field in ("forward_price", "implied_vol", "log_moneyness"):
        assert field in point, f"blueprint field {field!r} must be in the analytics payload"
    # The dollar_* layer is exposed as named metrics carrying the raw per-unit Greek.
    # RT-Vega (running-time / annualised vega, ADR 0050) rides beside vega.
    assert set(point["metrics"]) == {"delta", "gamma", "vega", "rt_vega", "theta", "rho"}
    assert "raw" in point["metrics"]["delta"] and "dollar" in point["metrics"]["delta"]


def test_dollar_greeks_carry_unit_strings(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # P0.2 / ADR 0036: every dollar number carries a non-empty unit string with pinned semantics.
    metrics = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]["metrics"]
    assert metrics["delta"]["unit"] == seed.AN_DOLLAR_DELTA_UNIT
    assert metrics["gamma"]["unit"] == seed.AN_DOLLAR_GAMMA_UNIT
    assert metrics["vega"]["unit"] == seed.AN_DOLLAR_VEGA_UNIT
    assert metrics["theta"]["unit"] == seed.AN_DOLLAR_THETA_UNIT
    assert metrics["rho"]["unit"] == seed.AN_DOLLAR_RHO_UNIT
    # RT-Vega (ADR 0050): raw + cash with its own unit, read straight back from the cell.
    assert metrics["rt_vega"]["raw"] == pytest.approx(seed.AN_RT_VEGA)
    assert metrics["rt_vega"]["dollar"] == pytest.approx(seed.AN_DOLLAR_RT_VEGA)
    assert metrics["rt_vega"]["unit"] == seed.AN_DOLLAR_RT_VEGA_UNIT
    for name in ("delta", "gamma", "vega", "rt_vega", "theta", "rho"):
        assert metrics[name]["unit"], f"{name} must carry a non-empty unit string"


def test_mirror_greeks_serialized_in_analytics_payload(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    """The analytics payload carries price_mirror and mirror_metrics (T-mirror-greeks-putcall).

    The seeded rows pre-date the mirror-greeks lane (no mirror fields set), so the payload
    carries ``price_mirror: null`` and null raw/dollar values inside ``mirror_metrics``.
    This pins the additive-nullable serialization path — a pre-lane partition must not 500.
    The structure (keys present, correct shape) is what this test locks.
    """
    point = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]
    # price_mirror is present (as null on pre-lane rows).
    assert "price_mirror" in point
    assert point["price_mirror"] is None  # seed row has no mirror fields
    # mirror_metrics is present with the three sides-greeks (delta/theta/rho, no gamma/vega).
    assert "mirror_metrics" in point
    mirror = point["mirror_metrics"]
    assert set(mirror) == {"delta", "theta", "rho"}
    for greek in ("delta", "theta", "rho"):
        assert "raw" in mirror[greek]
        assert "dollar" in mirror[greek]
        assert "unit" in mirror[greek]
        assert mirror[greek]["raw"] is None  # pre-lane row


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
    # The full labelled shape the web client matches on: label + the raw echoed value.
    assert response.json() == {"error": "bad_trade_date", "trade_date": "nope"}


def test_analytics_falls_back_to_surface_grid_when_projection_empty(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Friday-nappe path (B): when the tenor × delta-band projection produced no cells for the day
    # (it skips an underlying lacking a usable spot) but the surface fit persisted, the endpoint
    # rebuilds the nappe from surface_grid so the front shows a real vol surface instead of "No
    # surface to plot yet". IV per node is sqrt(total_variance / maturity_years); the moneyness
    # buckets stand in for the delta axis. Once projected_option_analytics lands it takes priority.
    underlying = "GRIDONLY"
    maturity_years = 0.25
    grid_rows = [(0.10, 0.012), (-0.10, 0.020), (0.00, 0.010)]  # unsorted on purpose
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

    # No projected_option_analytics on disk → the grid fallback fired (and says so).
    assert payload["source"] == "surface_grid"
    assert payload["n_maturities"] == 1
    maturity = payload["maturities"][0]
    assert maturity["maturity_years"] == pytest.approx(maturity_years)
    # F-BFF-04: the fallback x-axis is moneyness buckets and must say so — bucket values
    # never masquerade under a "deltas" key. The buckets ARE log-moneyness (the grid reads
    # total variance at k = bucket), so log_moneyness carries the same values legitimately.
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
    # The fitted SVI slice is still attached for the 3D trace; per-cell points stay empty until
    # the rich projection lands.
    assert maturity["surface_slice"]["svi_b"] == pytest.approx(seed.SVI_B)
    assert maturity["points"] == []


def test_analytics_prefers_projection_over_grid_fallback(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # When projected_option_analytics has cells, the rich grid wins and the fallback is not taken —
    # the seeded store has both a projection and a surface for AAA, so source is the projection.
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert payload["source"] == "projected_option_analytics"
    assert payload["maturities"][0]["points"], "rich per-cell points must be present"


def test_dense_surface_absent_for_a_single_fitted_slice(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # The dense reconstructed surface (the smooth 3D nappe) needs >= 2 fitted slices to span a
    # maturity axis; the seed carries one, so `surface` is None and the front falls back to the
    # band-point grid. The key is always present (typed contract), never missing.
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": seed.MEMBER_AAA, "trade_date": seed.TRADE_DATE.isoformat()},
    ).json()
    assert "surface" in payload
    assert payload["surface"] is None
