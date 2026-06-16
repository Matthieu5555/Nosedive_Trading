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

PR_DOLLAR_DELTA = 55.0
PR_DOLLAR_GAMMA = 8.0
PR_DOLLAR_VEGA = 0.10
PR_DOLLAR_THETA = -0.0000274
PR_DOLLAR_RHO = 0.0003

GAMMA_UNDERLYING = "GMMA"
GMMA_RAW_GAMMA = 0.04
GMMA_SPOT = 200.0
GMMA_MULT = 100.0
GMMA_QTY = 1.0
GMMA_DOLLAR_GAMMA_ONE_DOLLAR = GMMA_RAW_GAMMA * GMMA_SPOT * GMMA_SPOT * GMMA_MULT * GMMA_QTY
GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED = 1600.0

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
AAA_29_OPEN = 190.0
AAA_29_HIGH = 193.5
AAA_29_LOW = 189.5
AAA_29_CLOSE = 192.0
AAA_29_VOLUME = 1_200_000.0
BBB_29_CLOSE = 45.5

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
AN_DOLLAR_GAMMA = 7.6
AN_DOLLAR_VEGA = 0.31
AN_DOLLAR_THETA = -0.000041
AN_DOLLAR_RHO = 0.0005
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
    store.write("daily_bar", [daily_bar_row(MEMBER_AAA, row) for row in AAA_BARS])
    store.write("daily_bar", [daily_bar_row(MEMBER_BBB, row) for row in BBB_BARS])
    store.write(
        "index_constituents",
        [
            constituent_row(MEMBER_AAA, 0.6, date(2026, 1, 1), None, date(2026, 1, 1)),
            constituent_row(MEMBER_BBB, 0.4, date(2026, 1, 1), None, date(2026, 1, 1)),
            constituent_row("CCC", 0.0, date(2025, 1, 1), date(2026, 4, 1), date(2026, 1, 1)),
            constituent_row("FUT", 0.0, date(2026, 6, 1), None, date(2026, 1, 1)),
        ],
    )
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
    store.write(
        "market_state_snapshots",
        [snapshot_row(UNDERLYING_KEY, 100.0), snapshot_row(CALL_100, 3.99)],
    )
    store.write(
        "surface_parameters",
        [
            surface_parameters_row(
                UNDERLYING,
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
                dollar_delta=GMMA_RAW_GAMMA * 0.0,
                dollar_gamma=GMMA_DOLLAR_GAMMA_ONE_PCT_EXPECTED,
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


SURFACE_PORTFOLIO = "pf-surface"
SURFACE_SPOT_AXIS = [-0.5, 0.0, 0.5]
SURFACE_VOL_AXIS = [-0.5, 0.0, 0.5]
SURFACE_VERSION = "scn-2026.06.07+grid+surf"
SURFACE_TOTALS = {
    (-0.5, -0.5): -5000.0, (-0.5, 0.0): -4000.0, (-0.5, 0.5): -3000.0,
    (0.0, -0.5): -100.0, (0.0, 0.0): 0.0, (0.0, 0.5): 150.0,
    (0.5, -0.5): 3000.0, (0.5, 0.0): 4000.0, (0.5, 0.5): 5000.0,
}
SURFACE_CENTRE_LEGS = (250.0, -250.0)

NAMED_2008_SPOT = -0.40
NAMED_2008_VOL = 0.30
NAMED_2008_RATE = -0.01
NAMED_2008_LEGS = (-1200.0, -800.0)
NAMED_2008_PNL = NAMED_2008_LEGS[0] + NAMED_2008_LEGS[1]
NAMED_COVID_SPOT = -0.34
NAMED_COVID_VOL = 0.45
NAMED_COVID_PNL = -1500.0


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


def seed_named_scenarios_store(root: Path) -> None:
    store = ParquetStore(root)
    rows: list[tables.ScenarioResult] = []
    for contract_key, leg_pnl in (
        (CALL_100.canonical(), NAMED_2008_LEGS[0]),
        (GMMA_CALL.canonical(), NAMED_2008_LEGS[1]),
    ):
        rows.append(
            tables.ScenarioResult(
                valuation_ts=AS_OF,
                portfolio_id=SURFACE_PORTFOLIO,
                scenario_id="named_2008",
                contract_key=contract_key,
                spot_shock=NAMED_2008_SPOT,
                vol_shock=NAMED_2008_VOL,
                time_shock=0.0,
                scenario_pnl=leg_pnl,
                scenario_version=SURFACE_VERSION,
                source_snapshot_ts=AS_OF,
                provenance=prov(f"named:2008:{contract_key}"),
                rate_shock=NAMED_2008_RATE,
            )
        )
    rows.append(
        tables.ScenarioResult(
            valuation_ts=AS_OF,
            portfolio_id=SURFACE_PORTFOLIO,
            scenario_id="named_covid-2020",
            contract_key=CALL_100.canonical(),
            spot_shock=NAMED_COVID_SPOT,
            vol_shock=NAMED_COVID_VOL,
            time_shock=0.0,
            scenario_pnl=NAMED_COVID_PNL,
            scenario_version=SURFACE_VERSION,
            source_snapshot_ts=AS_OF,
            provenance=prov("named:covid"),
            rate_shock=0.0,
        )
    )
    rows.append(surface_cell(0.0, 0.0, 0.0, CALL_100.canonical()))
    store.write("scenario_results", rows)


RATE_PORTFOLIO = "pf-rate"
RATE_VERSION = "scn-rate+grid"
RATE_LEGS: dict[float, tuple[float, float]] = {
    -0.0010: (-300.0, -150.0),
    0.0: (0.0, 0.0),
    0.0010: (320.0, 160.0),
}
RATE_TOTALS = {shock: legs[0] + legs[1] for shock, legs in RATE_LEGS.items()}


def _rate_id(rate_shock: float) -> str:
    return f"rate_{rate_shock:+.4f}"


def seed_rate_store(root: Path) -> None:
    store = ParquetStore(root)
    rows: list[tables.ScenarioResult] = []
    for rate_shock, legs in RATE_LEGS.items():
        for contract_key, leg_pnl in zip(
            (CALL_100.canonical(), GMMA_CALL.canonical()), legs, strict=True
        ):
            rows.append(
                tables.ScenarioResult(
                    valuation_ts=AS_OF,
                    portfolio_id=RATE_PORTFOLIO,
                    scenario_id=_rate_id(rate_shock),
                    contract_key=contract_key,
                    spot_shock=0.0,
                    vol_shock=0.0,
                    time_shock=0.0,
                    scenario_pnl=leg_pnl,
                    scenario_version=RATE_VERSION,
                    source_snapshot_ts=AS_OF,
                    provenance=prov(f"rate:{rate_shock}:{contract_key}"),
                    rate_shock=rate_shock,
                )
            )
    rows.append(
        tables.ScenarioResult(
            valuation_ts=AS_OF,
            portfolio_id=RATE_PORTFOLIO,
            scenario_id="spot_-0.0500",
            contract_key=CALL_100.canonical(),
            spot_shock=-0.05,
            vol_shock=0.0,
            time_shock=0.0,
            scenario_pnl=-77.0,
            scenario_version=RATE_VERSION,
            source_snapshot_ts=AS_OF,
            provenance=prov("rate-store:fam:spot"),
            rate_shock=0.0,
        )
    )
    store.write("scenario_results", rows)


COMPLETE_DATE_1 = date(2026, 5, 28)
COMPLETE_DATE_2 = date(2026, 5, 29)
PARTIAL_DATE = date(2026, 5, 30)


def seed_ledger(root: Path) -> None:
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


@pytest.fixture(scope="session")
def seed() -> ModuleType:
    return sys.modules[__name__]


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    store_root = tmp_path / "data"
    configs_dir = tmp_path / "configs"
    return AppContext(
        store_root=store_root,
        configs_dir=configs_dir,
        store=ParquetStore(store_root),
        default_underlying="SX5E",
    )


@pytest.fixture
def infra_client(ctx: AppContext) -> Iterator[TestClient]:
    with TestClient(create_app(ctx)) as client:
        yield client


def seeded_context(root: Path) -> AppContext:
    seed_store(root)
    return AppContext(
        store_root=root,
        configs_dir=root.parent / "configs",
        store=ParquetStore(root),
        default_underlying=UNDERLYING,
    )


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(seeded_context(tmp_path / "data"))) as client:
        yield client


@pytest.fixture
def surface_client(tmp_path: Path) -> Iterator[TestClient]:
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
def named_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    seed_named_scenarios_store(store_root)
    app_ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    with TestClient(create_app(app_ctx)) as client:
        yield client


@pytest.fixture
def rate_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    seed_rate_store(store_root)
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
    store_root = tmp_path / "data"
    app_ctx = seeded_context(store_root)
    seed_ledger(store_root)
    with TestClient(create_app(app_ctx)) as client:
        yield client
