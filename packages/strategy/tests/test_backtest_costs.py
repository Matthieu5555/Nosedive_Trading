from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.pricing import price
from algotrading.infra.risk.valuation import ContractValuationInput, pricing_state_for
from algotrading.strategy.backtest import HeldContract, TransactionCostModel
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy

_INDEX = "SX5E"
_CONTRACT_KEY = "SX5E|OPT|P|3800.0000"
_MULTIPLIER = 10.0


def _held(quantity: float) -> HeldContract:
    leg = PutLineStrategy(
        PutLineConfig(
            index=_INDEX, put_tenor="1M", put_delta_band="24dp", line_capacity=10
        )
    ).construct(date(2026, 1, 5), basket_id="seed").legs[0]
    return HeldContract(
        contract_key=_CONTRACT_KEY,
        quantity=quantity,
        expiry=date(2026, 2, 5),
        leg=leg,
    )


def _valuation() -> ContractValuationInput:
    return ContractValuationInput(
        contract_key=_CONTRACT_KEY,
        underlying=_INDEX,
        option_right="P",
        exercise_style="european",
        strike=3800.0,
        maturity_years=30 / 365,
        spot=3900.0,
        carry=0.0,
        volatility=0.20,
        discount_factor=1.0,
        multiplier=_MULTIPLIER,
        currency="EUR",
    )


def test_commission_scales_with_absolute_contract_count() -> None:
    model = TransactionCostModel(commission_per_contract=1.5, slippage_rate=0.0)
    assert model.entry_cost(_held(-3.0), None) == pytest.approx(4.5)


def test_slippage_is_a_fraction_of_priced_notional() -> None:
    model = TransactionCostModel(commission_per_contract=0.0, slippage_rate=0.01)
    valuation = _valuation()
    unit_price = price(pricing_state_for(valuation)).price
    expected = 0.01 * unit_price * _MULTIPLIER * 2.0
    assert model.entry_cost(_held(-2.0), valuation) == pytest.approx(expected, rel=1e-12)


def test_commission_and_slippage_add() -> None:
    model = TransactionCostModel(commission_per_contract=2.0, slippage_rate=0.005)
    valuation = _valuation()
    unit_price = price(pricing_state_for(valuation)).price
    expected = 2.0 * 1.0 + 0.005 * unit_price * _MULTIPLIER * 1.0
    assert model.entry_cost(_held(-1.0), valuation) == pytest.approx(expected, rel=1e-12)


def test_unpriceable_leg_charges_commission_only() -> None:
    model = TransactionCostModel(commission_per_contract=3.0, slippage_rate=0.5)
    assert model.entry_cost(_held(2.0), None) == pytest.approx(6.0)


def test_negative_parameters_are_refused() -> None:
    with pytest.raises(ValueError, match="commission_per_contract"):
        TransactionCostModel(commission_per_contract=-1.0)
    with pytest.raises(ValueError, match="slippage_rate"):
        TransactionCostModel(slippage_rate=-0.01)
