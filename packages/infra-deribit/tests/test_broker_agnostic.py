"""The broker-agnostic guarantee: every broker's ticks normalize to one identical raw shape.

ADR 0020's whole bet is that no broker leaks a second code path: whichever broker produced a
tick, the collector turns it into the *same* ``RawMarketEvent`` shape, so the actor downstream
cannot tell Saxo from Deribit from IBKR. The full "same actor → structurally identical outputs"
check belongs to M4 (the actor isn't relocated yet); this is its in-reach floor — drive each
broker's pure tick-translation, push the ticks through the one shared ``RawCollector``
normalize path, and assert the emitted records are structurally indistinguishable across brokers.

IBKR participates only when its optional ``ib_async`` extra is installed (its adapter imports the
SDK at module load); Saxo and Deribit always run. The cross-leaf imports here are test-only — the
source packages stay independent (``infra-saxo`` never imports ``infra-deribit``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from algotrading.infra.collectors import BrokerTick, RawCollector
from algotrading.infra.collectors.normalize import BrokerTick as CollectorBrokerTick
from algotrading.infra.storage.events import RawMarketEvent
from algotrading.infra_deribit.collectors.deribit_adapter import _ticks_from_ticker_data
from algotrading.infra_saxo.collectors.saxo_adapter import parse_strike_frame

# A canonical EAV field vocabulary — no broker may emit a field outside this shared set.
_CANONICAL_FIELDS = {
    "bid", "ask", "last", "mark_price", "mark_iv",
    "delta", "gamma", "vega", "theta", "open_interest",
}

_FIXED_TS = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)


class _NullAdapter:
    """A MarketDataAdapter that does nothing — lets us drive RawCollector.ingest() directly."""

    def subscribe(self, instrument_keys: Sequence[str]) -> None: ...
    def set_tick_callback(self, callback: object) -> None: ...
    def set_fault_callback(self, callback: object) -> None: ...
    def unsubscribe_all(self) -> None: ...


class _MemoryWriter:
    def __init__(self) -> None:
        self.events: list[object] = []

    def write_events(self, events: Sequence[object]) -> None:
        self.events.extend(events)


def _normalize(ticks: Sequence[BrokerTick]) -> list[RawMarketEvent]:
    """Push ticks through the one shared collector normalize path and collect the raw events."""
    writer = _MemoryWriter()
    collector = RawCollector(
        adapter=_NullAdapter(),
        writer=writer,
        clock=lambda: _FIXED_TS,
        session_id="agnostic-test",
        flush_batch_size=10_000,
    )
    for tick in ticks:
        collector.ingest(tick)
    collector.flush()
    return [e for e in writer.events if isinstance(e, RawMarketEvent)]


def _deribit_ticks() -> list[BrokerTick]:
    data = {
        "best_bid_price": 0.05,
        "best_ask_price": 0.06,
        "last_price": 0.055,
        "mark_price": 0.052,
        "mark_iv": 65.3,
        "index_price": 67000.0,
        "underlying_price": 67000.0,
    }
    return _ticks_from_ticker_data(
        data,
        instrument_key_str="OPT:BTC:OPT:20251226:C:100000:1:DERIBIT:USD",
        underlying="BTC",
        index_price=67000.0,
    )


def _saxo_ticks() -> list[BrokerTick]:
    strike = {
        "Strike": 760.0,
        "Call": {"Bid": 12.0, "Ask": 12.5, "Greeks": {"MidVolatility": 0.21}},
        "Put": {"Bid": 9.0, "Ask": 9.4, "Greeks": {"MidVolatility": 0.22}},
    }
    return parse_strike_frame(
        strike,
        call_key="OPT:ASML:OPT:20260701:C:760:100:SAXO_236:EUR",
        put_key="OPT:ASML:OPT:20260701:P:760:100:SAXO_236:EUR",
        ts=_FIXED_TS,
    )


def _assert_canonical_raw_events(events: Sequence[RawMarketEvent], *, provider: str) -> None:
    assert events, f"{provider} produced no raw events"
    for e in events:
        assert e.provider == provider
        assert e.field_name in _CANONICAL_FIELDS
        # EAV: one observed field per event, numeric values normalized to exact Decimal.
        assert e.field_value is None or isinstance(e.field_value, Decimal)
        assert e.collector_session_id == "agnostic-test"
        assert e.receipt_ts == _FIXED_TS


def test_saxo_and_deribit_normalize_to_the_same_raw_shape() -> None:
    deribit = _normalize(_deribit_ticks())
    saxo = _normalize(_saxo_ticks())

    _assert_canonical_raw_events(deribit, provider="DERIBIT")
    _assert_canonical_raw_events(saxo, provider="SAXO")

    # Structurally identical: the emitted record type and its field layout do not depend on
    # which broker produced the tick.
    assert {type(e) for e in deribit} == {RawMarketEvent}
    assert {type(e) for e in saxo} == {RawMarketEvent}
    fields = {f.name for f in dataclasses.fields(RawMarketEvent)}
    for e in (*deribit, *saxo):
        assert {f.name for f in dataclasses.fields(e)} == fields


def test_brokertick_seam_is_the_one_shared_tick_type() -> None:
    """Both leaves emit the same ``collectors.normalize.BrokerTick`` — no per-broker tick type."""
    for tick in (*_deribit_ticks(), *_saxo_ticks()):
        assert isinstance(tick, CollectorBrokerTick)


def test_ibkr_joins_the_same_shape_when_its_extra_is_present() -> None:
    pytest.importorskip("ib_async")
    from algotrading.infra_ibkr.collectors.ibkr_adapter import ticker_to_ticks

    class _Ticker:
        time = _FIXED_TS
        bid = 1.0
        ask = 1.2
        last = 1.1
        close = 1.05

    ticks = ticker_to_ticks(
        _Ticker(),
        instrument_key="OPT:SPY:OPT:20260619:C:500:100:SMART:USD",
        underlying="SPY",
        contract_id_broker="123",
    )
    events = _normalize(ticks)
    _assert_canonical_raw_events(events, provider="IBKR")
    fields = {f.name for f in dataclasses.fields(RawMarketEvent)}
    for e in events:
        assert {f.name for f in dataclasses.fields(e)} == fields
