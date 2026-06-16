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
    return position_set_from_fills(
        ledger.read(trade_date=trade_date, underlying=underlying),
        source_ts=source_ts,
        source=source,
    )
