"""ADR 0024 §4 acceptance bar: the REST path and the TWS path produce identical raw events.

Swapping IBKR ingestion (Client Portal REST snapshot ↔ Nautilus TWS QuoteTick) must not move a
single downstream byte. We feed the *same* observation — same instrument, values, sequence, and
timestamps — through both normalizers and assert the resulting ``RawMarketEvent`` tuples are equal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from algotrading.infra_ibkr.collectors.cp_rest_normalize import snapshot_to_events
from algotrading.infra_ibkr.collectors.nautilus_normalize import quote_tick_to_events
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EXCHANGE = datetime(2026, 6, 4, 18, 29, 20, 115330, tzinfo=UTC)
_RECEIPT = datetime(2026, 6, 4, 18, 29, 20, 115587, tzinfo=UTC)
_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"
_UNDERLYING = "SPY"
_SESSION = "ibkr-equiv-2026-06-04"
_SEQUENCE = 5


def _nanos(moment: datetime) -> int:
    return ((moment - _EPOCH) // timedelta(microseconds=1)) * 1000


def test_rest_snapshot_equals_tws_quote_tick() -> None:
    bid, ask, bid_size, ask_size = "9.27", "9.31", 10, 12

    # TWS path: a Nautilus QuoteTick carrying the same quote.
    tick = QuoteTick(
        instrument_id=InstrumentId.from_str("SPY.SMART"),
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_int(bid_size),
        ask_size=Quantity.from_int(ask_size),
        ts_event=_nanos(_EXCHANGE),
        ts_init=_nanos(_RECEIPT),
    )
    tws_events = quote_tick_to_events(
        tick, instrument_key=_IK, underlying=_UNDERLYING, session_id=_SESSION, sequence=_SEQUENCE
    )

    # REST path: the same quote as a Client Portal snapshot row (84=bid, 86=ask, 88=bid_size,
    # 85=ask_size), with the same exchange/receipt timestamps injected.
    rest_events = snapshot_to_events(
        {"84": bid, "86": ask, "88": str(bid_size), "85": str(ask_size)},
        instrument_key=_IK,
        underlying=_UNDERLYING,
        session_id=_SESSION,
        sequence=_SEQUENCE,
        exchange_ts=_EXCHANGE,
        receipt_ts=_RECEIPT,
    )

    # The acceptance bar: byte-for-byte equal RawMarketEvents (frozen-dataclass equality), in the
    # same order — the two transports are interchangeable below the seam.
    assert rest_events == tws_events
