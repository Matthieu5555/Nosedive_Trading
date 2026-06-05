"""Shared field names + ``RawMarketEvent`` construction for both IBKR ingestion paths.

The Nautilus-TWS path (``nautilus_normalize``) and the Client Portal REST path
(``cp_rest_normalize``) must emit **identical** ``RawMarketEvent`` rows for the same
observation — that equivalence is ADR 0024's acceptance bar. Both build their events through
:func:`raw_market_event` here and use the same field-name constants, so the only thing that can
differ between the two paths is how each *maps its wire shape onto these names* — which is exactly
what the equivalence test pins.
"""

from datetime import UTC, datetime, timedelta

from algotrading.infra.contracts import RawMarketEvent, content_event_id

# The canonical observation field names. Both paths map their broker-specific wire fields
# (Nautilus QuoteTick attributes; Client Portal numeric tag codes) onto exactly these strings.
BID = "bid"
ASK = "ask"
BID_SIZE = "bid_size"
ASK_SIZE = "ask_size"
LAST = "last"
LAST_SIZE = "last_size"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_datetime(nanos: int) -> datetime:
    """Unix nanoseconds → UTC datetime (microsecond precision; exact for our timestamps)."""
    return _EPOCH + timedelta(microseconds=nanos // 1000)


def raw_market_event(
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    field_name: str,
    value: float,
    sequence: int,
    exchange_ts: datetime,
    receipt_ts: datetime,
) -> RawMarketEvent:
    """Build one immutable ``RawMarketEvent`` for one observed field.

    ``event_id`` is content-addressed (``content_event_id`` over instrument/field/sequence), so a
    re-delivered observation at the same ``sequence`` is written exactly once. ``canonical_ts`` is
    the exchange time — the ordering/as-of clock.
    """
    return RawMarketEvent(
        session_id=session_id,
        event_id=content_event_id(instrument_key, field_name, sequence),
        instrument_key=instrument_key,
        exchange_ts=exchange_ts,
        receipt_ts=receipt_ts,
        canonical_ts=exchange_ts,
        field_name=field_name,
        value=value,
        trade_date=exchange_ts.date(),
        underlying=underlying,
    )
