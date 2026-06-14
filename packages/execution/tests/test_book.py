"""Folding fills into the booked position set.

The book is the running result of fills: partial fills accumulate exactly, a net-zero
contract is a closed position (absent from the live book but never erased from the ledger),
and the result is the ``PositionSet`` shape risk already reads, sourced as ``"booked"``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import pytest
from algotrading.execution import (
    BOOKED,
    Fill,
    FillsLedgerError,
    InMemoryFillsLedger,
    booked_position_set,
    position_set_from_fills,
)


def test_partial_fills_on_one_contract_accumulate_exactly(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    # Three partial fills of the same contract make one position of the summed quantity.
    fills = [
        make_fill(fill_id="p1", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("2")),
        make_fill(fill_id="p2", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("3")),
        make_fill(fill_id="p3", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("1")),
    ]
    book = position_set_from_fills(fills, source_ts=fill_ts)
    assert len(book.positions) == 1
    (pos,) = book.positions
    assert pos.contract_key == "SX5E|OPT|C|4400"
    assert pos.quantity == Decimal("6")
    assert book.source == BOOKED
    assert book.source_ts == fill_ts


def test_buys_and_sells_net_with_sign(make_fill: Callable[..., Fill], fill_ts: datetime) -> None:
    fills = [
        make_fill(fill_id="b", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("5")),
        make_fill(fill_id="s", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("-2")),
    ]
    (pos,) = position_set_from_fills(fills, source_ts=fill_ts).positions
    assert pos.quantity == Decimal("3")


def test_a_contract_that_nets_to_zero_is_a_closed_position(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    # Fully closing a position drops it from the live book — but both fills stay in the ledger.
    fills = [
        make_fill(fill_id="open", contract_key="SX5E|OPT|P|4200", signed_qty=Decimal("4")),
        make_fill(fill_id="close", contract_key="SX5E|OPT|P|4200", signed_qty=Decimal("-4")),
    ]
    book = position_set_from_fills(fills, source_ts=fill_ts)
    assert book.positions == ()


def test_positions_come_out_ordered_by_contract_key(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    fills = [
        make_fill(fill_id="z", contract_key="SX5E|OPT|P|4200", signed_qty=Decimal("1")),
        make_fill(fill_id="a", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("1")),
    ]
    book = position_set_from_fills(fills, source_ts=fill_ts)
    assert [p.contract_key for p in book.positions] == ["SX5E|OPT|C|4400", "SX5E|OPT|P|4200"]


def test_broker_contract_id_rides_through_when_fills_agree(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    fills = [
        make_fill(fill_id="1", signed_qty=Decimal("1"), broker_contract_id="conid-9"),
        make_fill(fill_id="2", signed_qty=Decimal("1"), broker_contract_id="conid-9"),
    ]
    (pos,) = position_set_from_fills(fills, source_ts=fill_ts).positions
    assert pos.broker_contract_id == "conid-9"


def test_conflicting_broker_ids_for_one_contract_is_a_labelled_error(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    # No silent reconciliation: two different broker ids for one contract is a labelled failure.
    fills = [
        make_fill(fill_id="1", signed_qty=Decimal("1"), broker_contract_id="conid-9"),
        make_fill(fill_id="2", signed_qty=Decimal("1"), broker_contract_id="conid-OTHER"),
    ]
    with pytest.raises(FillsLedgerError) as exc:
        position_set_from_fills(fills, source_ts=fill_ts)
    assert exc.value.field == "broker_contract_id"


def test_booked_position_set_reads_the_ledger_then_folds(
    make_fill: Callable[..., Fill],
    fill_ts: datetime,
) -> None:
    ledger = InMemoryFillsLedger()
    ledger.append_many(
        [
            make_fill(fill_id="1", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("2")),
            make_fill(fill_id="2", contract_key="SX5E|OPT|C|4400", signed_qty=Decimal("1")),
        ]
    )
    book = booked_position_set(ledger, source_ts=fill_ts)
    (pos,) = book.positions
    assert pos.quantity == Decimal("3")


def test_an_empty_ledger_is_a_flat_book(make_fill: Callable[..., Fill], fill_ts: datetime) -> None:
    book = booked_position_set(InMemoryFillsLedger(), source_ts=fill_ts)
    assert book.positions == ()
    assert book.source == BOOKED
