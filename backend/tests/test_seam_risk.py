"""D's seams: D -> A (contracts round-trip and stamp) and D -> C (pin the pricer).

D -> A: ``Position``, ``RiskAggregate``, and ``ScenarioResult`` are produced by D's
*real* code, round-trip through A's ``ParquetStore`` equal, and the derived two
carry a provenance stamp that survives storage; a malformed instance of each is
refused by A's write-ahead validation (per ``tasks/TESTING.md``).

D -> C: D builds against C's frozen pricing interface, so a pin of that interface
lives here — a C-side change to the state vector, the Greeks shape, the public
surface, or an entry-point parameter breaks D's suite loudly, not E's (ADR 0004
places the breaking pin in D's suite; C keeps a lighter shape-pin of its own).
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import pricing
from contracts import ContractValidationError, RiskAggregate, ScenarioResult
from fixtures.positions import RISK_VALUATIONS, risk_positions
from pricing import PriceGreeks, PricingState, from_forward, from_spot, price, pricing_result
from provenance import ProvenanceStamp, source_ref, stamp
from risk import (
    RISK_ENGINE_VERSION,
    PositionRisk,
    Scenario,
    aggregate_lines,
    position_risk,
    risk_aggregate,
    scenario_line_pnls,
    scenario_result,
)
from storage import ParquetStore

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)


def _stamp() -> ProvenanceStamp:
    return stamp(
        calc_ts=TS,
        code_version=RISK_ENGINE_VERSION,
        config_hash="cfg-hash-0",
        source_records=(source_ref("market_state_snapshots", TS, "AAPL|OPT|C|100"),),
        source_timestamps=(TS,),
    )


def _lines() -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def make_risk_aggregate() -> RiskAggregate:
    net = aggregate_lines(_lines(), portfolio_id="pf-risk", dimension="underlying")[0]
    return risk_aggregate(net, valuation_ts=TS, source_snapshot_ts=TS, provenance=_stamp())


def make_scenario_result() -> ScenarioResult:
    cell = scenario_line_pnls(_lines(), [Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0)])[0]
    return scenario_result(
        cell, valuation_ts=TS, scenario_version="scn-1", source_snapshot_ts=TS, provenance=_stamp()
    )


# --- D -> A round-trips ------------------------------------------------------
def test_position_round_trips_through_a_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = risk_positions()[0]
    store.write("positions", [record])
    assert store.read("positions") == [record]


@pytest.mark.parametrize(
    "table, factory",
    [("risk_aggregates", make_risk_aggregate), ("scenario_results", make_scenario_result)],
)
def test_derived_contract_round_trips_and_keeps_its_stamp(
    table: str, factory: Callable[[], Any], tmp_path: Path
) -> None:
    store = ParquetStore(tmp_path)
    record = factory()
    store.write(table, [record])
    read_back = store.read(table)
    assert read_back == [record]
    assert read_back[0].provenance.stamp_hash == record.provenance.stamp_hash


# --- D -> A malformed rejection ----------------------------------------------
@pytest.mark.parametrize(
    "table, factory, field",
    [
        ("positions", lambda: risk_positions()[0], "quantity"),
        ("risk_aggregates", make_risk_aggregate, "net_delta"),
        ("scenario_results", make_scenario_result, "pnl"),
    ],
)
def test_malformed_contract_is_rejected_by_a_validation(
    table: str, factory: Callable[[], Any], field: str, tmp_path: Path
) -> None:
    store = ParquetStore(tmp_path)
    malformed = dataclasses.replace(factory(), **{field: math.nan})
    with pytest.raises(ContractValidationError) as info:
        store.write(table, [malformed])
    assert info.value.field == field


# --- D -> C interface pin (a C-side change breaks D's suite here) ------------
def test_pricing_state_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PricingState))
    assert names == (
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "exercise_style", "spot", "carry",
    )


def test_price_greeks_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PriceGreeks))
    assert names == ("price", "delta", "gamma", "vega", "theta", "rho")


def test_pricing_public_surface_covers_what_d_imports() -> None:
    # The names D's risk engine reaches into ``pricing`` for. A drop here is a real
    # interface break for D, surfaced in D's own suite.
    assert {"PriceGreeks", "PricingState", "from_spot", "price", "pricing_result"} <= set(
        pricing.__all__
    )


def test_pricer_entrypoint_signatures_are_frozen() -> None:
    assert set(inspect.signature(from_spot).parameters) == {
        "spot", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "carry", "exercise_style",
    }
    assert set(inspect.signature(from_forward).parameters) == {
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "spot", "exercise_style",
    }
    assert set(inspect.signature(price).parameters) == {"state", "steps"}
    assert set(inspect.signature(pricing_result).parameters) == {
        "state", "greeks", "snapshot_ts", "contract_key", "source_snapshot_ts", "provenance",
    }
