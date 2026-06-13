"""Risk seams: risk -> contracts (round-trip + stamp) and risk -> pricing (pin the pricer).

risk -> contracts: ``Position``, ``RiskAggregate``, and ``ScenarioResult`` are produced
by risk's *real* code, round-trip through M1's ``ParquetStore`` equal, and the derived
two carry a provenance stamp that survives storage; a malformed instance of each is
refused by the contracts' write-ahead validation (per ``tasks/TESTING.md``).

risk -> pricing: the risk engine builds against M2's frozen pricing interface, so a pin
of that interface lives here — an M2-side change to the state vector, the Greeks shape,
the public surface, or an entry-point parameter breaks risk's suite loudly (ADR 0004
places the breaking pin here; pricing keeps a lighter shape-pin of its own).
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import algotrading.infra.pricing as pricing
import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref
from algotrading.infra.contracts import (
    ContractValidationError,
    RiskAggregate,
    ScenarioAttribution,
    ScenarioResult,
)
from algotrading.infra.pricing import (
    PriceGreeks,
    PricingState,
    from_forward,
    from_spot,
    price,
    pricing_result,
)
from algotrading.infra.risk import (
    AttributionConfig,
    PositionRisk,
    Scenario,
    aggregate_lines,
    attribute_book,
    book_attribution_result,
    position_risk,
    risk_aggregate,
    scenario_line_pnls,
    scenario_result,
)
from algotrading.infra.storage import ParquetStore
from fixtures.positions import RISK_VALUATIONS, risk_positions
from fixtures.records import make_stamp

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)


def _stamp() -> ProvenanceStamp:
    return make_stamp((source_ref("market_state_snapshots", TS, "AAPL|OPT|C|100"),))


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


def make_scenario_attribution() -> ScenarioAttribution:
    book = attribute_book(
        _lines(), Scenario("spot_down_5", "spot", -0.05, 0.0, 0.0), AttributionConfig.defaults()
    )
    return book_attribution_result(
        book, valuation_ts=TS, scenario_version="scn-1", source_snapshot_ts=TS, provenance=_stamp()
    )


# --- risk -> contracts round-trips -------------------------------------------
def test_position_round_trips_through_storage(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = risk_positions()[0]
    store.write("positions", [record])
    assert store.read("positions") == [record]


@pytest.mark.parametrize(
    "table, factory",
    [
        ("risk_aggregates", make_risk_aggregate),
        ("scenario_results", make_scenario_result),
        ("scenario_attributions", make_scenario_attribution),
    ],
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


# --- risk -> contracts malformed rejection -----------------------------------
@pytest.mark.parametrize(
    "table, factory, field",
    [
        ("positions", lambda: risk_positions()[0], "quantity"),
        ("risk_aggregates", make_risk_aggregate, "net_delta"),
        ("scenario_results", make_scenario_result, "scenario_pnl"),
        ("scenario_attributions", make_scenario_attribution, "residual"),
    ],
)
def test_malformed_contract_is_rejected_by_validation(
    table: str, factory: Callable[[], Any], field: str, tmp_path: Path
) -> None:
    store = ParquetStore(tmp_path)
    malformed = dataclasses.replace(factory(), **{field: math.nan})
    with pytest.raises(ContractValidationError) as info:
        store.write(table, [malformed])
    assert info.value.field == field


# --- risk -> pricing interface pin (an M2-side change breaks risk's suite here) ----
def test_pricing_state_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PricingState))
    assert names == (
        "forward", "strike", "maturity_years", "volatility", "discount_factor",
        "option_right", "exercise_style", "spot", "carry",
    )


def test_price_greeks_shape_is_frozen() -> None:
    names = tuple(f.name for f in dataclasses.fields(PriceGreeks))
    assert names == (
        "price", "delta", "gamma", "vega", "theta", "rho",
        # Second-order set (TARGET §7.2), appended with 0.0 defaults.
        "vanna", "volga", "charm",
    )


def test_pricing_public_surface_covers_what_risk_imports() -> None:
    # The names the risk engine reaches into ``pricing`` for. A drop here is a real
    # interface break for risk, surfaced in risk's own suite.
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
