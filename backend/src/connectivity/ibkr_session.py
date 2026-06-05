"""The first concrete live ``BrokerSession``: a real IBKR feed via ib_async.

This is the broker-specific code the rest of the connectivity layer is designed to
keep at arm's length. Three things stay *inside* this file and never cross the seam:

1. **The broker SDK.** ``ib_async`` is an *optional* dependency (extra ``ibkr``). It is
   imported lazily inside the methods that talk to the gateway, so importing this module —
   or the whole ``connectivity`` package — never requires the SDK to be installed. The
   pure tick-type mapping and the chain-selection helpers below are importable and
   testable with no broker present.
2. **The broker's native tick-type enum.** IBKR identifies every observation by an
   integer tick type (1 = bid, 2 = ask, …, 66+ = the *delayed* variants). That integer
   is mapped to the plain :class:`~connectivity.broker.BrokerTick` ``field_name`` string
   *here*; no IB enum is ever exported, which is exactly what lets the disk replay emit
   the same :class:`BrokerTick` this adapter does and run the same collector code.
3. **The broker's chain-discovery shape.** :meth:`IbkrBrokerSession.request_option_chain`
   does not leak IBKR's ``reqSecDefOptParams`` *parameter grid* (a menu of every listed
   expiry and strike) outward. It expands that menu into one resolved, ``conId``-keyed
   contract row per tradable instrument — the exact shape the universe layer
   (``universe.resolve_chain`` / ``materialize_universe``) consumes — so a caller never
   has to know how IBKR chain discovery works. The raw grid is still reachable for
   diagnostics through :meth:`option_chain_parameters`, which is *not* a ``BrokerSession``
   method.

The session speaks IB ``conId`` as the ``broker_contract_id``: :meth:`subscribe` takes a
conId string and the ticks it yields carry the same string, so they resolve against the
universe (whose instruments are keyed by conId) through the unchanged collector path.

Read-only by default — the attached client can pull data and read state but can never
place, modify, or cancel an order. Pacing/entitlement notices and reconnects are *not*
this layer's job: it raises :class:`SessionDisconnected` on a drop and lets the
:class:`~connectivity.supervisor.SessionSupervisor` own reconnect-with-backoff, exactly
as the fake and replay sessions do.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from universe import AvailableChain, ChainSelection, plan_chain

from .broker import BrokerTick
from .errors import ConnectionFailed, SessionDisconnected

if TYPE_CHECKING:
    from ib_async import IB, Contract, OptionChain, Ticker


# IBKR's native tick-type integers, mapped to the broker-agnostic field name. Both the
# real-time code and its delayed twin (paper logins without a live market-data
# subscription receive the delayed variants) map to the *same* field name, so a delayed
# bid and a live bid are indistinguishable downstream — only the value differs. The
# numbers are IBKR's published "Tick Types" table; the delayed block is 66–76.
#   real-time          delayed
_FIELD_BY_TICK_TYPE: Mapping[int, str] = {
    1: "bid",
    66: "bid",
    2: "ask",
    67: "ask",
    4: "last",
    68: "last",
    9: "close",
    75: "close",
    14: "open",
    76: "open",
    6: "high",
    72: "high",
    7: "low",
    73: "low",
    0: "bid_size",
    69: "bid_size",
    3: "ask_size",
    70: "ask_size",
    5: "last_size",
    71: "last_size",
    8: "volume",
    74: "volume",
}

# The fields whose value lives in a tick's ``size`` rather than its ``price``.
_SIZE_FIELDS: frozenset[str] = frozenset(
    {"bid_size", "ask_size", "last_size", "volume"}
)

# IBKR sends a price of -1.0 to mean "no value available" (e.g. a halted or pre-open
# instrument). It is a sentinel, not a real price, so a tick carrying it is dropped.
_NO_VALUE = -1.0

# Defaults for the universe rows. A qualified IB ``Stock`` carries ``exchange="SMART"``
# already, but a contract can come back with an empty exchange/currency; these fill that
# gap so a resolved row is never missing a field the normalizer requires. A stock has no
# real contract multiplier — IBKR returns ``""`` — but the universe normalizer *requires*
# a positive multiplier for every instrument, so the underlying row carries ``"1"``.
_DEFAULT_EXCHANGE = "SMART"
_DEFAULT_CURRENCY = "USD"
_STOCK_MULTIPLIER = "1"


def ibkr_field_name(tick_type: int) -> str | None:
    """Map an IBKR tick-type integer to a :class:`BrokerTick` field name.

    Returns ``None`` for a tick type this adapter does not carry (option greeks, the
    many auxiliary types), which the caller skips. Pure and SDK-free, so it is unit
    tested directly against IBKR's published tick-type table without a broker.
    """
    return _FIELD_BY_TICK_TYPE.get(tick_type)


def _tick_value(field_name: str, price: float, size: float) -> float | None:
    """Pick the scalar for a field, or ``None`` if the tick carries no usable value.

    Size-bearing fields read ``size``; the rest read ``price``. A non-finite value or
    IBKR's ``-1`` price sentinel yields ``None`` so the observation is dropped rather
    than written as a fake number.
    """
    raw = size if field_name in _SIZE_FIELDS else price
    if raw is None:
        return None
    value = float(raw)
    if value != value:  # NaN
        return None
    if field_name not in _SIZE_FIELDS and value == _NO_VALUE:
        return None
    if value < 0.0:
        return None
    return value


# -- broker-row normalization and row building (pure, SDK-free) --------------
#
# These read only the attributes named below off an ib_async ``Contract`` / ``OptionChain``,
# so they are duck-typed against the SDK at runtime and unit-tested with plain stand-ins.
# The chain-selection *policy* (which listing, which expiries, which strikes) is broker-
# agnostic and lives in :mod:`universe.chain_planning`; this file only translates IBKR's
# native chain-discovery rows into the planner's :class:`AvailableChain` shape and expands
# the resulting plan into real contracts. The Protocol exists for the type checker and
# documents exactly which contract fields the row builders read.


class _QualifiedContract(Protocol):
    """The subset of an ib_async ``Contract`` the row builders read after qualification."""

    conId: int
    symbol: str
    exchange: str
    currency: str
    multiplier: str
    lastTradeDateOrContractMonth: str
    strike: float
    right: str


def _stock_row(contract: _QualifiedContract) -> dict[str, object]:
    """Render a qualified underlying contract as a resolver-ready ``STK`` row."""
    return {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "secType": "STK",
        "exchange": contract.exchange or _DEFAULT_EXCHANGE,
        "currency": contract.currency or _DEFAULT_CURRENCY,
        "multiplier": _STOCK_MULTIPLIER,
    }


def _option_row(contract: _QualifiedContract) -> dict[str, object]:
    """Render a qualified option contract as a resolver-ready ``OPT`` row.

    ``expiry`` is IBKR's ``lastTradeDateOrContractMonth`` (``YYYYMMDD`` for a dated
    option), which the universe normalizer accepts verbatim.
    """
    return {
        "conId": contract.conId,
        "symbol": contract.symbol,
        "secType": "OPT",
        "exchange": contract.exchange or _DEFAULT_EXCHANGE,
        "currency": contract.currency or _DEFAULT_CURRENCY,
        "multiplier": contract.multiplier,
        "expiry": contract.lastTradeDateOrContractMonth,
        "strike": contract.strike,
        "right": contract.right,
    }


def _available_chains(params: Sequence[OptionChain]) -> tuple[AvailableChain, ...]:
    """Translate IBKR's ``reqSecDefOptParams`` rows into the planner's neutral shape.

    ``reqSecDefOptParams`` returns one row per (listing exchange, trading class), each a
    menu of expirations and strikes. This is the only IBKR-specific step in chain
    planning: it copies those fields into a broker-agnostic :class:`AvailableChain` so the
    listing/expiry/strike *policy* in :mod:`universe.chain_planning` never sees an IBKR
    type. Expirations and strikes are frozen into tuples to match the value shape the
    planner expects.
    """
    return tuple(
        AvailableChain(
            exchange=row.exchange,
            trading_class=row.tradingClass,
            multiplier=row.multiplier,
            expirations=tuple(row.expirations),
            strikes=tuple(float(strike) for strike in row.strikes),
        )
        for row in params
    )


class IbkrBrokerSession:
    """A live IBKR :class:`~connectivity.broker.BrokerSession` backed by ib_async.

    Construct it with the Gateway/TWS endpoint; drive it through a
    :class:`~connectivity.supervisor.SessionSupervisor` like any other session. The
    optional ``max_runtime_seconds`` / ``max_ticks`` bounds let a smoke or a fixed
    collection window end the otherwise-unbounded live stream *cleanly* (the supervisor
    sees a normal end of iteration, not a drop), which is what the bundled smoke uses.

    ``market_data_type`` follows IBKR's request types: 1 = live, 2 = frozen, 3 = delayed,
    4 = delayed-frozen. A paper login with no market-data subscription is served the
    delayed variants regardless; 3 makes that explicit and avoids empty live ticks.

    ``selection`` bounds how much of each option chain :meth:`request_option_chain`
    qualifies into the universe (see :class:`ChainSelection`).
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 4002,
        readonly: bool = True,
        market_data_type: int = 3,
        connect_timeout: float = 15.0,
        poll_timeout: float = 1.0,
        max_runtime_seconds: float | None = None,
        max_ticks: int | None = None,
        underlying_exchange: str = _DEFAULT_EXCHANGE,
        currency: str = _DEFAULT_CURRENCY,
        selection: ChainSelection | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._readonly = readonly
        self._market_data_type = market_data_type
        self._connect_timeout = connect_timeout
        self._poll_timeout = poll_timeout
        self._max_runtime_seconds = max_runtime_seconds
        self._max_ticks = max_ticks
        self._underlying_exchange = underlying_exchange
        self._currency = currency
        self._selection = selection or ChainSelection()

        self._ib: IB | None = None
        self._client_id: int | None = None
        self._subscriptions: dict[str, object] = {}
        self._queue: deque[BrokerTick] = deque()
        self._sequence = 0
        self._emitted = 0
        self._pending_disconnect = False
        self._started_at: float | None = None
        # Feed diagnostics, surfaced after a session so the caller can build a
        # MarketDataStatus: the raw (code, message) error notices IBKR pushed, and the
        # market-data type the broker actually served (0 until a tick reveals it).
        self._feed_errors: list[tuple[int, str]] = []
        self._observed_market_data_type = 0

    # -- connection lifecycle -------------------------------------------------

    def connect(self, client_id: int) -> None:
        """Open (or re-open) the IB connection and re-request the data type.

        Called once on first connect and again by the supervisor after every drop; each
        call builds a fresh ``IB`` so a half-dead socket is never reused. A failure to
        connect is raised as :class:`ConnectionFailed`, the variant the supervisor backs
        off and retries.
        """
        try:
            from ib_async import IB, StartupFetchNONE
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ConnectionFailed(
                "ib_async is not installed; add the 'ibkr' extra (uv sync --extra ibkr)"
            ) from exc

        if self._ib is not None:
            self._safe_disconnect()

        ib = IB()
        ib.pendingTickersEvent += self._on_tickers
        ib.disconnectedEvent += self._on_disconnect
        ib.errorEvent += self._on_error
        try:
            ib.connect(
                self._host,
                self._port,
                clientId=client_id,
                readonly=self._readonly,
                timeout=self._connect_timeout,
                # Skip the positions/orders startup queries: they are useless for a data
                # feed and a read-only login lets them time out, stalling connect.
                # ib_async spells "fetch nothing" as the module-level StartupFetchNONE
                # (the zero value of the StartupFetch flag), not a StartupFetch member.
                fetchFields=StartupFetchNONE,
            )
        except Exception as exc:  # noqa: BLE001 - ib_async raises bare Exceptions on connect
            raise ConnectionFailed(f"IBKR connect failed at {self._host}:{self._port}") from exc

        ib.reqMarketDataType(self._market_data_type)
        self._ib = ib
        self._client_id = client_id
        self._pending_disconnect = False
        self._started_at = time.monotonic()

    def disconnect(self) -> None:
        self._safe_disconnect()

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def _safe_disconnect(self) -> None:
        if self._ib is not None:
            self._ib.pendingTickersEvent -= self._on_tickers
            self._ib.disconnectedEvent -= self._on_disconnect
            self._ib.errorEvent -= self._on_error
            if self._ib.isConnected():
                self._ib.disconnect()
        self._ib = None

    # -- feed diagnostics -----------------------------------------------------

    @property
    def requested_market_data_type(self) -> int:
        """The market-data type this session asked the broker for (1/2/3/4)."""
        return self._market_data_type

    @property
    def observed_market_data_type(self) -> int:
        """The market-data type the broker actually served, or ``0`` if none seen yet.

        Read off the ticks themselves: a paper/unentitled login that requested live data
        but is served the delayed variants reports ``3`` here, which is how a downgrade is
        detected without trusting the request to have taken effect.
        """
        return self._observed_market_data_type

    def feed_errors(self) -> tuple[tuple[int, str], ...]:
        """The raw ``(code, message)`` error notices IBKR pushed during the session.

        Returned unclassified and clock-free; a caller with a clock turns these into
        :class:`~connectivity.FeedNotice`\\ s (via ``classify_feed_notice``) and folds them
        into a :class:`~connectivity.MarketDataStatus`. Kept raw here so this adapter reads
        no clock, exactly like the rest of its paths.
        """
        return tuple(self._feed_errors)

    # -- universe discovery ---------------------------------------------------

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        """Resolve an underlying into ``conId``-keyed universe rows: the stock + options.

        Qualifies the underlying, reads its option-chain parameters, normalizes them into
        the broker-agnostic :class:`~universe.AvailableChain` shape, and asks
        :func:`~universe.plan_chain` which bounded set to qualify (see
        :class:`~universe.ChainSelection`). The chosen :class:`~universe.ChainPlan` is then
        *expanded* into real ``Option`` contracts, each qualified to its own ``conId``.
        Returns one plain mapping per qualified contract — the underlying first, then every
        call/put — in exactly the shape the universe layer's ``resolve_chain`` /
        ``materialize_universe`` consumes. No IBKR SDK object escapes. An unknown symbol
        yields ``()``; a symbol with no options yields just the stock row.
        """
        ib = self._require_connected()
        from ib_async import Option, Stock

        qualified = ib.qualifyContracts(Stock(symbol, self._underlying_exchange, self._currency))
        if not qualified:
            return ()
        underlying = qualified[0]
        rows: list[Mapping[str, object]] = [_stock_row(underlying)]

        params = ib.reqSecDefOptParams(symbol, "", underlying.secType, underlying.conId)
        spot = self._spot_price(ib, underlying)
        plan = plan_chain(
            symbol, _available_chains(params), spot=spot, selection=self._selection
        )
        if plan is None:
            return tuple(rows)

        contracts = [
            Option(
                symbol,
                expiry,
                strike,
                right,
                plan.exchange,
                currency=self._currency,
                multiplier=plan.multiplier,
                tradingClass=plan.trading_class,
            )
            for expiry in plan.expiries
            for strike in plan.strikes
            for right in plan.rights
        ]
        if not contracts:
            return tuple(rows)
        # Even within the right trading class the (strikes × expiries) product
        # over-generates: not every strike trades at every expiry. Those phantom combos
        # fail to qualify (IBKR error 200) and come back without a conId — keep only the
        # contracts that genuinely resolved, so a sparse ladder narrows the universe
        # rather than crashing the build.
        qualified_options = ib.qualifyContracts(*contracts)
        rows.extend(
            _option_row(contract) for contract in qualified_options if contract.conId
        )
        return tuple(rows)

    def option_chain_parameters(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        """Raw ``reqSecDefOptParams`` rows for an underlying — a diagnostics escape hatch.

        This is the IBKR parameter *grid* (one row per listing exchange: its expirations,
        strikes, trading class, multiplier), not an instrument universe. It is
        deliberately *not* a :class:`~connectivity.broker.BrokerSession` method —
        :meth:`request_option_chain` is the seam — and exists only to inspect what the
        gateway offered before selection. The rows are plain mappings; no SDK type leaks.
        """
        ib = self._require_connected()
        from ib_async import Stock

        qualified = ib.qualifyContracts(Stock(symbol, self._underlying_exchange, self._currency))
        if not qualified:
            return ()
        underlying = qualified[0]
        params = ib.reqSecDefOptParams(symbol, "", underlying.secType, underlying.conId)
        return tuple(
            {
                "underlying_symbol": symbol,
                "exchange": row.exchange,
                "trading_class": row.tradingClass,
                "multiplier": row.multiplier,
                "expirations": tuple(sorted(row.expirations)),
                "strikes": tuple(sorted(row.strikes)),
            }
            for row in params
        )

    def _spot_price(self, ib: IB, underlying: Contract) -> float | None:
        """Best-effort current price of the underlying, to center the strike window.

        A one-shot snapshot via ``reqTickers``; ``marketPrice()`` is last-within-spread
        else the mid. Any failure or a non-positive/non-finite price returns ``None``, on
        which :func:`~universe.select_strikes` falls back to a strike block around the
        median — a missing snapshot must widen the selection, never abort the universe
        build.
        """
        try:
            tickers = ib.reqTickers(underlying)
        except Exception:  # noqa: BLE001 - a missing snapshot must not abort universe build
            return None
        if not tickers:
            return None
        price = float(tickers[0].marketPrice())
        return price if math.isfinite(price) and price > 0.0 else None

    def subscribe(self, broker_contract_id: str) -> None:
        """Stream market data for a contract, identified by its IBKR conId string."""
        ib = self._require_connected()
        from ib_async import Contract

        contract = Contract(conId=int(broker_contract_id))
        ib.qualifyContracts(contract)
        ib.reqMktData(contract, "", False, False)
        self._subscriptions[broker_contract_id] = contract

    # -- the tick stream ------------------------------------------------------

    def ticks(self) -> Iterator[BrokerTick]:
        """Yield observations until a drop (raises) or a configured bound (clean end).

        Each pass waits for the next batch of IB updates, then drains every
        :class:`BrokerTick` the event handler queued from it. A mid-stream disconnect is
        surfaced as :class:`SessionDisconnected` for the supervisor to recover; reaching
        ``max_ticks`` or ``max_runtime_seconds`` simply returns, ending the stream the
        way a clean end of feed would.
        """
        ib = self._require_connected()
        while True:
            yield from self._drain()
            if self._pending_disconnect:
                self._pending_disconnect = False
                raise SessionDisconnected(f"IBKR feed dropped on client {self._client_id}")
            if self._bounds_reached():
                return
            ib.waitOnUpdate(timeout=self._poll_timeout)

    def _drain(self) -> Iterator[BrokerTick]:
        while self._queue:
            tick = self._queue.popleft()
            self._emitted += 1
            yield tick
            if self._max_ticks is not None and self._emitted >= self._max_ticks:
                return

    def _bounds_reached(self) -> bool:
        if self._max_ticks is not None and self._emitted >= self._max_ticks:
            return True
        return (
            self._max_runtime_seconds is not None
            and self._started_at is not None
            and time.monotonic() - self._started_at >= self._max_runtime_seconds
        )

    # -- ib_async event handlers ---------------------------------------------

    def _on_tickers(self, tickers: object) -> None:
        """Translate a batch of ib_async tickers into queued ``BrokerTick``\\ s.

        Reads the raw per-update ``ticks`` list on each ticker — every entry carries the
        IB tick-type integer this adapter maps — rather than the aggregated named
        attributes, so no update (including a same-value repaint) is lost.
        """
        for ticker in tickers:  # type: ignore[attr-defined]
            self._queue_ticker(ticker)

    def _queue_ticker(self, ticker: Ticker) -> None:
        con_id = ticker.contract.conId if ticker.contract is not None else None
        if con_id is None:
            return
        # A tick reveals which market-data type the broker actually served (live vs the
        # delayed downgrade an unentitled login receives). Recorded as a diagnostic; absent
        # on a ticker (or a stand-in) it is simply not updated.
        market_data_type = getattr(ticker, "marketDataType", None)
        if market_data_type is not None:
            self._observed_market_data_type = int(market_data_type)
        broker_contract_id = str(con_id)
        for entry in ticker.ticks:
            field_name = ibkr_field_name(int(entry.tickType))
            if field_name is None:
                continue
            value = _tick_value(field_name, entry.price, entry.size)
            if value is None:
                continue
            exchange_ts = entry.time if isinstance(entry.time, datetime) else None
            self._queue.append(
                BrokerTick(
                    broker_contract_id=broker_contract_id,
                    field_name=field_name,
                    value=value,
                    sequence=self._sequence,
                    exchange_ts=exchange_ts,
                )
            )
            self._sequence += 1

    def _on_error(
        self, req_id: int, error_code: int, error_string: str, contract: object = None
    ) -> None:
        """Buffer a raw IBKR error/notice for later classification by the caller.

        ib_async routes every ``error``/notice through ``errorEvent`` as
        ``(reqId, errorCode, errorString, contract)`` — including the entitlement and
        pacing notices a live feed emits. They are kept verbatim (code + message) and not
        classified here, so this adapter stays clock-free; :meth:`feed_errors` hands them to
        a caller that owns a clock and the :func:`~connectivity.classify_feed_notice`
        vocabulary.
        """
        self._feed_errors.append((int(error_code), str(error_string)))

    def _on_disconnect(self) -> None:
        self._pending_disconnect = True

    def _require_connected(self) -> IB:
        if self._ib is None or not self._ib.isConnected():
            raise SessionDisconnected("operation requested while IBKR session is not connected")
        return self._ib
