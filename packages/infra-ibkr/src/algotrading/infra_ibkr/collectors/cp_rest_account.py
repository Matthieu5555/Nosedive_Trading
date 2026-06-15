"""Read-only CP REST **account** collector — positions, cash/ledger, and the day's fills.

The IBKR-side capability broker reconciliation reads from (TARGET §5.9 / §6, the recon sub-lane
of ``execution-operational-hardening``). The broker leaf had market-data ingestion only; this
closes the gap with a **strictly read-only** collector over three Client Portal endpoints:

* ``GET /portfolio/{accountId}/positions/{pageId}`` → ``BrokerPosition``
* ``GET /portfolio/{accountId}/ledger``             → ``BrokerCashBalance``
* ``GET /iserver/account/trades``                   → ``BrokerFill``

assembled into one coherent :class:`~algotrading.infra.contracts.BrokerAccountSnapshot` (the recon
layer's input). **No order endpoint is ever touched** — only ``/portfolio/*`` and
``/iserver/account/trades`` GETs, asserted in ``test_cp_rest_account.py`` exactly as the
market-data adapter asserts its own read-only invariant (ADR 0024 §4).

Normalize-at-the-door discipline (mirroring the close capture): a malformed row is **rejected**,
not coerced — a positions/trades row that fails wire validation is dropped with a recorded reason
(``ibkr.account.rejected_row``), never written as a dishonest zero. Fills are stamped at their
**own venue time** (``trade_time_r`` epoch-ms), never the read clock, so there is no look-ahead.

Dependencies are injected (the house DI rule): the transport (typed against the shared read seam),
the clock (``now_fn``), and ``contract_key_for_conid`` — the conid → canonical-instrument-key map
recon joins on. The default stamps an unresolved conid as ``conid=<N>`` (the broker's own key is
always present); a caller with a resolved chain can pass a real canonical-key resolver. The whole
path runs in CI against a fake transport — no live Gateway.
"""

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

# A wire row type (PositionRow / TradeRow): keeps the list-parse helper generic so the per-row
# normalizer still sees its concrete row type.
_RowT = TypeVar("_RowT")

# The three read-only account endpoints. ``positions`` is paged; ``{pageId}`` 0 is the first page.
# Spelled as templates so the read-only assertion test can confirm the collector never strays off
# these paths onto an order endpoint.
_POSITIONS_PATH = "/portfolio/{account_id}/positions/{page}"
_LEDGER_PATH = "/portfolio/{account_id}/ledger"
_TRADES_PATH = "/iserver/account/trades"

# IBKR's raw execution side codes → our explicit FILL_SIDES. A code outside this map is a row the
# door rejects (we will not guess a direction).
_SIDE_BY_CODE: dict[str, str] = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}


def _reject(kind: str, reason: str) -> None:
    """Record one normalize-door rejection (a malformed/unjoinable row dropped, never coerced)."""
    _LOGGER.warning("ibkr.account.rejected_row", extra={"kind": kind, "reason": reason})


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _default_contract_key_for_conid(conid: int) -> str:
    """The fallback canonical key for a conid: ``conid=<N>``.

    The broker's integer conid is always present and is recon's primary join key; a caller that
    has resolved the full option chain can inject a resolver that returns the real canonical
    instrument key instead. This default never invents expiry/strike it does not know.
    """
    return f"conid={conid}"


def _position_to_contract(
    row: PositionRow,
    *,
    account_id: str,
    as_of_ts: datetime,
    contract_key_for_conid: Callable[[int], str],
) -> BrokerPosition | None:
    """One validated :class:`PositionRow` → a :class:`BrokerPosition`, or ``None`` to skip it.

    A row with no usable conid cannot be joined to the book, so it is a recorded skip rather than
    a position keyed on nothing.
    """
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
    """One ``(currency, LedgerRow)`` pair → a :class:`BrokerCashBalance` (key is the currency)."""
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
    """One validated :class:`TradeRow` → a :class:`BrokerFill`, or ``None`` to skip it.

    A fill with no execution id, no conid, no venue timestamp, or an unrecognized side is a row
    the door refuses — accounting reads only honest fills (§6). The venue timestamp is the fill's
    *own* ``trade_time_r`` (no look-ahead).
    """
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
    """Reads one broker account's read-only state (positions / cash / fills) over CP REST.

    Strictly read-only: the only paths it ever requests are the two ``/portfolio/*`` GETs and the
    ``/iserver/account/trades`` GET — never an order endpoint (asserted). It does not persist;
    it returns a :class:`BrokerAccountSnapshot` the caller hands to the recon layer (or writes
    through the storage port). The transport, clock, and conid→key resolver are injected.
    """

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
        """Read positions, cash, and the day's fills, normalized into one snapshot at one instant.

        ``as_of_ts`` (the read instant) stamps every position/cash row — the broker has no per-row
        read timestamp, so one read shares one instant. Fills keep their own venue time.
        """
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
        """The account's current positions, stamped at the read instant."""
        return self._read_positions(self._now_fn())

    def read_cash_balances(self) -> tuple[BrokerCashBalance, ...]:
        """The account's per-currency cash/ledger balances, stamped at the read instant."""
        return self._read_cash(self._now_fn())

    def read_fills(self) -> tuple[BrokerFill, ...]:
        """The day's fills, each stamped at its own venue time (no look-ahead)."""
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
        """Parse a list body's rows, rejecting (not raising on) a single malformed row.

        The list parsers validate each row eagerly, so a single bad row would otherwise abort the
        whole read. We re-parse loosely instead: a row that fails validation is recorded and
        dropped, the rest survive — one bad row never loses the good ones.
        """
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
    """One-shot read of a broker account's read-only state — the functional entry point.

    A thin convenience over :class:`CpRestAccountCollector` for callers (the recon runner) that
    want the whole snapshot in one call.
    """
    collector = CpRestAccountCollector(
        transport,
        account_id=account_id,
        now_fn=now_fn,
        contract_key_for_conid=contract_key_for_conid,
    )
    return collector.collect()
