"""Store-backed market dashboard tests: the BFF over a real seeded ParquetStore.

The seeded market state anchors to spot 100, forward 100, T 0.25, vol 0.20,
multiplier 100. The Black-76 hand values (see docstring) are the independent oracle.

    ATM call (F=K=100, sigma=0.2, T=0.25, DF=1):
        d1 = (ln(F/K) + sigma^2 T / 2) / (sigma sqrt(T)) = 0.05
        C  = F (N(0.05) - N(-0.05)) = 100 (0.519939 - 0.480061) = 3.98776
        spot delta (DF=1, carry 0) = N(d1) = 0.519939
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.bundles import (
    ForwardDiagnostics,
    IvDiagnostics,
    SurfaceFitDiagnostics,
)
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 5, 29)
EXPIRY = date(2026, 8, 28)
MATURITY_YEARS = 0.25
SPOT = 100.0
VOL = 0.20
ATM_CALL_PRICE = 3.98776
ATM_CALL_DELTA = 0.519939

UNDERLYING_KEY = InstrumentKey(
    underlying_symbol="AAPL",
    security_type="STK",
    exchange="SMART",
    currency="USD",
    multiplier=1.0,
    broker_contract_id="u-AAPL",
)
CALL_100 = InstrumentKey(
    underlying_symbol="AAPL",
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=100.0,
    broker_contract_id="o-C-100",
    expiry=EXPIRY,
    strike=100.0,
    option_right="C",
)
PUT_100 = InstrumentKey(
    underlying_symbol="AAPL",
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=100.0,
    broker_contract_id="o-P-100",
    expiry=EXPIRY,
    strike=100.0,
    option_right="P",
)


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="test-pipeline",
        config_hash="cfg-test",
        source_records=(source_ref("raw_market_events", "sess-test", source),),
        source_timestamps=(AS_OF,),
    )


def _snapshot(instrument: InstrumentKey, mid: float) -> tables.MarketStateSnapshot:
    return tables.MarketStateSnapshot(
        snapshot_ts=AS_OF,
        instrument_key=instrument.canonical(),
        reference_spot=SPOT,
        bid=mid - 0.05,
        ask=mid + 0.05,
        last=mid,
        spread_pct=0.001,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=TRADE_DATE,
        underlying="AAPL",
        provenance=_prov(f"snap:{instrument.broker_contract_id}"),
    )


def _seed_store(root: Path) -> None:
    store = ParquetStore(root)
    store.write(
        "instrument_master",
        [
            tables.InstrumentMaster(
                instrument_key=key.canonical(),
                as_of_date=TRADE_DATE,
                instrument=key,
                raw_broker_payload="{}",
            )
            for key in (UNDERLYING_KEY, CALL_100, PUT_100)
        ],
    )
    store.write(
        "market_state_snapshots",
        [_snapshot(UNDERLYING_KEY, SPOT), _snapshot(CALL_100, 3.99), _snapshot(PUT_100, 3.99)],
    )
    store.write(
        "forward_curve",
        [
            tables.ForwardCurvePoint(
                snapshot_ts=AS_OF,
                underlying="AAPL",
                maturity_years=MATURITY_YEARS,
                expiry_date=EXPIRY,
                day_count="ACT/365",
                forward=100.0,
                diagnostics=ForwardDiagnostics(
                    method="parity_regression",
                    candidate_count=2,
                    residual_mad=0.0,
                    quality_label="good",
                ),
                source_snapshot_ts=AS_OF,
                provenance=_prov("forward:AAPL"),
            )
        ],
    )
    store.write(
        "iv_points",
        [
            tables.IvPoint(
                snapshot_ts=AS_OF,
                contract_key=key.canonical(),
                iv=VOL,
                k=0.0,
                total_variance=VOL * VOL * MATURITY_YEARS,
                solver_version="test-solver",
                diagnostics=IvDiagnostics(
                    converged=True, iterations=4, residual=0.0, status="converged"
                ),
                source_snapshot_ts=AS_OF,
                provenance=_prov(f"iv:{key.broker_contract_id}"),
            )
            for key in (CALL_100, PUT_100)
        ],
    )
    store.write(
        "surface_parameters",
        [
            tables.SurfaceParameters(
                snapshot_ts=AS_OF,
                underlying="AAPL",
                maturity_years=MATURITY_YEARS,
                model_version="svi-test",
                svi_a=VOL * VOL * MATURITY_YEARS,
                svi_b=1e-9,
                svi_rho=0.0,
                svi_m=0.0,
                svi_sigma=0.1,
                expiry_date=EXPIRY,
                day_count="ACT/365",
                diagnostics=SurfaceFitDiagnostics(rmse=0.001, n_points=2, arb_free=True),
                source_snapshot_ts=AS_OF,
                provenance=_prov("surface:AAPL"),
            )
        ],
    )
    store.write(
        "surface_grid",
        [
            tables.SurfaceGrid(
                snapshot_ts=AS_OF,
                underlying="AAPL",
                maturity_years=MATURITY_YEARS,
                moneyness_bucket=k,
                model_version="svi-test",
                total_variance=VOL * VOL * MATURITY_YEARS,
                source_snapshot_ts=AS_OF,
                provenance=_prov(f"grid:{k}"),
            )
            for k in (-0.1, 0.0, 0.1)
        ],
    )


@pytest.fixture
def store_client(tmp_path: Path) -> TestClient:
    _seed_store(tmp_path)
    ctx = AppContext(
        store_root=tmp_path,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(tmp_path),
        default_underlying="AAPL",
    )
    return TestClient(create_app(ctx))


@pytest.fixture
def empty_client(tmp_path: Path) -> TestClient:
    empty_root = tmp_path / "empty"
    ctx = AppContext(
        store_root=empty_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(empty_root),
    )
    return TestClient(create_app(ctx))


def test_underlyings_lists_store_symbols_ahead_of_fixture_ones(
    store_client: TestClient,
) -> None:
    body = store_client.get("/api/underlyings").json()
    symbols = [item["symbol"] for item in body["underlyings"]]
    assert symbols[0] == "AAPL"
    assert "SPX" in symbols


def test_market_dashboard_is_served_from_the_store(store_client: TestClient) -> None:
    response = store_client.get("/api/market?underlying=AAPL")

    assert response.status_code == 200
    body = response.json()
    assert body["provenance"]["provider"] == "store"
    assert body["provenance"]["code_version"] == "test-pipeline"
    assert body["index_snapshot"]["last"] == pytest.approx(SPOT)
    chain = body["option_chain"]
    assert len(chain) == 2
    for quote in chain:
        assert quote["implied_vol"] == pytest.approx(VOL, abs=1e-9)
    call = next(q for q in chain if q["option_type"] == "call")
    assert call["greeks"]["delta"] == pytest.approx(ATM_CALL_DELTA, abs=1e-3)
    surface = body["volatility_surface"]
    assert surface["slices"][0]["atm_vol"] == pytest.approx(VOL, abs=1e-4)
    assert len(surface["points"]) == 3


def test_fixture_underlyings_keep_their_explicit_fixture_stamp(
    store_client: TestClient,
) -> None:
    body = store_client.get("/api/market?underlying=SPX").json()
    assert body["provenance"]["provider"] == "fixture"


def test_unknown_underlying_is_still_a_404(store_client: TestClient) -> None:
    assert store_client.get("/api/market?underlying=NOPE").status_code == 404


def test_empty_store_falls_back_to_fixture_serving(empty_client: TestClient) -> None:
    body = empty_client.get("/api/market?underlying=SPX").json()
    assert body["provenance"]["provider"] == "fixture"
    symbols = [u["symbol"] for u in empty_client.get("/api/underlyings").json()["underlyings"]]
    assert "SPX" in symbols
