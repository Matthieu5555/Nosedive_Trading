"""The Nautilus-tick → RawMarketEvent seam (ADR 0023/0024).

Pure, base-Nautilus-types only — fully exercised in CI without the ``ibkr`` extra or a
Gateway. Expected event ids are derived independently from the documented
``content_event_id`` formula (SHA-256 of ``instrument_key \x1f field \x1f sequence``),
not read back from the code under test.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from algotrading.infra_ibkr.collectors.nautilus_normalize import (
    quote_tick_to_events,
    quote_ticks_to_events,
    trade_tick_to_events,
)
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# A microsecond-precision exchange/receipt instant; nanos are an exact multiple of 1000.
_EXCHANGE = datetime(2026, 6, 4, 18, 29, 20, 115330, tzinfo=UTC)
_RECEIPT = datetime(2026, 6, 4, 18, 29, 20, 115587, tzinfo=UTC)
_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"
_UNDERLYING = "SPY"
_SESSION = "ibkr-spy-2026-06-04"


def _nanos(moment: datetime) -> int:
    return ((moment - _EPOCH) // timedelta(microseconds=1)) * 1000


def _expected_event_id(field_name: str, sequence: int) -> str:
    # Independent oracle: the documented content_event_id formula (broker.py docstring).
    payload = "\x1f".join((_IK, field_name, str(sequence)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quote_tick(*, bid: str, ask: str, bid_size: int, ask_size: int) -> QuoteTick:
    return QuoteTick(
        instrument_id=InstrumentId.from_str("SPY.SMART"),
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_int(bid_size),
        ask_size=Quantity.from_int(ask_size),
        ts_event=_nanos(_EXCHANGE),
        ts_init=_nanos(_RECEIPT),
    )


def test_quote_tick_maps_to_four_field_events() -> None:
    events = quote_tick_to_events(
        _quote_tick(bid="9.27", ask="9.31", bid_size=10, ask_size=12),
        instrument_key=_IK,
        underlying=_UNDERLYING,
        session_id=_SESSION,
        sequence=42,
    )

    by_field = {event.field_name: event for event in events}
    assert set(by_field) == {"bid", "ask", "bid_size", "ask_size"}

    # Values carried through verbatim (hand-supplied above).
    assert by_field["bid"].value == 9.27
    assert by_field["ask"].value == 9.31
    assert by_field["bid_size"].value == 10.0
    assert by_field["ask_size"].value == 12.0

    for event in events:
        assert event.instrument_key == _IK
        assert event.underlying == _UNDERLYING
        assert event.session_id == _SESSION
        assert event.exchange_ts == _EXCHANGE  # from ts_event
        assert event.receipt_ts == _RECEIPT  # from ts_init
        assert event.canonical_ts == _EXCHANGE
        assert event.trade_date == _EXCHANGE.date()
        # Content-addressed id matches the independent oracle.
        assert event.event_id == _expected_event_id(event.field_name, 42)


def test_quote_tick_event_ids_are_idempotent_per_sequence() -> None:
    tick = _quote_tick(bid="9.27", ask="9.31", bid_size=10, ask_size=12)
    kwargs = {"instrument_key": _IK, "underlying": _UNDERLYING, "session_id": _SESSION}

    again = quote_tick_to_events(tick, sequence=42, **kwargs)
    same = quote_tick_to_events(tick, sequence=42, **kwargs)
    later = quote_tick_to_events(tick, sequence=43, **kwargs)

    # Re-delivery at the same sequence reproduces the same ids (idempotent write key).
    assert [e.event_id for e in again] == [e.event_id for e in same]
    # A genuinely new update (next sequence) gets distinct ids.
    assert set(e.event_id for e in again).isdisjoint(e.event_id for e in later)


def test_trade_tick_maps_to_last_and_size() -> None:
    tick = TradeTick(
        instrument_id=InstrumentId.from_str("SPY.SMART"),
        price=Price.from_str("9.29"),
        size=Quantity.from_int(5),
        aggressor_side=AggressorSide.NO_AGGRESSOR,
        trade_id=TradeId("T1"),
        ts_event=_nanos(_EXCHANGE),
        ts_init=_nanos(_RECEIPT),
    )
    events = {
        e.field_name: e
        for e in trade_tick_to_events(
            tick, instrument_key=_IK, underlying=_UNDERLYING, session_id=_SESSION, sequence=7
        )
    }
    assert set(events) == {"last", "last_size"}
    assert events["last"].value == 9.29
    assert events["last_size"].value == 5.0
    assert events["last"].event_id == _expected_event_id("last", 7)


def test_quote_ticks_run_assigns_monotonic_sequence() -> None:
    ticks = [
        _quote_tick(bid="9.27", ask="9.31", bid_size=10, ask_size=12),
        _quote_tick(bid="9.28", ask="9.32", bid_size=11, ask_size=13),
    ]
    events = quote_ticks_to_events(
        ticks, instrument_key=_IK, underlying=_UNDERLYING, session_id=_SESSION, first_sequence=100
    )
    assert len(events) == 8  # two ticks × four fields
    # First tick at sequence 100, second at 101 → ids differ across the two ticks.
    bid_ids = [e.event_id for e in events if e.field_name == "bid"]
    assert bid_ids == [_expected_event_id("bid", 100), _expected_event_id("bid", 101)]
