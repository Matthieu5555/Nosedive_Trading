"""BFF: the additive external risk-free curve r(T) + implied−riskfree spread surface (ADR 0054).

Seeds a temp store with an SX5E (EUR) forward point + an ingested `rates` curve, points the app at
the real shipped `configs/` (SX5E -> EUR, the EUR pillar set + warn-only bound), and asserts the BFF
surfaces the curve and the spread WITHOUT recomputing anything (it evaluates the persisted curve and
pairs it with the persisted implied_rate). Expected values are derived here, not copied from the BFF.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import snapshot_stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.rates import build_rate_points
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS = _REPO_ROOT / "configs"

_TRADE_DATE = date(2026, 5, 29)
_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_CALC = datetime(2026, 5, 29, 15, 30, 5, tzinfo=UTC)
_EXPIRY = date(2026, 8, 28)
_HASHES = {"rates": "rates-hash-0"}
_MATURITY = 0.25  # the 3m pillar -> r(T) reads the 3m pillar rate exactly


def _stamp():
    return snapshot_stamp(
        calc_ts=_CALC,
        code_version="test",
        config_hashes=_HASHES,
        source_snapshot_ts=_AS_OF,
        source_records=(),
        as_of=_TRADE_DATE,
    )


def _forward_point(implied_rate: float) -> tables.ForwardCurvePoint:
    return tables.ForwardCurvePoint(
        snapshot_ts=_AS_OF,
        underlying="SX5E",
        maturity_years=_MATURITY,
        expiry_date=_EXPIRY,
        day_count="ACT/365",
        forward_price=5000.0,
        diagnostics=tables.ForwardDiagnostics(
            method="parity", candidate_count=5, residual_mad=0.01, quality_label="good"
        ),
        source_snapshot_ts=_AS_OF,
        provenance=_stamp(),
        implied_rate=implied_rate,
        implied_carry=0.01,
        implied_dividend=implied_rate - 0.01,
    )


def _analytics_cell() -> tables.ProjectedOptionAnalytics:
    return tables.ProjectedOptionAnalytics(
        snapshot_ts=_AS_OF,
        provider="IBKR",
        underlying="SX5E",
        tenor_label="3m",
        maturity_years=_MATURITY,
        delta_band="30dc",
        target_delta=0.30,
        log_moneyness=0.0,
        strike=5000.0,
        forward_price=5000.0,
        implied_vol=0.20,
        total_variance=0.20 * 0.20 * _MATURITY,
        price=100.0,
        delta=0.30,
        gamma=0.001,
        vega=10.0,
        theta=-1.0,
        rho=5.0,
        dollar_delta=1.0,
        dollar_gamma=1.0,
        dollar_vega=1.0,
        dollar_delta_unit="$ per $1 of underlying",
        dollar_gamma_unit="$ per 1% move",
        dollar_vega_unit="$ per 1 vol point",
        model_version="svi-1",
        pricer_version="pricer-1",
        source_snapshot_ts=_AS_OF,
        provenance=_stamp(),
    )


def _seed_store(root: Path, *, published_3m: float, implied_rate: float) -> None:
    from algotrading.core.config import load_platform_config

    store = ParquetStore(root)
    store.write("projected_option_analytics", [_analytics_cell()])
    store.write("forward_curve", [_forward_point(implied_rate)])

    eur = load_platform_config(_CONFIGS).rates.for_currency("EUR")
    # Publish only the 3m pillar so r(0.25) reads it exactly (the other pillars are a coverage gap).
    points = build_rate_points(
        currency_config=eur,
        published_levels={"euribor_3m": published_3m},
        as_of=_TRADE_DATE,
        snapshot_ts=_AS_OF,
        source_snapshot_ts=_AS_OF,
        calc_ts=_CALC,
        config_hashes=_HASHES,
    )
    store.write("rates", list(points))


def _client(root: Path) -> TestClient:
    ctx = AppContext(
        store_root=root,
        configs_dir=_CONFIGS,
        store=ParquetStore(root),
        default_underlying="SX5E",
    )
    return TestClient(create_app(ctx))


def _payload(client: TestClient) -> dict:
    return client.get(
        "/api/analytics",
        params={"underlying": "SX5E", "trade_date": _TRADE_DATE.isoformat()},
    ).json()


def test_top_level_rate_curve_surfaces_the_ingested_pillars(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _seed_store(root, published_3m=0.03, implied_rate=0.041)
    with _client(root) as client:
        curve = _payload(client)["rate_curve"]
    assert curve is not None
    assert curve["currency"] == "EUR"
    assert curve["n_pillars"] == 1
    assert curve["pillars"][0]["pillar_tenor"] == "3m"
    assert curve["pillars"][0]["instrument"] == "euribor_3m"
    assert "ACT/365" in curve["rate_unit"]


def test_per_maturity_spread_is_implied_minus_curve_and_warns_only(tmp_path: Path) -> None:
    # The shipped EUR config is continuous/ACT-365, so the published 3% is the canonical rate
    # unchanged (the identity conversion). r(0.25) reads the 3m pillar exactly.
    published = 0.03
    expected_rf = published
    implied = 0.041
    expected_spread = implied - expected_rf

    root = tmp_path / "data"
    _seed_store(root, published_3m=published, implied_rate=implied)
    with _client(root) as client:
        rate_curve = _payload(client)["maturities"][0]["rate_curve"]

    assert rate_curve is not None
    assert rate_curve["currency"] == "EUR"
    assert rate_curve["risk_free_rate"] == pytest.approx(expected_rf, abs=1e-9)
    assert rate_curve["implied_rate"] == pytest.approx(implied, abs=1e-12)
    assert rate_curve["spread"] == pytest.approx(expected_spread, abs=1e-9)
    # |spread| ~ 0.012 < 0.02 bound -> ok; the gate is warn-only by config regardless.
    assert rate_curve["breached"] is False
    assert rate_curve["qc_status"] == "ok"


def test_large_spread_breach_warns_not_fails(tmp_path: Path) -> None:
    # An implied rate far above the curve breaches the 200bp bound; default disposition is WARN.
    root = tmp_path / "data"
    _seed_store(root, published_3m=0.03, implied_rate=0.20)
    with _client(root) as client:
        rate_curve = _payload(client)["maturities"][0]["rate_curve"]
    assert rate_curve["breached"] is True
    assert rate_curve["qc_status"] == "warn"


def test_rate_curve_is_null_when_no_curve_is_seeded(tmp_path: Path) -> None:
    # Additive surface: with a forward but no rates partition, the rate_curve fields are null and the
    # rest of the payload is unaffected.
    root = tmp_path / "data"
    store = ParquetStore(root)
    store.write("projected_option_analytics", [_analytics_cell()])
    store.write("forward_curve", [_forward_point(0.041)])
    with _client(root) as client:
        payload = _payload(client)
    assert payload["rate_curve"] is None
    assert payload["maturities"][0]["rate_curve"] is None
    # The pre-existing rate_diagnostics (parity-implied) is unaffected.
    assert payload["maturities"][0]["rate_diagnostics"]["implied_rate"] == pytest.approx(0.041)
