"""Tests for the live IBKR adapter (`connectivity.ibkr_session`).

The adapter is the one broker-specific `BrokerSession`. The gate runs with **no**
`ib_async` installed (it is the optional `ibkr` extra), so two things are proven here:

1. The pure, SDK-free core — the tick-type mapping, the value filter, and the chain
   selection/row builders — works with no broker present and is asserted against
   IBKR's published tick-type table and independently-derived selections.
2. The SDK-driven paths — `request_option_chain`, `subscribe`, the ticker→`BrokerTick`
   translation, and a full supervisor+collector run — work against a hand-built fake
   `ib_async` module, and the rows the adapter emits are *accepted by the real universe
   resolver* and stream all the way to persisted `RawMarketEvent`s.

Expected values are derived here, never read back from the adapter under test.
"""

from __future__ import annotations

import math
import sys
import types
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from collectors import MarketDataCollector
from connectivity import (
    ENTITLEMENT,
    OTHER,
    IbkrBrokerSession,
    ManualClock,
    SessionSupervisor,
    classify_feed_notice,
    client_id_for,
)
from connectivity.ibkr_session import (
    _option_row,
    _stock_row,
    _tick_value,
    ibkr_field_name,
)
from storage import ParquetStore
from universe import ChainSelection, UniverseService, resolve_chain

_TRADE_DATE = date(2026, 6, 1)
_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# A hand-built fake `ib_async`. Only the surface the adapter actually touches.
# ---------------------------------------------------------------------------


class _FakeContract:
    """Mirror of the ib_async `Contract` attributes the adapter reads."""

    def __init__(
        self,
        *,
        conId: int = 0,
        symbol: str = "",
        secType: str = "",
        exchange: str = "",
        currency: str = "",
        multiplier: str = "",
        lastTradeDateOrContractMonth: str = "",
        strike: float = 0.0,
        right: str = "",
        tradingClass: str = "",
    ) -> None:
        self.conId = conId
        self.symbol = symbol
        self.secType = secType
        self.exchange = exchange
        self.currency = currency
        self.multiplier = multiplier
        self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth
        self.strike = strike
        self.right = right
        self.tradingClass = tradingClass


def _Stock(symbol: str = "", exchange: str = "", currency: str = "") -> _FakeContract:
    return _FakeContract(secType="STK", symbol=symbol, exchange=exchange, currency=currency)


def _Option(
    symbol: str = "",
    lastTradeDateOrContractMonth: str = "",
    strike: float = 0.0,
    right: str = "",
    exchange: str = "",
    *,
    multiplier: str = "",
    currency: str = "",
    tradingClass: str = "",
) -> _FakeContract:
    return _FakeContract(
        secType="OPT",
        symbol=symbol,
        lastTradeDateOrContractMonth=lastTradeDateOrContractMonth,
        strike=strike,
        right=right,
        exchange=exchange,
        multiplier=multiplier,
        currency=currency,
        tradingClass=tradingClass,
    )


class _FakeOptionChain:
    """Mirror of one ib_async `OptionChain` (`reqSecDefOptParams` row)."""

    def __init__(
        self,
        *,
        exchange: str,
        tradingClass: str,
        multiplier: str,
        expirations: Sequence[str],
        strikes: Sequence[float],
    ) -> None:
        self.exchange = exchange
        self.tradingClass = tradingClass
        self.multiplier = multiplier
        self.expirations: Sequence[str] = expirations
        self.strikes: Sequence[float] = strikes


class _FakeSnapshot:
    def __init__(self, price: float) -> None:
        self._price = price

    def marketPrice(self) -> float:
        return self._price


class _FakeTickEntry:
    def __init__(self, tick_type: int, *, price: float = 0.0, size: float = 0.0,
                 time: datetime | None = None) -> None:
        self.tickType = tick_type
        self.price = price
        self.size = size
        self.time = time


class _FakeTicker:
    def __init__(
        self,
        con_id: int | None,
        ticks: list[_FakeTickEntry],
        *,
        market_data_type: int | None = None,
    ) -> None:
        self.contract = _FakeContract(conId=con_id) if con_id is not None else None
        self.ticks = ticks
        # ib_async tickers carry the served market-data type; the adapter reads it off
        # the tick. Left unset (no attribute) when not provided, like the older fakes.
        if market_data_type is not None:
            self.marketDataType = market_data_type


class _FakeEvent:
    """The `+=` / `-=` event handle ib_async exposes; emits to registered handlers."""

    def __init__(self) -> None:
        self._handlers: list[object] = []

    def __iadd__(self, handler: object) -> _FakeEvent:
        self._handlers.append(handler)
        return self

    def __isub__(self, handler: object) -> _FakeEvent:
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self

    def emit(self, *payload: object) -> None:
        for handler in list(self._handlers):
            handler(*payload)  # type: ignore[operator]


class _FakeIB:
    """A scriptable stand-in for ib_async's `IB`.

    Configured at the class level (so the adapter's no-arg `IB()` in `connect` picks the
    script up) by `configure(...)`. Drives the live loop: each `waitOnUpdate` emits the
    next scripted ticker batch through `pendingTickersEvent`, exactly as the real client
    does on a feed update.
    """

    underlying: _FakeContract | None = None
    params: list[_FakeOptionChain] = []
    spot: float | None = None
    fail_tickers: bool = False
    script_batches: list[list[_FakeTicker]] = []
    _next_con_id: int = 700_000

    @classmethod
    def configure(
        cls,
        *,
        underlying: _FakeContract | None = None,
        params: list[_FakeOptionChain] | None = None,
        spot: float | None = None,
        fail_tickers: bool = False,
        script_batches: list[list[_FakeTicker]] | None = None,
    ) -> None:
        cls.underlying = underlying
        cls.params = params or []
        cls.spot = spot
        cls.fail_tickers = fail_tickers
        cls.script_batches = script_batches or []
        cls._next_con_id = 700_000

    def __init__(self) -> None:
        self.pendingTickersEvent = _FakeEvent()
        self.disconnectedEvent = _FakeEvent()
        self.errorEvent = _FakeEvent()
        self._connected = False
        self._cursor = 0
        self.market_data_type: int | None = None
        self.req_mkt_data_contracts: list[_FakeContract] = []
        self.qualify_batches: list[tuple[_FakeContract, ...]] = []

    # connection lifecycle
    def connect(self, host: str, port: int, *, clientId: int, readonly: bool,
                timeout: float, fetchFields: object) -> None:
        self._connected = True

    def isConnected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_type = market_data_type

    # discovery
    def qualifyContracts(self, *contracts: _FakeContract) -> list[_FakeContract]:
        self.qualify_batches.append(contracts)
        if len(contracts) == 1 and contracts[0].secType == "STK":
            underlying = type(self).underlying
            assert underlying is not None, "configure(underlying=...) first"
            return [underlying]
        qualified: list[_FakeContract] = []
        for contract in contracts:
            if not contract.conId:  # leave an already-qualified id (e.g. from subscribe)
                contract.conId = type(self)._next_con_id
                type(self)._next_con_id += 1
            qualified.append(contract)
        return qualified

    def reqSecDefOptParams(self, underlyingSymbol: str, futFopExchange: str,
                           underlyingSecType: str, underlyingConId: int) -> list[_FakeOptionChain]:
        return type(self).params

    def reqTickers(self, *contracts: _FakeContract) -> list[_FakeSnapshot]:
        if type(self).fail_tickers:
            raise RuntimeError("no snapshot available")
        spot = type(self).spot
        if spot is None:
            return []
        return [_FakeSnapshot(spot)]

    # streaming
    def reqMktData(self, contract: _FakeContract, *args: object) -> None:
        self.req_mkt_data_contracts.append(contract)

    def waitOnUpdate(self, timeout: float) -> None:
        if self._cursor < len(type(self).script_batches):
            batch = type(self).script_batches[self._cursor]
            self._cursor += 1
            self.pendingTickersEvent.emit(batch)


@pytest.fixture
def ib_async_stub(monkeypatch: pytest.MonkeyPatch) -> type[_FakeIB]:
    """Install a fake `ib_async` module so the adapter's lazy imports resolve."""
    module = types.ModuleType("ib_async")
    module.IB = _FakeIB  # type: ignore[attr-defined]
    module.Stock = _Stock  # type: ignore[attr-defined]
    module.Option = _Option  # type: ignore[attr-defined]
    module.Contract = _FakeContract  # type: ignore[attr-defined]
    module.StartupFetchNONE = 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ib_async", module)
    _FakeIB.configure()
    return _FakeIB


# ---------------------------------------------------------------------------
# Pure core: tick-type mapping and value filter (no broker, no stub).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("live", "delayed", "field"),
    [
        (1, 66, "bid"),
        (2, 67, "ask"),
        (4, 68, "last"),
        (9, 75, "close"),
        (14, 76, "open"),
        (6, 72, "high"),
        (7, 73, "low"),
        (0, 69, "bid_size"),
        (3, 70, "ask_size"),
        (5, 71, "last_size"),
        (8, 74, "volume"),
    ],
)
def test_live_and_delayed_tick_types_map_to_the_same_field(
    live: int, delayed: int, field: str
) -> None:
    assert ibkr_field_name(live) == field
    assert ibkr_field_name(delayed) == field


def test_unmapped_tick_type_is_none() -> None:
    # 13 (model option computation) and 100+ (auxiliary) are not carried.
    assert ibkr_field_name(13) is None
    assert ibkr_field_name(106) is None


def test_tick_value_reads_price_for_a_price_field_and_size_for_a_size_field() -> None:
    assert _tick_value("bid", price=101.5, size=0.0) == 101.5
    assert _tick_value("bid_size", price=0.0, size=42.0) == 42.0


def test_tick_value_drops_nan_negative_and_the_minus_one_price_sentinel() -> None:
    assert _tick_value("bid", price=math.nan, size=0.0) is None
    assert _tick_value("bid", price=-1.0, size=0.0) is None      # IBKR "no value" sentinel
    assert _tick_value("ask", price=-5.0, size=0.0) is None      # genuinely negative
    # -1 is a real price sentinel only; a size of 0 is a legitimate value.
    assert _tick_value("bid_size", price=0.0, size=0.0) == 0.0


# ---------------------------------------------------------------------------
# Pure core: row builders. (Chain-selection policy now lives in
# `universe.chain_planning`; its tests are in `test_chain_planning.py`.)
# ---------------------------------------------------------------------------


def test_stock_and_option_row_builders_emit_resolver_ready_rows() -> None:
    stock = _FakeContract(conId=265598, symbol="AAPL", secType="STK",
                          exchange="SMART", currency="USD")
    assert _stock_row(stock) == {
        "conId": 265598, "symbol": "AAPL", "secType": "STK",
        "exchange": "SMART", "currency": "USD", "multiplier": "1",
    }
    option = _FakeContract(conId=111, symbol="AAPL", secType="OPT", exchange="SMART",
                           currency="USD", multiplier="100",
                           lastTradeDateOrContractMonth="20260619", strike=300.0, right="C")
    assert _option_row(option) == {
        "conId": 111, "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
        "currency": "USD", "multiplier": "100", "expiry": "20260619",
        "strike": 300.0, "right": "C",
    }


# ---------------------------------------------------------------------------
# SDK-driven: request_option_chain expands into resolver-accepted rows.
# ---------------------------------------------------------------------------


def _connected_session(ib: _FakeIB, **kwargs: object) -> IbkrBrokerSession:
    """An IbkrBrokerSession with a fake IB injected and reporting connected."""
    session = IbkrBrokerSession(**kwargs)  # type: ignore[arg-type]
    session._ib = ib  # type: ignore[assignment]
    return session


def _aapl_chain(strikes: list[float]) -> _FakeOptionChain:
    return _FakeOptionChain(
        exchange="SMART", tradingClass="AAPL", multiplier="100",
        expirations=["20260619", "20260717", "20260918"], strikes=strikes,
    )


def test_request_option_chain_emits_a_universe_the_resolver_accepts(
    ib_async_stub: type[_FakeIB],
) -> None:
    underlying = _FakeContract(conId=265598, symbol="AAPL", secType="STK",
                               exchange="SMART", currency="USD")
    ib_async_stub.configure(
        underlying=underlying,
        params=[_aapl_chain([280.0, 290.0, 300.0, 310.0, 320.0])],
        spot=300.0,
    )
    ib = _FakeIB()
    ib._connected = True
    session = _connected_session(
        ib, selection=ChainSelection(max_expiries=2, strike_window_pct=0.1,
                                     min_strikes_per_side=1),
    )

    rows = session.request_option_chain("AAPL")

    # First row is the underlying; the rest are options, all conId-keyed.
    assert rows[0]["secType"] == "STK"
    assert rows[0]["conId"] == 265598
    option_rows = [r for r in rows if r["secType"] == "OPT"]
    # 2 expiries x strikes-in-[270,330] (280,290,300,310,320) x {C,P}.
    assert len(option_rows) == 2 * 5 * 2
    assert {r["right"] for r in option_rows} == {"C", "P"}
    assert all(isinstance(r["conId"], int) and r["conId"] for r in option_rows)

    # The headline: the real universe resolver accepts every row, and the resolved
    # universe exposes the underlying plus a non-empty option chain.
    resolved = resolve_chain(rows)
    universe = UniverseService([c.instrument for c in resolved], _TRADE_DATE)
    assert universe.get_underlying("AAPL").broker_contract_id == "265598"
    chain = universe.get_option_chain("AAPL", _TRADE_DATE)
    assert len(chain) == len(option_rows)
    assert {opt.option_right for opt in chain} == {"C", "P"}


def test_request_option_chain_unknown_symbol_yields_nothing(
    ib_async_stub: type[_FakeIB],
) -> None:
    ib_async_stub.configure(underlying=None)

    class _NoStockIB(_FakeIB):
        def qualifyContracts(self, *contracts: _FakeContract) -> list[_FakeContract]:
            return []  # an unknown symbol qualifies to nothing

    no_stock = _NoStockIB()
    no_stock._connected = True
    session = _connected_session(no_stock)
    assert session.request_option_chain("NOPE") == ()


def test_request_option_chain_with_no_option_params_returns_stock_only(
    ib_async_stub: type[_FakeIB],
) -> None:
    underlying = _FakeContract(conId=265598, symbol="AAPL", secType="STK",
                               exchange="SMART", currency="USD")
    ib_async_stub.configure(underlying=underlying, params=[])  # listed, but no options
    ib = _FakeIB()
    ib._connected = True
    session = _connected_session(ib)
    rows = session.request_option_chain("AAPL")
    assert len(rows) == 1
    assert rows[0]["secType"] == "STK"


def test_request_option_chain_survives_a_missing_spot_snapshot(
    ib_async_stub: type[_FakeIB],
) -> None:
    underlying = _FakeContract(conId=265598, symbol="AAPL", secType="STK",
                               exchange="SMART", currency="USD")
    ib_async_stub.configure(
        underlying=underlying,
        params=[_aapl_chain([280.0, 290.0, 300.0, 310.0, 320.0])],
        fail_tickers=True,  # reqTickers raises -> _spot_price returns None
    )
    ib = _FakeIB()
    ib._connected = True
    session = _connected_session(
        ib, selection=ChainSelection(max_expiries=1, min_strikes_per_side=2),
    )
    rows = session.request_option_chain("AAPL")
    option_rows = [r for r in rows if r["secType"] == "OPT"]
    # Median-block fallback: 1 expiry x 4 strikes (2 each side of median 300) x {C,P}.
    assert len(option_rows) == 1 * 4 * 2
    # Still a valid universe.
    assert resolve_chain(rows)


# ---------------------------------------------------------------------------
# SDK-driven: subscribe issues market-data on a conId-based contract.
# ---------------------------------------------------------------------------


def test_subscribe_requests_market_data_on_a_conid_contract(
    ib_async_stub: type[_FakeIB],
) -> None:
    ib = _FakeIB()
    ib._connected = True
    session = _connected_session(ib)
    session.subscribe("265598")
    assert len(ib.req_mkt_data_contracts) == 1
    assert ib.req_mkt_data_contracts[0].conId == 265598


# ---------------------------------------------------------------------------
# SDK-driven: ticker batch -> BrokerTick translation.
# ---------------------------------------------------------------------------


def test_ticks_translate_a_ticker_batch_dropping_unusable_values(
    ib_async_stub: type[_FakeIB],
) -> None:
    ts = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    batch = [
        _FakeTicker(
            265598,
            [
                _FakeTickEntry(1, price=300.0, time=ts),      # bid (live)
                _FakeTickEntry(67, price=300.5, time=ts),     # ask (delayed)
                _FakeTickEntry(4, price=-1.0, time=ts),       # last -> dropped (sentinel)
                _FakeTickEntry(13, price=1.0, time=ts),       # unmapped -> skipped
                _FakeTickEntry(0, size=12.0, time=ts),        # bid_size
            ],
        ),
        _FakeTicker(None, [_FakeTickEntry(1, price=1.0)]),    # no contract -> skipped
    ]
    ib = _FakeIB()
    ib._connected = True
    # Wire the adapter's handler the way connect() would, then feed the batch.
    session = _connected_session(ib, max_ticks=3)
    ib.pendingTickersEvent += session._on_tickers
    _FakeIB.script_batches = [batch]  # emitted on the first waitOnUpdate

    ticks = list(session.ticks())

    assert [(t.field_name, t.value) for t in ticks] == [
        ("bid", 300.0), ("ask", 300.5), ("bid_size", 12.0),
    ]
    assert all(t.broker_contract_id == "265598" for t in ticks)
    assert [t.sequence for t in ticks] == [0, 1, 2]  # stable per-session ordinals


# ---------------------------------------------------------------------------
# End to end: AAPL -> universe -> quotes -> persisted RawMarketEvents,
# through the real SessionSupervisor and MarketDataCollector.
# ---------------------------------------------------------------------------


def test_supervisor_and_collector_persist_raw_events_from_a_live_ibkr_session(
    ib_async_stub: type[_FakeIB], tmp_path: Path,
) -> None:
    underlying = _FakeContract(conId=265598, symbol="AAPL", secType="STK",
                               exchange="SMART", currency="USD")
    ts = _T0 + timedelta(seconds=1)
    # One feed update carrying a bid and an ask on the underlying conId.
    batch = [_FakeTicker(265598, [
        _FakeTickEntry(66, price=300.0, time=ts),   # delayed bid
        _FakeTickEntry(67, price=300.5, time=ts),   # delayed ask
    ])]
    ib_async_stub.configure(
        underlying=underlying,
        params=[_aapl_chain([290.0, 300.0, 310.0])],
        spot=300.0,
        script_batches=[batch],
    )

    store = ParquetStore(tmp_path)
    clock = ManualClock(start=_T0)
    session = IbkrBrokerSession(
        max_ticks=2,
        selection=ChainSelection(max_expiries=1, strike_window_pct=0.5,
                                 min_strikes_per_side=1),
    )
    supervisor = SessionSupervisor(session, client_id=client_id_for("smoke"), clock=clock)
    supervisor.connect()

    # Discover the universe through the same seam the smoke uses.
    rows = supervisor.request_option_chain("AAPL")
    resolved = resolve_chain(rows)
    universe = UniverseService([c.instrument for c in resolved], _TRADE_DATE)
    con_ids = [universe.get_underlying("AAPL").broker_contract_id] + [
        opt.broker_contract_id for opt in universe.get_option_chain("AAPL", _TRADE_DATE)
    ]

    collector = MarketDataCollector(
        store=store, universe=universe, session_id="sess-ibkr-2026-06-01",
        trade_date=_TRADE_DATE, clock=clock,
    )
    summary = collector.collect(supervisor, subscribe=con_ids)

    events = store.read("raw_market_events")
    assert {e.field_name for e in events} == {"bid", "ask"}
    assert sorted(e.value for e in events) == [300.0, 300.5]
    # The conId resolved to the underlying's canonical instrument key.
    assert all(e.instrument_key.startswith("AAPL|STK") for e in events)
    assert summary.event_count == 2


# ---------------------------------------------------------------------------
# SDK-driven: feed diagnostics (entitlement notices, observed data type).
# ---------------------------------------------------------------------------


def test_error_event_notices_are_buffered_raw_and_stay_classifiable(
    ib_async_stub: type[_FakeIB],
) -> None:
    ts = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    session = IbkrBrokerSession(market_data_type=3)
    session.connect(7)  # connect wires errorEvent onto the fresh internal IB
    # IBKR pushes notices through errorEvent: an entitlement downgrade and a benign info.
    session._ib.errorEvent.emit(  # type: ignore[union-attr]
        7, 10091, "Requested market data is not subscribed; displaying delayed", None
    )
    session._ib.errorEvent.emit(7, 2104, "Market data farm connection is OK", None)  # type: ignore[union-attr]

    # Buffered verbatim (code, message), unclassified and clock-free.
    assert session.feed_errors() == (
        (10091, "Requested market data is not subscribed; displaying delayed"),
        (2104, "Market data farm connection is OK"),
    )
    # A caller with a clock classifies them: 10091 is an entitlement notice, 2104 is not.
    kinds = {code: classify_feed_notice(code, msg, ts).kind for code, msg in session.feed_errors()}
    assert kinds[10091] == ENTITLEMENT
    assert kinds[2104] == OTHER


def test_observed_market_data_type_is_read_off_ticks_revealing_a_downgrade(
    ib_async_stub: type[_FakeIB],
) -> None:
    ts = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    # Requested live (1) but the broker serves delayed (3): a downgrade the tick reveals.
    batch = [_FakeTicker(265598, [_FakeTickEntry(66, price=300.0, time=ts)], market_data_type=3)]
    ib = _FakeIB()
    ib._connected = True
    session = _connected_session(ib, max_ticks=1, market_data_type=1)
    ib.pendingTickersEvent += session._on_tickers
    _FakeIB.script_batches = [batch]

    list(session.ticks())

    assert session.requested_market_data_type == 1
    assert session.observed_market_data_type == 3
