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
) -> RawMarketEvent:
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
