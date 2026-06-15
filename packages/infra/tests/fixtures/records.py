"""One valid baseline record per table family.

These are the canonical "good" records: every table family, fully populated,
passing validation, ready to write. Two jobs:

* the storage round-trip test iterates all of them (write → read → equal);
* the rejection tests take one and break a single field, so each malformed case
  differs from a known-good record in exactly one way.

They are built once and returned as a fresh dict each call, so a test that mutates
a copy cannot disturb another test.

``make_record`` is the keyword-override door onto the same baselines: tests that need
"one good record with a few fields bent" build it as baseline + explicit overrides
instead of re-enumerating every contract field. A contract gaining a field then needs
editing here only — never in the consuming test files (M11).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from typing import Any

from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, source_ref, stamp
from algotrading.infra.contracts import (
    Basket,
    BasketLeg,
    BookGreeks,
    ConstituentCaptureOutcome,
    DailyBar,
    ForwardCurvePoint,
    ForwardDiagnostics,
    IndexConstituent,
    InstrumentKey,
    InstrumentMaster,
    IvDiagnostics,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    ProjectedOptionAnalytics,
    QcResult,
    RawMarketEvent,
    RiskAggregate,
    ScenarioAttribution,
    ScenarioResult,
    StrategySignal,
    SurfaceFitDiagnostics,
    SurfaceGrid,
    SurfaceParameters,
    TriageRecord,
)

CODE_VERSION = "0.1.0-fixture"
CONFIG_HASH = {"cfg": "cfg-hash-0"}

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
    *,
    calc_ts: datetime = CALC_TS,
    code_version: str = CODE_VERSION,
    config_hashes: dict[str, str] | None = None,
    source_timestamps: tuple[datetime, ...] = (SNAPSHOT_TS,),
) -> ProvenanceStamp:
    """A valid provenance stamp pointing at the given source records.

    Every ``stamp`` field is overridable so hash-pinned suites (the determinism
    goldens) can pass their exact historical parameters and keep their stamp hashes
    byte-identical; everything else rides the fixture defaults.
    """
    return stamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes=CONFIG_HASH if config_hashes is None else config_hashes,
        source_records=source_records,
        source_timestamps=source_timestamps,
    )


def make_record(table: str, **overrides: Any) -> Any:
    """The baseline record for ``table`` with the named fields replaced.

    Overrides go through ``dataclasses.replace``, so an unknown field name fails
    loudly, and deliberately *invalid* values pass through unchecked — exactly what
    the "break one field" rejection tests need (contract validation lives at the
    write door, not in the constructors).
    """
    return dataclasses.replace(baseline_records()[table], **overrides)


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
            forward_price=191.0,
            diagnostics=ForwardDiagnostics(
                method="parity", candidate_count=5, residual_mad=0.01, quality_label="good"
            ),
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "iv_points": IvPoint(
            snapshot_ts=SNAPSHOT_TS,
            contract_key=CONTRACT_KEY,
            implied_vol=0.2,
            log_moneyness=0.0,
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
            dollar_delta=50.0,
            dollar_gamma=2.0,
            dollar_vega=10.0,
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
            scenario_pnl=-25.0,
            scenario_version="scn-1",
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "qc_results": QcResult(
            run_id="run-1",
            check_name="forward_stability",
            target_key="AAPL:0.25",
            run_ts=SNAPSHOT_TS,
            qc_status="pass",
            severity="info",
            measured_value=0.001,
            threshold_version="qc-1",
            context='{"note": "within tolerance"}',
        ),
        "constituent_capture_outcomes": ConstituentCaptureOutcome(
            run_id="run-1",
            run_ts=SNAPSHOT_TS,
            index="SX5E",
            underlying="ASML",
            outcome="captured",
            rank=1,
            weight=0.12,
            n_options=6,
            detail="captured 6 option leg(s)",
        ),
        "strategy_signals": StrategySignal(
            snapshot_ts=SNAPSHOT_TS,
            provider="IBKR",
            underlying="SX5E",
            signal_kind="implied_correlation",
            subject="SX5E",
            tenor_label="3m",
            value=0.62,
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "daily_bar": DailyBar(
            provider="IBKR",
            underlying="AAPL",
            trade_date=TRADE_DATE,
            open=189.0,
            high=191.5,
            low=188.5,
            close=190.5,
            volume=1_000_000.0,
            bar_type="1d-TRADES",
            source="cp-rest-history",
            provenance=make_stamp(),
        ),
        "index_constituents": IndexConstituent(
            index="SPX",
            constituent="AAPL",
            effective_add_date=date(2020, 3, 23),
            effective_remove_date=None,
            knowledge_date=TRADE_DATE,
            vendor="siblis",
            weight=0.061,
        ),
        "projected_option_analytics": ProjectedOptionAnalytics(
            snapshot_ts=SNAPSHOT_TS,
            provider="IBKR",
            underlying="AAPL",
            tenor_label="1m",
            maturity_years=0.0833,
            delta_band="30dc",
            target_delta=0.30,
            log_moneyness=0.05,
            strike=200.0,
            forward_price=191.0,
            implied_vol=0.22,
            total_variance=0.004,
            price=1.25,
            delta=0.30,
            gamma=0.03,
            vega=0.12,
            theta=-0.02,
            rho=0.01,
            dollar_delta=5730.0,
            dollar_gamma=109.7,
            dollar_vega=12.0,
            dollar_delta_unit="per $1 underlying move",
            dollar_gamma_unit="per 1% underlying move",
            dollar_vega_unit="per 1 vol point",
            model_version="svi-1",
            pricer_version="px-1",
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
            dollar_theta=-2.0,
            dollar_rho=1.0,
            dollar_theta_unit="per calendar day",
            dollar_rho_unit="per 1% rate move",
        ),
        "baskets": Basket(
            basket_id="rr-aapl-1m",
            trade_date=TRADE_DATE,
            underlying="AAPL",
            legs=(
                BasketLeg("option", "long", 1.0, "AAPL", tenor_label="1m", delta_band="30dc"),
                BasketLeg("option", "short", -1.0, "AAPL", tenor_label="1m", delta_band="30dp"),
                BasketLeg("stock", "long", 25.0, "AAPL"),
            ),
            provider="IBKR",
        ),
        "scenario_attributions": ScenarioAttribution(
            valuation_ts=SNAPSHOT_TS,
            portfolio_id="pf-1",
            scenario_id="spot-down-5",
            contract_key=CONTRACT_KEY,
            level="position",
            spot_shock=-0.05,
            vol_shock=0.0,
            time_shock=0.0,
            delta_pnl=-20.0,
            gamma_pnl=-4.0,
            vega_pnl=0.0,
            theta_pnl=0.0,
            approx_pnl=-24.0,
            full_reprice_pnl=-25.0,
            residual=-1.0,
            within_tolerance=True,
            residual_abs_tol=2.0,
            residual_rel_tol=0.1,
            scenario_version="scn-1",
            attribution_version="attr-1",
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "book_greeks": BookGreeks(
            valuation_ts=SNAPSHOT_TS,
            book_id="book-1",
            level="layer",
            layer_label="risk-reversal",
            layer_index=0,
            net_delta=5.0,
            net_gamma=0.2,
            net_vega=1.0,
            net_theta=-0.1,
            dollar_delta=950.0,
            dollar_gamma=36.3,
            dollar_vega=100.0,
            dollar_theta=-10.0,
            dollar_rho=3.0,
            dollar_delta_unit="per $1 underlying move",
            dollar_gamma_unit="per 1% underlying move",
            dollar_vega_unit="per 1 vol point",
            dollar_theta_unit="per calendar day",
            dollar_rho_unit="per 1% rate move",
            composition_version="book-1",
            source_snapshot_ts=SNAPSHOT_TS,
            provenance=make_stamp(),
        ),
        "triage_records": TriageRecord(
            run_id="run-1",
            run_ts=SNAPSHOT_TS,
            underlying="AAPL",
            source="qc",
            name="forward_stability",
            target_key="AAPL:0.25",
            status="fail",
            severity="warn",
            reason_code="FORWARD_JUMP",
            detail="forward 0.25y moved 2.1% vs previous snapshot",
            threshold_version="qc-1",
        ),
    }
