"""Unit tests for the backtest position book — the rolling roll-off and the priced-line join.

The book is a thin ledger over the landed risk engine: it accumulates held contracts, rolls off
expired ones (S2's "one expires daily"), and prices the survivors into landed
:class:`PositionRisk` lines. These tests pin the two behaviours that are the book's own — the
expiry roll-off boundary and the labelled-absence drop of an unpriceable contract — against
hand-built fixtures. Pricing itself is the landed engine's, tested there.
"""

from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.contracts import BasketLeg
from algotrading.infra.risk.valuation import ContractValuationInput
from algotrading.strategy import SignalSnapshot
from algotrading.strategy.backtest import (
    BacktestBook,
    ContractMarks,
    HeldContract,
    InMemoryBacktestData,
)
from algotrading.strategy.backtest.data import BacktestData

_INDEX = "SX5E"


def _leg() -> BasketLeg:
    return BasketLeg(
        instrument_kind="option", side="short", quantity=-1.0,
        underlying=_INDEX, tenor_label="1M", delta_band="24dp", surface_side="put",
    )


def _held(key: str, expiry: date) -> HeldContract:
    return HeldContract(contract_key=key, quantity=-1.0, expiry=expiry, leg=_leg())


def _valuation(key: str, spot: float) -> ContractValuationInput:
    return ContractValuationInput(
        contract_key=key, underlying=_INDEX, option_right="P", exercise_style="european",
        strike=3800.0, maturity_years=30 / 365, spot=spot, carry=0.0, volatility=0.20,
        discount_factor=1.0, multiplier=10.0, currency="EUR",
    )


@pytest.mark.parametrize(
    ("expiry_offset_days", "rolls_off"),
    [
        (-1, True),   # expired yesterday -> rolls off.
        (0, True),    # expires today -> rolls off (expiry <= as_of).
        (1, False),   # expires tomorrow -> survives.
    ],
)
def test_expire_rolls_off_contracts_at_or_before_the_day(
    expiry_offset_days: int, rolls_off: bool
) -> None:
    as_of = date(2026, 1, 20)
    expiry = date(2026, 1, 20 + expiry_offset_days)
    book = BacktestBook()
    book.add([_held("K1", expiry)])
    rolled = book.expire(as_of)
    assert (len(rolled) == 1) is rolls_off
    assert book.open_contract_count == (0.0 if rolls_off else 1.0)


def test_expire_keeps_the_survivors_and_returns_the_rolled() -> None:
    as_of = date(2026, 1, 20)
    book = BacktestBook()
    book.add([
        _held("EXPIRED", date(2026, 1, 19)),
        _held("ALIVE", date(2026, 2, 19)),
    ])
    rolled = book.expire(as_of)
    assert [c.contract_key for c in rolled] == ["EXPIRED"]
    assert [c.contract_key for c in book.held] == ["ALIVE"]


def test_price_drops_an_unpriceable_contract_as_a_labelled_absence() -> None:
    """A held contract with no mark on the day is recorded in ``unpriced``, not given a fake mark."""
    as_of = date(2026, 1, 20)
    book = BacktestBook()
    book.add([_held("PRICED", date(2026, 3, 1)), _held("GAPPED", date(2026, 3, 1))])
    data: BacktestData = InMemoryBacktestData(
        signals_by_day={},
        concrete_by_cell={},
        # Only PRICED has a mark on the day; GAPPED is absent -> dropped, not invented.
        marks_by_contract={"PRICED": ContractMarks(by_day={as_of: _valuation("PRICED", 3900.0)})},
    )
    priced = book.price(data, as_of)
    assert [line.contract_key for line in priced.lines] == ["PRICED"]
    assert priced.unpriced == ("GAPPED",)
    assert set(priced.valuations) == {"PRICED"}


def test_price_of_an_empty_book_is_empty_lines() -> None:
    book = BacktestBook()
    data: BacktestData = InMemoryBacktestData(
        signals_by_day={}, concrete_by_cell={}, marks_by_contract={},
    )
    priced = book.price(data, date(2026, 1, 20))
    assert priced.lines == ()
    assert priced.unpriced == ()


def test_inmemory_signals_absence_is_an_empty_snapshot() -> None:
    """A day with no persisted signal yields an empty snapshot (labelled absence, not a crash)."""
    data = InMemoryBacktestData(signals_by_day={}, concrete_by_cell={}, marks_by_contract={})
    snapshot = data.signals(date(2026, 1, 20))
    assert isinstance(snapshot, SignalSnapshot)
    assert snapshot.readings == ()


def test_inmemory_concretize_absent_cell_is_none() -> None:
    """A grid cell the adapter has no template for concretizes to None (the engine skips the leg)."""
    data = InMemoryBacktestData(signals_by_day={}, concrete_by_cell={}, marks_by_contract={})
    assert data.concretize_leg(_leg(), date(2026, 1, 20)) is None
