from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.infra.contracts import RawMarketEvent, content_event_id

from .errors import ReservedFieldError

RESERVED_PREFIX = "__"


@dataclass(frozen=True, slots=True)
class BrokerTick:

    instrument_key: str
    field_name: str
    value: float | str | None
    underlying: str
    sequence: int = 0
    provider: str = "DERIBIT"
    exchange_ts: datetime | None = None
    contract_id_broker: str | None = None


def is_observation(field_name: str) -> bool:
    return not field_name.startswith(RESERVED_PREFIX)


def is_storable_observation(tick: BrokerTick) -> bool:
    return is_observation(tick.field_name) and _finite_value(tick.value) is not None


def normalize_event(
    tick: BrokerTick,
    *,
    session_id: str,
    trade_date: date,
    receipt_ts: datetime,
) -> RawMarketEvent | None:
    if not is_observation(tick.field_name):
        raise ReservedFieldError(tick.field_name)
    numeric = _finite_value(tick.value)
    if numeric is None:
        return None
    canonical_ts = tick.exchange_ts if tick.exchange_ts is not None else receipt_ts
    exchange_ts = tick.exchange_ts if tick.exchange_ts is not None else receipt_ts
    return RawMarketEvent(
        session_id=session_id,
        event_id=content_event_id(tick.instrument_key, tick.field_name, tick.sequence),
        instrument_key=tick.instrument_key,
        exchange_ts=exchange_ts,
        receipt_ts=receipt_ts,
        canonical_ts=canonical_ts,
        field_name=tick.field_name,
        value=numeric,
        trade_date=trade_date,
        underlying=tick.underlying,
    )


def _finite_value(value: float | str | None) -> float | None:
    if value is None or isinstance(value, str):
        return None
    if not math.isfinite(value):
        return None
    return float(value)
