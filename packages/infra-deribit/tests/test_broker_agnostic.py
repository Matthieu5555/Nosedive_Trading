"""The broker-agnostic guarantee: every broker's ticks normalize to one identical raw shape.

ADR 0027's whole bet is that no broker leaks a second code path: whichever broker produced a
tick, the one :class:`RawCollector` turns it into the *same* canonical
:class:`~algotrading.infra.contracts.RawMarketEvent`, so the actor downstream cannot tell Saxo
from Deribit from IBKR. This drives each broker's pure tick-translation, pushes the ticks
through the single shared collector, and asserts the persisted records are structurally
indistinguishable across brokers — one tick type, one collector, one raw shape.

IBKR participates through its Client Portal REST normalizer (``snapshot_to_events``), which is
SDK-free and builds the canonical ``RawMarketEvent`` directly, so all three brokers run in the
gate. The cross-leaf imports here are test-only — the source packages stay independent
(``infra-saxo`` never imports ``infra-deribit``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.infra.collectors import BrokerTick, RawCollector, next_sequence
from algotrading.infra.collectors.normalize import BrokerTick as CollectorBrokerTick
from algotrading.infra.contracts import RawMarketEvent
from algotrading.infra.storage import ParquetStore
from algotrading.infra_deribit.collectors.deribit_adapter import _ticks_from_ticker_data
from algotrading.infra_saxo.collectors.saxo_adapter import parse_strike_frame

# A canonical EAV field vocabulary — no broker may emit a field outside this shared set.
_CANONICAL_FIELDS = {
    "bid", "ask", "last", "mark_price", "mark_iv",
    "delta", "gamma", "vega", "theta", "open_interest",
}

_FIXED_TS = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
_TRADE_DATE = date(2026, 6, 5)


class _FixedClock:
    def now(self) -> datetime:
        return _FIXED_TS


class _PushAdapter:
    """A MarketDataAdapter that captures the collector's tick callback so a test can drive it."""

    def __init__(self) -> None:
        self.tick_cb = None

    def subscribe(self, instrument_keys: Sequence[str]) -> None: ...
    def set_tick_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.tick_cb = callback
    def set_fault_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        ...
    def unsubscribe_all(self) -> None: ...


def _normalize(ticks: Sequence[BrokerTick], tmp_path: Path) -> list[RawMarketEvent]:
    """Push ticks through the one shared collector and read back the persisted raw events.

    Each broker's pure translator omits ``sequence`` (the adapter assigns it on the live
    path); here the test assigns it by the same per-(instrument, field) rule the live and
    replay paths share, so distinct observations get distinct content-addressed ids.
    """
    store = ParquetStore(tmp_path)
    adapter = _PushAdapter()
    collector = RawCollector(
        store=store,
        adapter=adapter,
        session_id="agnostic-test",
        trade_date=_TRADE_DATE,
        clock=_FixedClock(),
        flush_batch_size=10_000,
    )
    counters: dict[tuple[str, str], int] = {}
    for tick in ticks:
        sequenced = dataclasses.replace(
            tick, sequence=next_sequence(counters, tick.instrument_key, tick.field_name)
        )
        adapter.tick_cb(sequenced)  # type: ignore[misc]
    collector.flush()
    return [e for e in store.read("raw_market_events") if e.session_id == "agnostic-test"]


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


def _assert_canonical_raw_events(events: Sequence[RawMarketEvent]) -> None:
    assert events, "produced no raw events"
    for e in events:
        assert e.field_name in _CANONICAL_FIELDS
        # One observed field per event, with a finite numeric value (the raw layer's value
        # is a required finite float; absent observations are not stored, by design).
        assert isinstance(e.value, float)
        assert e.session_id == "agnostic-test"
        assert e.receipt_ts == _FIXED_TS


def test_saxo_and_deribit_normalize_to_the_same_raw_shape(tmp_path: Path) -> None:
    deribit = _normalize(_deribit_ticks(), tmp_path / "deribit")
    saxo = _normalize(_saxo_ticks(), tmp_path / "saxo")

    _assert_canonical_raw_events(deribit)
    _assert_canonical_raw_events(saxo)

    # Structurally identical: the emitted record type and its field layout do not depend on
    # which broker produced the tick. The source/leaf is recoverable from the instrument key
    # (its provider segment), not a separate column — one canonical raw shape.
    assert {type(e) for e in deribit} == {RawMarketEvent}
    assert {type(e) for e in saxo} == {RawMarketEvent}
    fields = {f.name for f in dataclasses.fields(RawMarketEvent)}
    for e in (*deribit, *saxo):
        assert {f.name for f in dataclasses.fields(e)} == fields
    assert all("DERIBIT" in e.instrument_key for e in deribit)
    assert all("SAXO" in e.instrument_key for e in saxo)


def test_brokertick_seam_is_the_one_shared_tick_type() -> None:
    """Both leaves emit the same ``collectors.normalize.BrokerTick`` — no per-broker tick type."""
    for tick in (*_deribit_ticks(), *_saxo_ticks()):
        assert isinstance(tick, CollectorBrokerTick)


def test_ibkr_joins_the_same_shape_via_the_cp_rest_normalizer() -> None:
    """IBKR's CP-REST path emits the same canonical raw shape Saxo and Deribit converge to.

    The CP-REST normalizer builds ``RawMarketEvent`` rows directly (through the shared
    ``market_fields.raw_market_event``), so the broker-agnostic assertion here is on the
    emitted records themselves: same type, same field layout, canonical field vocabulary.
    CP field-tag codes per the Client Portal Web API: 84 = bid, 86 = ask, 31 = last.
    """
    from algotrading.infra_ibkr.collectors import snapshot_to_events

    events = snapshot_to_events(
        {"84": "1.0", "86": "1.2", "31": "1.1"},
        instrument_key="OPT:SPY:OPT:20260619:C:500:100:SMART:USD",
        underlying="SPY",
        session_id="agnostic-test",
        sequence=1,
        exchange_ts=_FIXED_TS,
        receipt_ts=_FIXED_TS,
    )
    _assert_canonical_raw_events(events)
    # bid, ask and last were all present and parseable, so all three become events.
    assert sorted(e.field_name for e in events) == ["ask", "bid", "last"]
    assert {type(e) for e in events} == {RawMarketEvent}
    fields = {f.name for f in dataclasses.fields(RawMarketEvent)}
    for e in events:
        assert {f.name for f in dataclasses.fields(e)} == fields
