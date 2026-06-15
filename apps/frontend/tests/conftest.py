"""Shared fixtures for the frontend BFF test suite.

The seeded-store machinery lives here, in one home: the hand-chosen oracle constants,
the contract-record builders, and the client fixtures over stores pre-seeded with real
contract rows (written through ``ParquetStore.write`` — exactly the table contracts the
actor pipeline emits). Expected values are derived independently: the SVI/Greek/OHLC
numbers are hand-chosen inputs written into the rows, and the per-router test files
assert the routers surface *those* values unchanged — never numbers copied from BFF
output.

The suite runs under ``--import-mode=importlib``, where test modules cannot import
siblings; the per-router files therefore reach the constants and builders through the
``seed`` fixture, which exposes this module's namespace.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.orchestration.run_state import (
    EOD_STAGES,
    OUTCOME_FAILED,
    OUTCOME_OK,
    StageRun,
    record_stage,
)
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------------------
# Hand-chosen oracle values written into the seeded rows. The assertions in the per-router
# files check the routers echo these exact numbers back — the independent oracle is "what
# we wrote in".
# --------------------------------------------------------------------------------------

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 5, 29)
EXPIRY = date(2026, 8, 28)
UNDERLYING = "AAPL"
MATURITY_YEARS = 0.25
PORTFOLIO_ID = "pf-readback"

SVI_A = 0.0123
SVI_B = 0.3400
SVI_RHO = -0.2100
SVI_M = 0.0
SVI_SIGMA = 0.1800
NET_DELTA = 123.45
NET_VEGA = 89.0
SCENARIO_PNL = -4567.89

# Hand-chosen dollar Greeks written into a pricing_results row; the metrics endpoint must
# echo these back beside their raw per-unit values and a non-empty unit string.
PR_DOLLAR_DELTA = 55.0
PR_DOLLAR_GAMMA = 8.0
PR_DOLLAR_VEGA = 0.10
PR_DOLLAR_THETA = -0.0000274
PR_DOLLAR_RHO = 0.0003

# A second pricing_results row under its own underlying ("GMMA") with round numbers, used to
# pin the dollar_gamma value-vs-label seam by hand. The engine stores dollar_gamma in the
# one_pct convention (ADR 0036: Γ·S²/100 per 1% move), so the stored and served value is:
#   Γ·S²·mult·qty / 100 = 0.04 · 200² · 100 · 1 / 100 = 1600.0
# The BFF passes this through unchanged; the per-$1 number (160000.0) is kept only as a
# guard to catch a serializer that passes the un-divided value through.
GAMMA_UNDERLYING = "GMMA"
GMMA_RAW_GAMMA = 0.04
GMMA_SPOT = 200.0
GMMA_MULT = 100.0
GMMA_QTY = 1.0
# Per-$1 (one_dollar) number — kept as a guard in the adversarial seam test; NOT stored in the row.
GMMA_DOLLAR_GAMMA_ONE_DOLLAR = GMMA_RAW_GAMMA * GMMA_SPOT * GMMA_SPOT * GMMA_MULT * GMMA_QTY
# Per-1% (one_pct) stored on the row and served by the BFF: 160000.0 / 100 = 1600.0.
GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED = 1600.0

# Hand-chosen daily OHLC bars for the index members. The price-history endpoint must echo
# these exact values back; the constituent price-first ordering keys off the latest close.
# AAA's latest close (192.0) > BBB's (45.5), so AAA must sort first.
INDEX = "TESTIDX"
MEMBER_AAA = "AAA"
MEMBER_BBB = "BBB"
AAA_BARS = [
    (date(2026, 5, 28), 188.0, 191.0, 187.0, 190.0, 1_000_000.0),
    (date(2026, 5, 29), 190.0, 193.5, 189.5, 192.0, 1_200_000.0),
]
BBB_BARS = [
    (date(2026, 5, 28), 44.0, 46.0, 43.5, 45.0, 500_000.0),
    (date(2026, 5, 29), 45.0, 46.2, 44.8, 45.5, 600_000.0),
]
# AAA's bar on 2026-05-29: the field-name conformance + read-back oracle.
AAA_29_OPEN = 190.0
AAA_29_HIGH = 193.5
AAA_29_LOW = 189.5
AAA_29_CLOSE = 192.0
AAA_29_VOLUME = 1_200_000.0
BBB_29_CLOSE = 45.5

# Hand-chosen projected-analytics cell values for AAA, one maturity (3M), two band points
# (a 30Δ put and a 30Δ call). The analytics endpoint must echo these back with the stored
# unit strings; the smile is ordered by delta (put first).
AN_FORWARD = 195.0
AN_PUT_IV = 0.2700
AN_PUT_LOGM = -0.1500
AN_PUT_DELTA = -0.30
AN_CALL_IV = 0.2300
AN_CALL_LOGM = 0.1200
AN_CALL_DELTA = 0.30
AN_PUT_DOLLAR_DELTA = -58.5
AN_CALL_DOLLAR_DELTA = 58.5
AN_DOLLAR_DELTA_UNIT = "$ per $1 of underlying"
AN_DOLLAR_GAMMA_UNIT = "$ per 1% move"
AN_DOLLAR_VEGA_UNIT = "$ per 1 vol point"
AN_DOLLAR_THETA_UNIT = "$ per calendar day"
AN_DOLLAR_RHO_UNIT = "$ per 1% rate"
AN_DOLLAR_RT_VEGA_UNIT = "$ per 1 vol point"
# Per-cell dollar Greeks shared by both band points (the basket sum oracle restates these).
AN_DOLLAR_GAMMA = 7.6
AN_DOLLAR_VEGA = 0.31
AN_DOLLAR_THETA = -0.000041
AN_DOLLAR_RHO = 0.0005
# RT-Vega (running-time / annualised vega, ADR 0049) per strike, raw + cash.
AN_RT_VEGA = 0.62
AN_DOLLAR_RT_VEGA = 0.0062
AN_PRICE = 4.2

UNDERLYING_KEY = InstrumentKey(
    underlying_symbol=UNDERLYING,
    security_type="STK",
    exchange="SMART",
    currency="USD",
    multiplier=1.0,
    broker_contract_id="u-AAPL",
)
CALL_100 = InstrumentKey(
    underlying_symbol=UNDERLYING,
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=100.0,
    broker_contract_id="o-C-100",
    expiry=EXPIRY,
    strike=100.0,
    option_right="C",
)
# A GMMA-underlying option whose pricing row carries the round-number dollar_gamma above,
# so /api/risk/metrics?underlying=GMMA isolates exactly that row for the hand-computed seam test.
GMMA_CALL = InstrumentKey(
    underlying_symbol=GAMMA_UNDERLYING,
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=GMMA_MULT,
    broker_contract_id="o-GMMA-C-200",
    expiry=EXPIRY,
    strike=200.0,
    option_right="C",
)


# --------------------------------------------------------------------------------------
# Record builders + store seeding (shared across the per-router files via ``seed``).
# --------------------------------------------------------------------------------------


def prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="readback-test",
        config_hashes={"cfg": "cfg-readback"},
        source_records=(source_ref("raw_market_events", "sess-readback", source),),
        source_timestamps=(AS_OF,),
    )


def snapshot_row(instrument: InstrumentKey, mid: float) -> tables.MarketStateSnapshot:
    return tables.MarketStateSnapshot(
        snapshot_ts=AS_OF,
        instrument_key=instrument.canonical(),
        reference_spot=100.0,
        bid=mid - 0.05,
        ask=mid + 0.05,
        last=mid,
        spread_pct=0.001,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=TRADE_DATE,
        underlying=UNDERLYING,
        provenance=prov(f"snap:{instrument.broker_contract_id}"),
    )


def daily_bar_row(
    underlying: str, row: tuple[date, float, float, float, float, float]
) -> tables.DailyBar:
    trade_date, open_, high, low, close, volume = row
    return tables.DailyBar(
        provider="IBKR",
        underlying=underlying,
        trade_date=trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        bar_type="1d-TRADES",
        source="readback-test",
        provenance=prov(f"bar:{underlying}:{trade_date.isoformat()}"),
    )


def constituent_row(
    constituent: str,
    weight: float,
    add: date,
    remove: date | None,
    knowledge: date,
) -> tables.IndexConstituent:
    return tables.IndexConstituent(
        index=INDEX,
        constituent=constituent,
        effective_add_date=add,
        effective_remove_date=remove,
        knowledge_date=knowledge,
        vendor="Siblis",
        weight=weight,
    )


def analytics_cell(
    *,
    delta_band: str,
    target_delta: float,
    log_moneyness: float,
    implied_vol: float,
    delta: float,
    dollar_delta: float,
) -> tables.ProjectedOptionAnalytics:
    return tables.ProjectedOptionAnalytics(
        snapshot_ts=AS_OF,
        provider="IBKR",
        underlying=MEMBER_AAA,
        tenor_label="3m",
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=log_moneyness,
        strike=AN_FORWARD * (1.0 + log_moneyness),
        forward_price=AN_FORWARD,
        implied_vol=implied_vol,
        total_variance=implied_vol * implied_vol * 0.25,
        price=AN_PRICE,
        delta=delta,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=dollar_delta,
        dollar_gamma=AN_DOLLAR_GAMMA,
        dollar_vega=AN_DOLLAR_VEGA,
        dollar_delta_unit=AN_DOLLAR_DELTA_UNIT,
        dollar_gamma_unit=AN_DOLLAR_GAMMA_UNIT,
        dollar_vega_unit=AN_DOLLAR_VEGA_UNIT,
        model_version="svi-readback",
        pricer_version="px-readback",
        source_snapshot_ts=AS_OF,
        provenance=prov(f"analytics:{delta_band}"),
        dollar_theta=AN_DOLLAR_THETA,
        dollar_rho=AN_DOLLAR_RHO,
        dollar_theta_unit=AN_DOLLAR_THETA_UNIT,
        dollar_rho_unit=AN_DOLLAR_RHO_UNIT,
        rt_vega=AN_RT_VEGA,
        dollar_rt_vega=AN_DOLLAR_RT_VEGA,
        dollar_rt_vega_unit=AN_DOLLAR_RT_VEGA_UNIT,
    )


def analytics_cell_on(
    snapshot_ts: datetime, *, delta_band: str, dollar_delta: float
) -> tables.ProjectedOptionAnalytics:
    """An AAA 3M analytics cell on a chosen snapshot date (for the no-look-ahead tests)."""
    return tables.ProjectedOptionAnalytics(
        snapshot_ts=snapshot_ts, provider="IBKR", underlying=MEMBER_AAA, tenor_label="3m",
        maturity_years=0.25, delta_band=delta_band, target_delta=0.30, log_moneyness=0.0,
        strike=AN_FORWARD, forward_price=AN_FORWARD, implied_vol=0.25, total_variance=0.015625,
        price=AN_PRICE, delta=0.3, gamma=0.02, vega=0.31, theta=-0.05, rho=0.04,
        dollar_delta=dollar_delta, dollar_gamma=AN_DOLLAR_GAMMA, dollar_vega=AN_DOLLAR_VEGA,
        dollar_delta_unit=AN_DOLLAR_DELTA_UNIT, dollar_gamma_unit=AN_DOLLAR_GAMMA_UNIT,
        dollar_vega_unit=AN_DOLLAR_VEGA_UNIT, model_version="svi", pricer_version="px",
        source_snapshot_ts=snapshot_ts, provenance=prov("lookahead"),
        dollar_theta=AN_DOLLAR_THETA, dollar_rho=AN_DOLLAR_RHO,
        dollar_theta_unit=AN_DOLLAR_THETA_UNIT, dollar_rho_unit=AN_DOLLAR_RHO_UNIT,
    )


def surface_parameters_row(
    underlying: str, diagnostics: SurfaceFitDiagnostics
) -> tables.SurfaceParameters:
    return tables.SurfaceParameters(
        snapshot_ts=AS_OF,
        underlying=underlying,
        maturity_years=MATURITY_YEARS,
        model_version="svi-readback",
        svi_a=SVI_A,
        svi_b=SVI_B,
        svi_rho=SVI_RHO,
        svi_m=SVI_M,
        svi_sigma=SVI_SIGMA,
        expiry_date=EXPIRY,
        day_count="ACT/365",
        diagnostics=diagnostics,
        source_snapshot_ts=AS_OF,
        provenance=prov(f"surface:{underlying}"),
    )


def seed_store(root: Path) -> None:
    store = ParquetStore(root)
    # Daily OHLC bars for the two index members (price-history + price-first ordering oracle).
    store.write("daily_bar", [daily_bar_row(MEMBER_AAA, row) for row in AAA_BARS])
    store.write("daily_bar", [daily_bar_row(MEMBER_BBB, row) for row in BBB_BARS])
    # Bitemporal membership: AAA in the basket on TRADE_DATE; CCC was removed before it and a
    # FUT member is added after it — both must be absent from the as-of basket (look-ahead gate).
    store.write(
        "index_constituents",
        [
            constituent_row(MEMBER_AAA, 0.6, date(2026, 1, 1), None, date(2026, 1, 1)),
            constituent_row(MEMBER_BBB, 0.4, date(2026, 1, 1), None, date(2026, 1, 1)),
            constituent_row("CCC", 0.0, date(2025, 1, 1), date(2026, 4, 1), date(2026, 1, 1)),
            constituent_row("FUT", 0.0, date(2026, 6, 1), None, date(2026, 1, 1)),
        ],
    )
    # Projected-analytics cells for AAA, one maturity, a 30Δ put + a 30Δ call.
    store.write(
        "projected_option_analytics",
        [
            analytics_cell(
                delta_band="30dp",
                target_delta=AN_PUT_DELTA,
                log_moneyness=AN_PUT_LOGM,
                implied_vol=AN_PUT_IV,
                delta=AN_PUT_DELTA,
                dollar_delta=AN_PUT_DOLLAR_DELTA,
            ),
            analytics_cell(
                delta_band="30dc",
                target_delta=AN_CALL_DELTA,
                log_moneyness=AN_CALL_LOGM,
                implied_vol=AN_CALL_IV,
                delta=AN_CALL_DELTA,
                dollar_delta=AN_CALL_DOLLAR_DELTA,
            ),
        ],
    )
    # A clean fitted SVI slice for AAA on TRADE_DATE so the analytics surface_slice is populated.
    store.write(
        "surface_parameters",
        [
            surface_parameters_row(
                MEMBER_AAA,
                SurfaceFitDiagnostics(
                    rmse=0.0008, n_points=9, arb_free=True, bound_hits=(), converged=True,
                ),
            )
        ],
    )
    _seed_legacy_store(store)


def _seed_legacy_store(store: ParquetStore) -> None:
    # A raw snapshot for the underlying/date so build_dashboard sees data flowing and can
    # match the surface partition to a raw underlying (surfaces_building -> ok).
    store.write(
        "market_state_snapshots",
        [snapshot_row(UNDERLYING_KEY, 100.0), snapshot_row(CALL_100, 3.99)],
    )
    store.write(
        "surface_parameters",
        [
            surface_parameters_row(
                UNDERLYING,
                # A degenerate calibration in the live SX5E/SPX shape: rho railed to its
                # bound, optimizer not converged, butterfly breached — the BFF must flag it.
                SurfaceFitDiagnostics(
                    rmse=0.0009, n_points=11, arb_free=False,
                    bound_hits=("rho_lower",), converged=False,
                ),
            )
        ],
    )
    store.write(
        "risk_aggregates",
        [
            tables.RiskAggregate(
                valuation_ts=AS_OF,
                portfolio_id=PORTFOLIO_ID,
                group_key=UNDERLYING,
                net_delta=NET_DELTA,
                net_gamma=6.7,
                net_vega=NET_VEGA,
                net_theta=-12.3,
                source_snapshot_ts=AS_OF,
                provenance=prov("risk:AAPL"),
            )
        ],
    )
    store.write(
        "pricing_results",
        [
            tables.PricingResult(
                snapshot_ts=AS_OF,
                contract_key=CALL_100.canonical(),
                pricer_version="px-readback",
                price=3.99,
                delta=0.55,
                gamma=0.02,
                vega=0.10,
                theta=-0.01,
                rho=0.03,
                dollar_delta=PR_DOLLAR_DELTA,
                dollar_gamma=PR_DOLLAR_GAMMA,
                dollar_vega=PR_DOLLAR_VEGA,
                dollar_theta=PR_DOLLAR_THETA,
                dollar_rho=PR_DOLLAR_RHO,
                source_snapshot_ts=AS_OF,
                provenance=prov("px:AAPL"),
            ),
            tables.PricingResult(
                snapshot_ts=AS_OF,
                contract_key=GMMA_CALL.canonical(),
                pricer_version="px-readback",
                price=12.5,
                delta=0.50,
                gamma=GMMA_RAW_GAMMA,
                vega=0.20,
                theta=-0.02,
                rho=0.05,
                dollar_delta=GMMA_RAW_GAMMA * 0.0,  # unused by the seam test; kept finite
                dollar_gamma=GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED,  # per-1% (one_pct), as the engine stores
                dollar_vega=0.002,
                dollar_theta=-0.00005,
                dollar_rho=0.0005,
                source_snapshot_ts=AS_OF,
                provenance=prov("px:GMMA"),
            ),
        ],
    )
    store.write(
        "scenario_results",
        [
            tables.ScenarioResult(
                valuation_ts=AS_OF,
                portfolio_id=PORTFOLIO_ID,
                scenario_id="spot-down-10",
                contract_key=CALL_100.canonical(),
                spot_shock=-0.10,
                vol_shock=0.0,
                time_shock=0.0,
                scenario_pnl=SCENARIO_PNL,
                scenario_version="scn-1",
                source_snapshot_ts=AS_OF,
                provenance=prov("scenario:AAPL"),
            )
        ],
    )


# --- WS 2B: a 3×3 cartesian (spot × vol) stress surface persisted as scenario_results ---
# The independent oracle is "what we wrote in": the portfolio total per (spot, vol) cell,
# summed across contracts. The centre (0,0) is two contracts that net to 0, so the reshape
# must sum contracts per cell — not pick one.
SURFACE_PORTFOLIO = "pf-surface"
SURFACE_SPOT_AXIS = [-0.5, 0.0, 0.5]
SURFACE_VOL_AXIS = [-0.5, 0.0, 0.5]
SURFACE_VERSION = "scn-2026.06.07+grid+surf"
SURFACE_TOTALS = {
    (-0.5, -0.5): -5000.0, (-0.5, 0.0): -4000.0, (-0.5, 0.5): -3000.0,
    (0.0, -0.5): -100.0, (0.0, 0.0): 0.0, (0.0, 0.5): 150.0,
    (0.5, -0.5): 3000.0, (0.5, 0.0): 4000.0, (0.5, 0.5): 5000.0,
}
# The centre cell is two contracts (+250, -250) → the reshape sums them to SURFACE_TOTALS[0,0].
SURFACE_CENTRE_LEGS = (250.0, -250.0)


def _surface_id(spot_shock: float, vol_shock: float) -> str:
    return f"surf_s{spot_shock:+.4f}_v{vol_shock:+.4f}"


def surface_cell(
    spot_shock: float, vol_shock: float, pnl: float, contract_key: str
) -> tables.ScenarioResult:
    return tables.ScenarioResult(
        valuation_ts=AS_OF,
        portfolio_id=SURFACE_PORTFOLIO,
        scenario_id=_surface_id(spot_shock, vol_shock),
        contract_key=contract_key,
        spot_shock=spot_shock,
        vol_shock=vol_shock,
        time_shock=0.0,
        scenario_pnl=pnl,
        scenario_version=SURFACE_VERSION,
        source_snapshot_ts=AS_OF,
        provenance=prov(f"surf:{spot_shock}:{vol_shock}"),
    )


def seed_surface_store(root: Path) -> None:
    store = ParquetStore(root)
    rows = []
    for spot_shock in SURFACE_SPOT_AXIS:
        for vol_shock in SURFACE_VOL_AXIS:
            if (spot_shock, vol_shock) == (0.0, 0.0):
                rows.append(surface_cell(0.0, 0.0, SURFACE_CENTRE_LEGS[0], CALL_100.canonical()))
                rows.append(surface_cell(0.0, 0.0, SURFACE_CENTRE_LEGS[1], GMMA_CALL.canonical()))
            else:
                rows.append(
                    surface_cell(
                        spot_shock,
                        vol_shock,
                        SURFACE_TOTALS[(spot_shock, vol_shock)],
                        CALL_100.canonical(),
                    )
                )
    # A families cell coexists in the same partition; 2C reads it via `cells`, and the surface
    # reshape (surf_-prefixed only) must ignore it.
    rows.append(
        tables.ScenarioResult(
            valuation_ts=AS_OF,
            portfolio_id=SURFACE_PORTFOLIO,
            scenario_id="spot_-0.0500",
            contract_key=CALL_100.canonical(),
            spot_shock=-0.05,
            vol_shock=0.0,
            time_shock=0.0,
            scenario_pnl=-42.0,
            scenario_version=SURFACE_VERSION,
            source_snapshot_ts=AS_OF,
            provenance=prov("fam:spot"),
        )
    )
    store.write("scenario_results", rows)


# -- recorded-dates: the 1G run-state ledger (two complete runs + one partial) ----------
COMPLETE_DATE_1 = date(2026, 5, 28)
COMPLETE_DATE_2 = date(2026, 5, 29)
PARTIAL_DATE = date(2026, 5, 30)


def seed_ledger(root: Path) -> None:
    """Two gap-free completed EOD runs + one partial/failed run in the run-state ledger."""
    for trade_date in (COMPLETE_DATE_1, COMPLETE_DATE_2):
        for stage in EOD_STAGES:
            record_stage(
                root,
                StageRun(
                    trade_date=trade_date,
                    stage=stage,
                    outcome=OUTCOME_OK,
                    run_id=f"run-{trade_date.isoformat()}",
                    recorded_ts=AS_OF,
                ),
            )
    # A partial/failed day: only the first two stages, the last recorded failed. Not complete.
    record_stage(
        root,
        StageRun(
            trade_date=PARTIAL_DATE,
            stage=EOD_STAGES[0],
            outcome=OUTCOME_OK,
            run_id="run-partial",
            recorded_ts=AS_OF,
        ),
    )
    record_stage(
        root,
        StageRun(
            trade_date=PARTIAL_DATE,
            stage=EOD_STAGES[1],
            outcome=OUTCOME_FAILED,
            run_id="run-partial",
            recorded_ts=AS_OF,
        ),
    )


# --------------------------------------------------------------------------------------
# Fixtures.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def seed() -> ModuleType:
    """This module's namespace: the oracle constants + record builders above.

    The suite runs under ``--import-mode=importlib`` (test modules cannot import
    siblings), so the shared seed vocabulary travels as a fixture instead of an import.
    """
    return sys.modules[__name__]


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    """An AppContext wired to an empty tmp store and a tmp configs dir."""
    store_root = tmp_path / "data"
    configs_dir = tmp_path / "configs"
    return AppContext(
        store_root=store_root,
        configs_dir=configs_dir,
        store=ParquetStore(store_root),
        # An index default (not a single-name) — the empty tmp configs carry no registry to
        # resolve it from, so set it explicitly for the routers' no-index fallback.
        default_underlying="SX5E",
    )


@pytest.fixture
def infra_client(ctx: AppContext) -> Iterator[TestClient]:
    """TestClient over the infra-wired BFF (empty store)."""
    with TestClient(create_app(ctx)) as client:
        yield client


def seeded_context(root: Path) -> AppContext:
    """Seed ``root`` with the full readback fixture set and wire a context over it."""
    seed_store(root)
    return AppContext(
        store_root=root,
        configs_dir=root.parent / "configs",
        store=ParquetStore(root),
        default_underlying=UNDERLYING,
    )


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over the BFF wired to a store pre-seeded with real contract rows."""
    with TestClient(create_app(seeded_context(tmp_path / "data"))) as client:
        yield client


@pytest.fixture
def surface_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over a store seeded with a 3×3 surface (+ one families cell)."""
    store_root = tmp_path / "data"
    seed_surface_store(store_root)
    app_ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    with TestClient(create_app(app_ctx)) as client:
        yield client


@pytest.fixture
def ledger_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over a seeded store whose run-state ledger has 2 complete + 1 partial run."""
    store_root = tmp_path / "data"
    app_ctx = seeded_context(store_root)
    seed_ledger(store_root)
    with TestClient(create_app(app_ctx)) as client:
        yield client
