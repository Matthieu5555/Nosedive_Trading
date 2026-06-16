from datetime import UTC, datetime, timedelta

from algotrading.infra.contracts import RawMarketEvent, content_event_id

BID = "bid"
ASK = "ask"
BID_SIZE = "bid_size"
ASK_SIZE = "ask_size"
LAST = "last"
LAST_SIZE = "last_size"
VOLUME = "volume"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_datetime(nanos: int) -> datetime:
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
    canonical_ts: datetime | None = None,
) -> RawMarketEvent:
    """Build one immutable ``RawMarketEvent`` for one observed field.

    ``event_id`` is content-addressed (``content_event_id`` over instrument/field/sequence), so a
    re-delivered observation at the same ``sequence`` is written exactly once. The blueprint
    (01-architecture §60) keeps the three timestamps distinct: ``exchange_ts`` is the broker's raw
    update time, ``receipt_ts`` when we received it, and ``canonical_ts`` the NORMALIZED ordering /
    as-of clock. ``canonical_ts`` defaults to ``exchange_ts`` (the streaming default — the exchange
    time *is* the ordering clock), but a caller that assigns a normalized instant distinct from the
    raw exchange time passes it explicitly: the EOD close capture stamps ``canonical_ts=as_of`` (the
    session-close instant all marks are ordered at) while preserving each row's real broker
    ``exchange_ts``. The partition ``trade_date`` follows the canonical (as-of) clock, not the raw
    exchange time.
    """
    canonical = canonical_ts if canonical_ts is not None else exchange_ts
    return RawMarketEvent(
        session_id=session_id,
        event_id=content_event_id(instrument_key, field_name, sequence),
        instrument_key=instrument_key,
        exchange_ts=exchange_ts,
        receipt_ts=receipt_ts,
        canonical_ts=canonical,
        field_name=field_name,
        value=value,
        trade_date=canonical.date(),
        underlying=underlying,
    )
