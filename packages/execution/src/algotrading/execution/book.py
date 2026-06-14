"""Fold fills into the booked position set the risk engine reads.

A position is the running result of fills (TARGET §5.5), never an intention. This module is
the *read* side of the booking chain: group the ledger's fills by their concrete
``contract_key`` and sum the signed quantities exactly (``Decimal``), producing the same
:class:`~algotrading.infra.risk.PositionSet` shape :func:`build_risk_snapshot` already
consumes. A contract whose fills net to zero is a *closed* position — absent from the live
book (a :class:`~algotrading.infra.risk.Position` is a non-zero holding) — while every fill
stays in the ledger for audit.

The seam is the one ``risk.positions`` already named: ``hypothetical_positions`` is "the seam
a live broker-positions source will later mirror". This is that source for the paper book —
the ``source`` is ``"booked"`` instead of ``"hypothetical"``, and risk reads it unchanged. No
risk code is touched; the engine was already agnostic to where its ``PositionSet`` comes from.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from algotrading.infra.risk import Position, PositionSet

from .fills import Fill
from .ledger import FillsLedger, FillsLedgerError

BOOKED = "booked"


def position_set_from_fills(
    fills: Iterable[Fill],
    *,
    source_ts: datetime,
    source: str = BOOKED,
) -> PositionSet:
    """Fold fills into the booked :class:`PositionSet` — pure, deterministic.

    Quantities for the same ``contract_key`` accumulate exactly (partial fills add up). A
    contract that nets to zero is dropped (a closed position is not a live holding). The
    ``broker_contract_id`` rides through when the fills agree; two fills claiming *different*
    non-null broker ids for one contract is a labelled :class:`FillsLedgerError`, never a
    silent pick. Positions come out ordered by ``contract_key`` so the book is reproducible.
    """
    totals: dict[str, Decimal] = {}
    broker_ids: dict[str, str | None] = {}
    for fill in fills:
        key = fill.contract_key
        totals[key] = totals.get(key, Decimal(0)) + fill.signed_qty
        if fill.broker_contract_id is not None:
            existing = broker_ids.get(key)
            if existing is not None and existing != fill.broker_contract_id:
                raise FillsLedgerError(
                    f"conflicting broker_contract_id for {key!r}: "
                    f"{existing!r} vs {fill.broker_contract_id!r}",
                    field="broker_contract_id",
                    value=(existing, fill.broker_contract_id),
                )
            broker_ids[key] = fill.broker_contract_id

    positions = tuple(
        Position(
            contract_key=key,
            quantity=totals[key],
            broker_contract_id=broker_ids.get(key),
        )
        for key in sorted(totals)
        if totals[key] != 0
    )
    return PositionSet(positions=positions, source=source, source_ts=source_ts)


def booked_position_set(
    ledger: FillsLedger,
    *,
    source_ts: datetime,
    source: str = BOOKED,
    trade_date: date | None = None,
    underlying: str | None = None,
) -> PositionSet:
    """Read the ledger (optionally narrowed) and fold it into the booked position set.

    The convenience entry point for the chain: ``ledger → fills → PositionSet`` that a risk
    run then prices via :func:`build_risk_snapshot`.
    """
    return position_set_from_fills(
        ledger.read(trade_date=trade_date, underlying=underlying),
        source_ts=source_ts,
        source=source,
    )
