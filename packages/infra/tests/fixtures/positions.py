from __future__ import annotations

from datetime import UTC, datetime

from algotrading.infra.contracts import Position
from algotrading.infra.risk import CONFIDENCE_LOW, CONFIDENCE_OK, ContractValuationInput

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


CALL_100 = _valuation("AAPL|OPT|C|100", underlying="AAPL", option_right="C", strike=100.0)
PUT_100 = _valuation("AAPL|OPT|P|100", underlying="AAPL", option_right="P", strike=100.0)
CALL_105 = _valuation("AAPL|OPT|C|105", underlying="AAPL", option_right="C", strike=105.0)
PUT_95 = _valuation("AAPL|OPT|P|95", underlying="AAPL", option_right="P", strike=95.0)

LOW_CONFIDENCE_CALL = _valuation(
    "AAPL|OPT|C|110", underlying="AAPL", option_right="C", strike=110.0, confidence=CONFIDENCE_LOW
)
EUR_CALL_100 = _valuation(
    "SX5E|OPT|C|100", underlying="SX5E", option_right="C", strike=100.0, currency="EUR"
)

RISK_VALUATIONS: dict[str, ContractValuationInput] = {
    valuation.contract_key: valuation
    for valuation in (CALL_100, PUT_100, CALL_105, PUT_95, LOW_CONFIDENCE_CALL, EUR_CALL_100)
}


def _position(contract_key: str, quantity: float) -> Position:
    return Position(
        valuation_ts=RISK_VALUATION_TS,
        portfolio_id=RISK_PORTFOLIO,
        contract_key=contract_key,
        quantity=quantity,
        source="record",
    )


def risk_positions() -> tuple[Position, ...]:
    return (
        _position(CALL_100.contract_key, 10.0),
        _position(PUT_100.contract_key, -5.0),
        _position(CALL_105.contract_key, 3.0),
    )
