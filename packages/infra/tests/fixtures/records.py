"""One valid baseline record per table family.

These are the canonical "good" records: every table family, fully populated,
passing validation, ready to write. Two jobs:

* the storage round-trip test iterates all of them (write → read → equal);
* the rejection tests take one and break a single field, so each malformed case
  differs from a known-good record in exactly one way.

They are built once and returned as a fresh dict each call, so a test that mutates
a copy cannot disturb another test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, source_ref, stamp
from algotrading.infra.contracts import (
    ForwardCurvePoint,
    ForwardDiagnostics,
    InstrumentKey,
    InstrumentMaster,
    IvDiagnostics,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    QcResult,
    RawMarketEvent,
    RiskAggregate,
    ScenarioResult,
    SurfaceFitDiagnostics,
    SurfaceGrid,
    SurfaceParameters,
)

CODE_VERSION = "0.1.0-fixture"
CONFIG_HASH = "cfg-hash-0"

TRADE_DATE = date(2026, 5, 29)
EXPIRY = date(2026, 6, 19)
SNAPSHOT_TS = datetime(2026, 5, 29, 15, 30, 0, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 15, 30, 5, tzinfo=UTC)
EXCHANGE_TS = datetime(2026, 5, 29, 15, 29, 59, tzinfo=UTC)

UNDERLYING_KEY = InstrumentKey(
    underlying_symbol="AAPL",
    security_type="STK",
    exchange="SMART",
    currency="USD",
    multiplier=1.0,
    broker_contract_id="u-AAPL",
)
OPTION_KEY = InstrumentKey(
    underlying_symbol="AAPL",
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=100.0,
    broker_contract_id="o-AAPL-C-100",
    expiry=EXPIRY,
    strike=100.0,
    option_right="C",
)
INSTRUMENT_KEY = UNDERLYING_KEY.canonical()
CONTRACT_KEY = OPTION_KEY.canonical()


# The raw events the baseline derived records trace back to, keyed exactly as the
# raw-event table is — (session_id, event_id) — so lineage resolves to one row.
_DEFAULT_SOURCE_RECORDS = (
    source_ref("raw_market_events", "sess-1", "evt-1"),
    source_ref("raw_market_events", "sess-1", "evt-2"),
)


def make_stamp(
    source_records: tuple[SourceRecordRef, ...] = _DEFAULT_SOURCE_RECORDS,
) -> ProvenanceStamp:
    """A valid provenance stamp pointing at the given source records."""
    return stamp(
        calc_ts=CALC_TS,
        code_version=CODE_VERSION,
        config_hash=CONFIG_HASH,
        source_records=source_records,
        source_timestamps=(SNAPSHOT_TS,),
    )


def baseline_records() -> dict[str, Any]:
    """Return a fresh mapping of table name to one valid record each."""
    return {
        "instrument_master": InstrumentMaster(
            instrument_key=INSTRUMENT_KEY,
            as_of_date=TRADE_DATE,
            instrument=UNDERLYING_KEY,
            raw_broker_payload='{"conId": 265598, "symbol": "AAPL"}',
        ),
        "raw_market_events": RawMarketEvent(
            session_id="sess-1",
            event_id="evt-1",
            instrument_key=INSTRUMENT_KEY,
            exchange_ts=EXCHANGE_TS,
            receipt_ts=SNAPSHOT_TS,
            canonical_ts=SNAPSHOT_TS,
            field_name="bid",
            value=190.5,
            trade_date=TRADE_DATE,
            underlying="AAPL",
        ),
        "market_state_snapshots": MarketStateSnapshot(
            snapshot_ts=SNAPSHOT_TS,
            instrument_key=INSTRUMENT_KEY,
            reference_spot=190.5,
            bid=190.4,
            ask=190.6,
            last=190.5,
            spread_pct=0.001,
            reference_type="mid",
            flags=("open",),
            completeness=1.0,
            trade_date=TRADE_DATE,
            underlying="AAPL",
            provenance=make_stamp(),
        ),
        "forward_curve": ForwardCurvePoint(
            snapshot_ts=SNAPSHOT_TS,
            underlying="AAPL",
            maturity_years=0.25,
            expiry_date=EXPIRY,
            day_count="ACT/365",
            forward=191.0,
            diagnostics=ForwardDiagnostics(
                method="parity", candidate_count=5, residual_mad=0.01, quality_label="good"
            ),
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "iv_points": IvPoint(
            snapshot_ts=SNAPSHOT_TS,
            contract_key=CONTRACT_KEY,
            iv=0.2,
            k=0.0,
            total_variance=0.01,
            solver_version="iv-1",
            diagnostics=IvDiagnostics(
                converged=True, iterations=4, residual=1e-9, status="converged"
            ),
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "surface_parameters": SurfaceParameters(
            snapshot_ts=SNAPSHOT_TS,
            underlying="AAPL",
            maturity_years=0.25,
            model_version="svi-1",
            svi_a=0.04,
            svi_b=0.10,
            svi_rho=-0.30,
            svi_m=0.0,
            svi_sigma=0.20,
            expiry_date=EXPIRY,
            day_count="ACT/365",
            diagnostics=SurfaceFitDiagnostics(rmse=0.001, n_points=5, arb_free=True),
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "surface_grid": SurfaceGrid(
            snapshot_ts=SNAPSHOT_TS,
            underlying="AAPL",
            maturity_years=0.25,
            moneyness_bucket=0.0,
            model_version="svi-1",
            total_variance=0.01,
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "pricing_results": PricingResult(
            snapshot_ts=SNAPSHOT_TS,
            contract_key=CONTRACT_KEY,
            pricer_version="px-1",
            price=5.0,
            delta=0.5,
            gamma=0.02,
            vega=0.1,
            theta=-0.01,
            rho=0.03,
            cash_delta=50.0,
            cash_gamma=2.0,
            cash_vega=10.0,
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "positions": Position(
            valuation_ts=SNAPSHOT_TS,
            portfolio_id="pf-1",
            contract_key=CONTRACT_KEY,
            quantity=10.0,
            source="record",
        ),
        "risk_aggregates": RiskAggregate(
            valuation_ts=SNAPSHOT_TS,
            portfolio_id="pf-1",
            group_key="AAPL",
            net_delta=5.0,
            net_gamma=0.2,
            net_vega=1.0,
            net_theta=-0.1,
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "scenario_results": ScenarioResult(
            valuation_ts=SNAPSHOT_TS,
            portfolio_id="pf-1",
            scenario_id="spot-down-5",
            contract_key=CONTRACT_KEY,
            spot_shock=-0.05,
            vol_shock=0.0,
            time_shock=0.0,
            pnl=-25.0,
            scenario_version="scn-1",
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "qc_results": QcResult(
            run_id="run-1",
            check_name="forward_stability",
            target_key="AAPL:0.25",
            run_ts=SNAPSHOT_TS,
            status="pass",
            severity="info",
            measured_value=0.001,
            threshold_version="qc-1",
            context='{"note": "within tolerance"}',
        ),
    }
