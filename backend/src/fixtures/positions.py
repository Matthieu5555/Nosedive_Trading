"""Named position and valuation fixtures for the risk engine (Workstream D).

D extends the shared fixture library (per ``tasks/TESTING.md``): the risk edge-case
tests bind to these named fixtures rather than inventing inline literals, so the
portfolio under test has one curated home. The market state is the one the
independent oracles were derived against (see ``tests/test_risk.py`` provenance
comments): spot 100, carry 0 (so forward 100), T 0.25, vol 0.20, DF 0.99,
multiplier 100, USD, European — chosen so the at-the-money call price is the
familiar 3.947884 anchor.

The "rogues' gallery" entries here are the ones D's edge cases need: a
low-confidence contract (C flagged the quote) and a non-USD contract for
multi-currency aggregation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from contracts import Position
from risk import CONFIDENCE_LOW, CONFIDENCE_OK, ContractValuationInput

# The shared market state the oracles used. r = -ln(0.99)/0.25; carry 0 => F == spot.
RISK_VALUATION_TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
RISK_SPOT = 100.0
RISK_CARRY = 0.0
RISK_MATURITY_YEARS = 0.25
RISK_VOL = 0.20
RISK_DF = 0.99
RISK_MULTIPLIER = 100.0
RISK_PORTFOLIO = "pf-risk"


def _valuation(
    contract_key: str,
    *,
    underlying: str,
    option_right: str,
    strike: float,
    volatility: float = RISK_VOL,
    multiplier: float = RISK_MULTIPLIER,
    currency: str = "USD",
    confidence: str = CONFIDENCE_OK,
    exercise_style: str = "european",
) -> ContractValuationInput:
    return ContractValuationInput(
        contract_key=contract_key,
        underlying=underlying,
        option_right=option_right,
        exercise_style=exercise_style,
        strike=strike,
        maturity_years=RISK_MATURITY_YEARS,
        spot=RISK_SPOT,
        carry=RISK_CARRY,
        volatility=volatility,
        discount_factor=RISK_DF,
        multiplier=multiplier,
        currency=currency,
        confidence=confidence,
    )


# The three contracts of the pf-risk portfolio, plus an OTM put for delta-sign
# coverage. Keys mirror the canonical "UNDERLYING|OPT|RIGHT|STRIKE" form used across
# the suite.
CALL_100 = _valuation("AAPL|OPT|C|100", underlying="AAPL", option_right="C", strike=100.0)
PUT_100 = _valuation("AAPL|OPT|P|100", underlying="AAPL", option_right="P", strike=100.0)
CALL_105 = _valuation("AAPL|OPT|C|105", underlying="AAPL", option_right="C", strike=105.0)
PUT_95 = _valuation("AAPL|OPT|P|95", underlying="AAPL", option_right="P", strike=95.0)

# Edge fixtures. The low-confidence contract is still priced; its label rides through.
LOW_CONFIDENCE_CALL = _valuation(
    "AAPL|OPT|C|110", underlying="AAPL", option_right="C", strike=110.0, confidence=CONFIDENCE_LOW
)
# A non-USD contract for the multi-currency aggregation edge.
EUR_CALL_100 = _valuation(
    "SX5E|OPT|C|100", underlying="SX5E", option_right="C", strike=100.0, currency="EUR"
)

# Keyed lookup the join step uses: contract_key -> its resolved valuation input.
RISK_VALUATIONS: dict[str, ContractValuationInput] = {
    valuation.contract_key: valuation
    for valuation in (CALL_100, PUT_100, CALL_105, PUT_95, LOW_CONFIDENCE_CALL, EUR_CALL_100)
}


def risk_positions() -> tuple[Position, ...]:
    """The pf-risk portfolio: long 10 C100, short 5 P100, long 3 C105.

    A fresh tuple each call so a test that builds on it cannot disturb another.
    """
    return (
        _position(CALL_100.contract_key, 10.0),
        _position(PUT_100.contract_key, -5.0),
        _position(CALL_105.contract_key, 3.0),
    )


def _position(contract_key: str, quantity: float) -> Position:
    return Position(
        valuation_ts=RISK_VALUATION_TS,
        portfolio_id=RISK_PORTFOLIO,
        contract_key=contract_key,
        quantity=quantity,
        source="record",
    )
