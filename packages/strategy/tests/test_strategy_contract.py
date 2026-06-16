from __future__ import annotations

import pytest
from algotrading.strategy import (
    GreekSign,
    IntendedGreeks,
    SignalKind,
    StrategyContract,
    StrategyContractError,
)

_VALID_GREEKS = IntendedGreeks(
    delta=GreekSign.FLAT, gamma=GreekSign.LONG, vega=GreekSign.LONG, theta=GreekSign.SHORT
)


def _contract(**overrides: object) -> StrategyContract:
    fields: dict[str, object] = {
        "strategy_id": "S1",
        "premium_harvested": "correlation premium: index IV rich vs constituent IVs",
        "signal": SignalKind.IMPLIED_CORRELATION,
        "intended_greeks": _VALID_GREEKS,
        "kill_condition": "single names go quiet together — theta bleed",
    }
    fields.update(overrides)
    return StrategyContract(**fields)  # type: ignore[arg-type]


def test_valid_contract_carries_all_four_columns() -> None:
    contract = _contract()
    assert contract.strategy_id == "S1"
    assert contract.premium_harvested.startswith("correlation premium")
    assert contract.signal is SignalKind.IMPLIED_CORRELATION
    assert contract.intended_greeks == _VALID_GREEKS
    assert "quiet" in contract.kill_condition


def test_intended_greeks_are_signed_directions_not_magnitudes() -> None:
    greeks = _contract().intended_greeks
    assert greeks.delta is GreekSign.FLAT
    assert greeks.gamma is GreekSign.LONG
    assert greeks.vega is GreekSign.LONG
    assert greeks.theta is GreekSign.SHORT


def test_contract_is_frozen() -> None:
    contract = _contract()
    with pytest.raises((AttributeError, TypeError)):
        contract.strategy_id = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_id", ""),
        ("strategy_id", "   "),
        ("premium_harvested", ""),
        ("kill_condition", ""),
        ("kill_condition", "   "),
    ],
)
def test_empty_required_text_is_rejected_with_the_offending_value(field: str, value: str) -> None:
    with pytest.raises(StrategyContractError) as exc:
        _contract(**{field: value})
    assert exc.value.field == field
    assert exc.value.value == value


def test_signal_kinds_cover_the_book_triggers() -> None:
    declared = {kind.value for kind in SignalKind}
    expected = {
        "implied_correlation",
        "iv_vs_realized",
        "iv_rank",
        "term_structure_slope",
        "range_premium",
    }
    assert declared == expected
