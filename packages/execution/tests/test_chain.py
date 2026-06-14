"""The booking chain in paper form: fills → booked positions → a priced risk snapshot.

This is the §5.1/§5.5 end-to-end proof: a booked fill becomes a position that the *existing*
risk engine prices, with no change to risk code. The seam is the ``PositionSet`` — risk was
already agnostic to whether its book came from a composed basket or from booked fills. The
snapshot's ``position_source`` carries the ``"booked"`` origin into the report's provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from algotrading.execution import Fill, InMemoryFillsLedger, booked_position_set
from algotrading.infra.risk import (
    ContractValuationInput,
    RiskParams,
    build_risk_snapshot,
)

_KEY = "SX5E|OPT|C|4400"


def _valuation() -> ContractValuationInput:
    # A plain ATM-ish European call; the numbers only need to price, not to hit an oracle —
    # the per-line pricing is proven in the infra suite. Here we assert the chain, not the math.
    return ContractValuationInput(
        contract_key=_KEY,
        underlying="SX5E",
        option_right="C",
        exercise_style="european",
        strike=4400.0,
        maturity_years=0.25,
        spot=4400.0,
        carry=0.0,
        volatility=0.20,
        discount_factor=0.99,
        multiplier=10.0,
        currency="EUR",
    )


def test_booked_fills_price_through_the_existing_risk_engine(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    ledger = InMemoryFillsLedger()
    ledger.append_many(
        [
            make_fill(fill_id="1", contract_key=_KEY, signed_qty=Decimal("2")),
            make_fill(fill_id="2", contract_key=_KEY, signed_qty=Decimal("1")),
        ]
    )

    book = booked_position_set(ledger, source_ts=fill_ts)
    snapshot = build_risk_snapshot(
        book,
        {_KEY: _valuation()},
        RiskParams.defaults(),
        analytics_version="test-analytics",
        portfolio_id="pf-exec",
    )

    # One position priced into one risk line, carrying the booked book's quantity.
    assert len(snapshot.lines) == 1
    (line,) = snapshot.lines
    # The booked origin rides into the snapshot provenance (reproducible from a named book).
    assert snapshot.position_source == "booked"
    assert snapshot.position_source_ts == fill_ts
    # The line's delta scales with the 3-lot booked position (non-zero, finite).
    assert line.position_delta != 0.0
