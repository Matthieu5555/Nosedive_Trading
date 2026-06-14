"""Unit tests for the typed ``StrategyContract`` (the four §3 columns as data).

Expected values are derived from the spec (TARGET §1/§3) and the validation rules in
``contract.py``, never from running the constructor: a contract with an empty premium or no
declared kill condition is malformed *by definition*, and the test states that independently.
"""

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
    # The §3 S1 row, transcribed: the four columns are exactly what the record holds.
    contract = _contract()
    assert contract.strategy_id == "S1"
    assert contract.premium_harvested.startswith("correlation premium")
    assert contract.signal is SignalKind.IMPLIED_CORRELATION
    assert contract.intended_greeks == _VALID_GREEKS
    assert "quiet" in contract.kill_condition


def test_intended_greeks_are_signed_directions_not_magnitudes() -> None:
    # S1's intended profile (§3): ~0 net delta, long gamma & vega. The contract records the
    # SIGN of each, the thing attribution checks P&L against — not a sizing magnitude.
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
    # A contract with no named premium / no death mode / no identity is malformed by §1's
    # definition ("a strategy NAMES the premium ... and its kill condition").
    with pytest.raises(StrategyContractError) as exc:
        _contract(**{field: value})
    assert exc.value.field == field
    assert exc.value.value == value


def test_signal_kinds_cover_the_book_triggers() -> None:
    # The §3 entry triggers across S1–S5 — each must be a nameable SignalKind so a strategy
    # declares the one it reads (derived from the §3 table, not from the enum).
    declared = {kind.value for kind in SignalKind}
    expected = {
        "implied_correlation",  # S1
        "iv_vs_realized",       # S2, S3
        "iv_rank",              # S3
        "term_structure_slope",  # S5
        "range_premium",        # S4
    }
    assert declared == expected
