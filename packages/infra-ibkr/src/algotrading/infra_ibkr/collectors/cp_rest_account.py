from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from algotrading.infra.collectors.transport_seam import SupportsRestGet
from algotrading.infra.contracts import (
    FILL_SIDES,
    BrokerAccountSnapshot,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
)
from pydantic import ValidationError

from .cp_rest_account_wire import (
    LedgerRow,
    PositionRow,
    TradeRow,
    parse_ledger_rows,
    parse_position_rows,
    parse_trade_rows,
)

_LOGGER = logging.getLogger(__name__)

_RowT = TypeVar("_RowT")

_POSITIONS_PATH = "/portfolio/{account_id}/positions/{page}"
_LEDGER_PATH = "/portfolio/{account_id}/ledger"
_TRADES_PATH = "/iserver/account/trades"

_SIDE_BY_CODE: dict[str, str] = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}


def _reject(kind: str, reason: str) -> None:
    _LOGGER.warning("ibkr.account.rejected_row", extra={"kind": kind, "reason": reason})


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _default_contract_key_for_conid(conid: int) -> str:
    return f"conid={conid}"


def _position_to_contract(
    row: PositionRow,
    *,
    account_id: str,
    as_of_ts: datetime,
    contract_key_for_conid: Callable[[int], str],
) -> BrokerPosition | None:
    if row.conid is None:
        _reject("position", "no conid")
        return None
    return BrokerPosition(
        as_of_ts=as_of_ts,
        account_id=account_id,
        conid=row.conid,
        contract_key=contract_key_for_conid(row.conid),
        quantity=row.position,
        avg_cost=row.avg_cost,
        market_price=row.market_price,
        market_value=row.market_value,
        currency=row.currency or "",
    )


def _ledger_to_contract(
    currency_code: str, row: LedgerRow, *, account_id: str, as_of_ts: datetime
) -> BrokerCashBalance:
    return BrokerCashBalance(
        as_of_ts=as_of_ts,
        account_id=account_id,
        currency=currency_code,
        cash_balance=row.cash_balance,
        settled_cash=row.settled_cash,
        net_liquidation=row.net_liquidation,
    )


def _trade_to_contract(
    row: TradeRow,
    *,
    account_id: str,
    contract_key_for_conid: Callable[[int], str],
) -> BrokerFill | None:
    if not row.execution_id.strip():
        _reject("fill", "no execution_id")
        return None
    if row.conid is None:
        _reject("fill", "no conid")
        return None
    side = _SIDE_BY_CODE.get(row.side.upper())
    if side is None or side not in FILL_SIDES:
        _reject("fill", f"side {row.side!r}")
        return None
    if row.trade_time_ms is None:
        _reject("fill", "no trade_time")
        return None
    venue_ts = datetime.fromtimestamp(row.trade_time_ms / 1000.0, tz=UTC)
    return BrokerFill(
        account_id=account_id,
        execution_id=row.execution_id,
        conid=row.conid,
        contract_key=contract_key_for_conid(row.conid),
        side=side,
        quantity=abs(row.size),
        price=row.price,
        currency=row.currency or "",
        venue_ts=venue_ts,
        trade_date=venue_ts.date(),
    )


class CpRestAccountCollector:

    def __init__(
        self,
        transport: SupportsRestGet,
        *,
        account_id: str,
        now_fn: Callable[[], datetime] = _now_utc,
        contract_key_for_conid: Callable[[int], str] = _default_contract_key_for_conid,
    ) -> None:
        self._transport = transport
        self._account_id = account_id
        self._now_fn = now_fn
        self._contract_key_for_conid = contract_key_for_conid

    def collect(self) -> BrokerAccountSnapshot:
        as_of_ts = self._now_fn()
        positions = self._read_positions(as_of_ts)
        cash_balances = self._read_cash(as_of_ts)
        fills = self._read_fills()
        return BrokerAccountSnapshot(
            account_id=self._account_id,
            as_of_ts=as_of_ts,
            positions=positions,
            cash_balances=cash_balances,
            fills=fills,
        )

    def read_positions(self) -> tuple[BrokerPosition, ...]:
        return self._read_positions(self._now_fn())

    def read_cash_balances(self) -> tuple[BrokerCashBalance, ...]:
        return self._read_cash(self._now_fn())

    def read_fills(self) -> tuple[BrokerFill, ...]:
        return self._read_fills()

    def _read_positions(self, as_of_ts: datetime) -> tuple[BrokerPosition, ...]:
        path = _POSITIONS_PATH.format(account_id=self._account_id, page=0)
        body = self._transport.get(path)
        kept: list[BrokerPosition] = []
        for row in self._validated_rows(parse_position_rows, body, kind="position"):
            contract = _position_to_contract(
                row,
                account_id=self._account_id,
                as_of_ts=as_of_ts,
                contract_key_for_conid=self._contract_key_for_conid,
            )
            if contract is not None:
                kept.append(contract)
        return tuple(kept)

    def _read_cash(self, as_of_ts: datetime) -> tuple[BrokerCashBalance, ...]:
        path = _LEDGER_PATH.format(account_id=self._account_id)
        body = self._transport.get(path)
        try:
            pairs = parse_ledger_rows(body)
        except ValidationError as exc:
            _reject("ledger", str(exc))
            return ()
        return tuple(
            _ledger_to_contract(code, row, account_id=self._account_id, as_of_ts=as_of_ts)
            for code, row in pairs
        )

    def _read_fills(self) -> tuple[BrokerFill, ...]:
        body = self._transport.get(_TRADES_PATH)
        kept: list[BrokerFill] = []
        for row in self._validated_rows(parse_trade_rows, body, kind="fill"):
            contract = _trade_to_contract(
                row,
                account_id=self._account_id,
                contract_key_for_conid=self._contract_key_for_conid,
            )
            if contract is not None:
                kept.append(contract)
        return tuple(kept)

    @staticmethod
    def _validated_rows(
        parse: Callable[[object], tuple[_RowT, ...]], body: object, *, kind: str
    ) -> tuple[_RowT, ...]:
        if not isinstance(body, list):
            return ()
        kept: list[_RowT] = []
        for entry in body:
            try:
                kept.extend(parse([entry]))
            except ValidationError as exc:
                _reject(kind, str(exc))
        return tuple(kept)


def collect_broker_account(
    transport: SupportsRestGet,
    *,
    account_id: str,
    now_fn: Callable[[], datetime] = _now_utc,
    contract_key_for_conid: Callable[[int], str] = _default_contract_key_for_conid,
) -> BrokerAccountSnapshot:
    collector = CpRestAccountCollector(
        transport,
        account_id=account_id,
        now_fn=now_fn,
        contract_key_for_conid=contract_key_for_conid,
    )
    return collector.collect()
