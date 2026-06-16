from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


def _require(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_utc(ts: datetime, name: str) -> None:
    offset = ts.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC, got {ts!r}")


@dataclass(frozen=True)
class CollectorEvent:

    collector_session_id: str
    event_id: str
    receipt_ts: datetime
    instrument_key: str
    field_name: str
    field_value: Decimal | str | None
    underlying: str
    provider: str = "DERIBIT"
    exchange_ts: datetime | None = None
    contract_id_broker: str | None = None

    def __post_init__(self) -> None:
        _require(self.collector_session_id, "collector_session_id")
        _require(self.event_id, "event_id")
        _require(self.instrument_key, "instrument_key")
        _require(self.field_name, "field_name")
        _require(self.underlying, "underlying")
        _require(self.provider, "provider")
        _require_utc(self.receipt_ts, "receipt_ts")
        if self.exchange_ts is not None:
            _require_utc(self.exchange_ts, "exchange_ts")
        if isinstance(self.field_value, Decimal) and not self.field_value.is_finite():
            raise ValueError(f"field_value must be finite, got {self.field_value}")
