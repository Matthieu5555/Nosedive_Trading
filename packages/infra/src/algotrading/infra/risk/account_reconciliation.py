from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from algotrading.core.log import get_logger
from algotrading.infra.contracts import (
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)

from .positions import Position, PositionSet

ACCOUNT_RECON_TOLERANCE_VERSION = "account-recon-1.0.0"

RECON_STATUS_MATCH = "match"
RECON_STATUS_BREAK = "break"
RECON_STATUS_BROKER_ONLY = "broker_only"
RECON_STATUS_BOOK_ONLY = "book_only"

_log = get_logger(__name__)


class BookFill(Protocol):

    @property
    def contract_key(self) -> str: ...

    @property
    def signed_qty(self) -> Decimal: ...

    @property
    def broker_contract_id(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class AccountReconciliationTolerance:

    version: str
    quantity_abs: float
    cash_abs: float


DEFAULT_ACCOUNT_RECON_TOLERANCE = AccountReconciliationTolerance(
    version=ACCOUNT_RECON_TOLERANCE_VERSION, quantity_abs=1e-6, cash_abs=1e-2
)


@dataclass(frozen=True, slots=True)
class PositionReconLine:

    join_key: str
    broker_contract_key: str | None
    book_contract_key: str | None
    broker_quantity: float | None
    book_quantity: float | None
    quantity_diff: float | None
    status: str
    threshold: float
    threshold_version: str


@dataclass(frozen=True, slots=True)
class CashReconLine:

    currency: str
    broker_cash_balance: float | None
    broker_settled_cash: float | None
    broker_net_liquidation: float | None
    status: str
    threshold_version: str


@dataclass(frozen=True, slots=True)
class FillReconLine:

    join_key: str
    broker_contract_key: str | None
    book_contract_key: str | None
    broker_signed_quantity: float | None
    book_signed_quantity: float | None
    quantity_diff: float | None
    status: str
    threshold: float
    threshold_version: str


@dataclass(frozen=True, slots=True)
class ReconStatusCounts:

    match: int
    breaks: int
    broker_only: int
    book_only: int


@dataclass(frozen=True, slots=True)
class AccountReconciliationReport:

    account_id: str
    as_of_ts: datetime
    book_source: str
    book_source_ts: datetime
    position_lines: tuple[PositionReconLine, ...]
    cash_lines: tuple[CashReconLine, ...]
    fill_lines: tuple[FillReconLine, ...]
    position_counts: ReconStatusCounts
    cash_counts: ReconStatusCounts
    fill_counts: ReconStatusCounts
    threshold_version: str

    @property
    def ok(self) -> bool:
        return all(
            counts.breaks == 0 and counts.broker_only == 0 and counts.book_only == 0
            for counts in (self.position_counts, self.fill_counts)
        )


def _count_statuses(statuses: Iterable[str]) -> ReconStatusCounts:
    tally = {
        RECON_STATUS_MATCH: 0,
        RECON_STATUS_BREAK: 0,
        RECON_STATUS_BROKER_ONLY: 0,
        RECON_STATUS_BOOK_ONLY: 0,
    }
    for status in statuses:
        tally[status] += 1
    return ReconStatusCounts(
        match=tally[RECON_STATUS_MATCH],
        breaks=tally[RECON_STATUS_BREAK],
        broker_only=tally[RECON_STATUS_BROKER_ONLY],
        book_only=tally[RECON_STATUS_BOOK_ONLY],
    )


def _quantity_status(diff: float, threshold: float) -> str:
    if not math.isfinite(diff) or abs(diff) > threshold:
        return RECON_STATUS_BREAK
    return RECON_STATUS_MATCH


@dataclass(frozen=True, slots=True)
class _Side:

    join_key: str
    contract_key: str
    quantity: float


@dataclass(frozen=True, slots=True)
class _Pair:

    join_key: str
    broker: _Side | None
    book: _Side | None


def _align_sides(
    broker_sides: Iterable[_Side], book_sides: Iterable[_Side]
) -> tuple[_Pair, ...]:
    broker_by_primary = {side.join_key: side for side in broker_sides}
    book_by_primary = {side.join_key: side for side in book_sides}
    broker_by_contract = {side.contract_key: side for side in broker_by_primary.values()}
    matched_broker_keys: set[str] = set()
    pairs: list[_Pair] = []
    for book in sorted(book_by_primary.values(), key=lambda s: s.join_key):
        broker = broker_by_primary.get(book.join_key)
        if broker is None and book.join_key == book.contract_key:
            broker = broker_by_contract.get(book.contract_key)
        if broker is not None:
            matched_broker_keys.add(broker.join_key)
            pairs.append(_Pair(join_key=broker.join_key, broker=broker, book=book))
        else:
            pairs.append(_Pair(join_key=book.join_key, broker=None, book=book))
    for broker in sorted(broker_by_primary.values(), key=lambda s: s.join_key):
        if broker.join_key not in matched_broker_keys:
            pairs.append(_Pair(join_key=broker.join_key, broker=broker, book=None))
    return tuple(sorted(pairs, key=lambda p: p.join_key))


def _broker_position_side(position: BrokerPosition) -> _Side:
    return _Side(
        join_key=str(position.conid),
        contract_key=position.contract_key,
        quantity=position.quantity,
    )


def _book_position_side(position: Position) -> _Side:
    join_key = (
        position.broker_contract_id
        if position.broker_contract_id is not None
        else position.contract_key
    )
    return _Side(
        join_key=join_key,
        contract_key=position.contract_key,
        quantity=float(position.quantity),
    )


def _reconcile_positions(
    broker_positions: Iterable[BrokerPosition],
    book_positions: Iterable[Position],
    *,
    tolerance: AccountReconciliationTolerance,
) -> tuple[PositionReconLine, ...]:
    pairs = _align_sides(
        [_broker_position_side(pos) for pos in broker_positions],
        [_book_position_side(pos) for pos in book_positions],
    )
    lines: list[PositionReconLine] = []
    for pair in pairs:
        broker = pair.broker
        book = pair.book
        if broker is not None and book is not None:
            diff = broker.quantity - book.quantity
            lines.append(
                PositionReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=broker.contract_key,
                    book_contract_key=book.contract_key,
                    broker_quantity=broker.quantity,
                    book_quantity=book.quantity,
                    quantity_diff=diff,
                    status=_quantity_status(diff, tolerance.quantity_abs),
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
        elif broker is not None:
            lines.append(
                PositionReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=broker.contract_key,
                    book_contract_key=None,
                    broker_quantity=broker.quantity,
                    book_quantity=None,
                    quantity_diff=None,
                    status=RECON_STATUS_BROKER_ONLY,
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
        else:
            assert book is not None
            lines.append(
                PositionReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=None,
                    book_contract_key=book.contract_key,
                    broker_quantity=None,
                    book_quantity=book.quantity,
                    quantity_diff=None,
                    status=RECON_STATUS_BOOK_ONLY,
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
    return tuple(lines)


def _cash_status(balance: BrokerCashBalance) -> str:
    finite = (
        math.isfinite(balance.cash_balance)
        and math.isfinite(balance.settled_cash)
        and math.isfinite(balance.net_liquidation)
    )
    if not finite:
        return RECON_STATUS_BREAK
    return RECON_STATUS_BROKER_ONLY


def _reconcile_cash(
    cash_balances: Iterable[BrokerCashBalance],
    *,
    tolerance: AccountReconciliationTolerance,
) -> tuple[CashReconLine, ...]:
    lines: list[CashReconLine] = []
    for balance in sorted(cash_balances, key=lambda row: row.currency):
        lines.append(
            CashReconLine(
                currency=balance.currency,
                broker_cash_balance=balance.cash_balance,
                broker_settled_cash=balance.settled_cash,
                broker_net_liquidation=balance.net_liquidation,
                status=_cash_status(balance),
                threshold_version=tolerance.version,
            )
        )
    return tuple(lines)


def _signed_broker_fill_quantity(fill: BrokerFill) -> float:
    magnitude = abs(fill.quantity)
    return magnitude if fill.side == "BUY" else -magnitude


def _broker_fill_sides(broker_fills: Iterable[BrokerFill]) -> tuple[_Side, ...]:
    totals: dict[str, tuple[str, float]] = {}
    for fill in broker_fills:
        key = str(fill.conid)
        prior = totals.get(key)
        running = (
            _signed_broker_fill_quantity(fill)
            if prior is None
            else prior[1] + _signed_broker_fill_quantity(fill)
        )
        totals[key] = (fill.contract_key, running)
    return tuple(
        _Side(join_key=key, contract_key=contract_key, quantity=running)
        for key, (contract_key, running) in totals.items()
    )


def _book_fill_sides(book_fills: Iterable[BookFill]) -> tuple[_Side, ...]:
    totals: dict[str, tuple[str, Decimal]] = {}
    for fill in book_fills:
        broker_id = fill.broker_contract_id
        key = broker_id if broker_id is not None else fill.contract_key
        prior = totals.get(key)
        running = fill.signed_qty if prior is None else prior[1] + fill.signed_qty
        totals[key] = (fill.contract_key, running)
    return tuple(
        _Side(join_key=key, contract_key=contract_key, quantity=float(running))
        for key, (contract_key, running) in totals.items()
    )


def _reconcile_fills(
    broker_fills: Iterable[BrokerFill],
    book_fills: Iterable[BookFill],
    *,
    tolerance: AccountReconciliationTolerance,
) -> tuple[FillReconLine, ...]:
    pairs = _align_sides(_broker_fill_sides(broker_fills), _book_fill_sides(book_fills))
    lines: list[FillReconLine] = []
    for pair in pairs:
        broker = pair.broker
        book = pair.book
        if broker is not None and book is not None:
            diff = broker.quantity - book.quantity
            lines.append(
                FillReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=broker.contract_key,
                    book_contract_key=book.contract_key,
                    broker_signed_quantity=broker.quantity,
                    book_signed_quantity=book.quantity,
                    quantity_diff=diff,
                    status=_quantity_status(diff, tolerance.quantity_abs),
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
        elif broker is not None:
            lines.append(
                FillReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=broker.contract_key,
                    book_contract_key=None,
                    broker_signed_quantity=broker.quantity,
                    book_signed_quantity=None,
                    quantity_diff=None,
                    status=RECON_STATUS_BROKER_ONLY,
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
        else:
            assert book is not None
            lines.append(
                FillReconLine(
                    join_key=pair.join_key,
                    broker_contract_key=None,
                    book_contract_key=book.contract_key,
                    broker_signed_quantity=None,
                    book_signed_quantity=book.quantity,
                    quantity_diff=None,
                    status=RECON_STATUS_BOOK_ONLY,
                    threshold=tolerance.quantity_abs,
                    threshold_version=tolerance.version,
                )
            )
    return tuple(lines)


def reconcile_account(
    snapshot: BrokerAccountSnapshot,
    position_set: PositionSet,
    *,
    book_fills: Iterable[BookFill] = (),
    tolerance: AccountReconciliationTolerance = DEFAULT_ACCOUNT_RECON_TOLERANCE,
) -> AccountReconciliationReport:
    position_lines = _reconcile_positions(
        snapshot.positions, position_set.positions, tolerance=tolerance
    )
    cash_lines = _reconcile_cash(snapshot.cash_balances, tolerance=tolerance)
    fill_lines = _reconcile_fills(snapshot.fills, book_fills, tolerance=tolerance)
    position_counts = _count_statuses(line.status for line in position_lines)
    cash_counts = _count_statuses(line.status for line in cash_lines)
    fill_counts = _count_statuses(line.status for line in fill_lines)
    report = AccountReconciliationReport(
        account_id=snapshot.account_id,
        as_of_ts=snapshot.as_of_ts,
        book_source=position_set.source,
        book_source_ts=position_set.source_ts,
        position_lines=position_lines,
        cash_lines=cash_lines,
        fill_lines=fill_lines,
        position_counts=position_counts,
        cash_counts=cash_counts,
        fill_counts=fill_counts,
        threshold_version=tolerance.version,
    )
    if not report.ok:
        _log.warning(
            "broker account reconciliation breaks",
            extra={
                "account_id": snapshot.account_id,
                "position_breaks": position_counts.breaks,
                "position_broker_only": position_counts.broker_only,
                "position_book_only": position_counts.book_only,
                "fill_breaks": fill_counts.breaks,
                "fill_broker_only": fill_counts.broker_only,
                "fill_book_only": fill_counts.book_only,
            },
        )
    return report
