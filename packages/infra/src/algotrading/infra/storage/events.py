"""The raw market event: one immutable observation of a single field.

The raw layer is entity-attribute-value: one event records exactly one observed field of one
instrument (its ``field_name`` and ``field_value``), as captured. This keeps the persisted
record minimal and append-only — a quote becomes separate bid/ask events sharing a receipt
timestamp, a trade becomes a ``last`` event, and a new field never requires a schema change.

``receipt_ts`` is when the collector received the observation and is always present, giving a
total order for replay; ``exchange_ts`` is the market observation time when the source
provides one. ``underlying`` is denormalized for partitioning. Numeric values are exact
``Decimal``; the broker's own contract id is kept only as an optional foreign key.
"""

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
class RawMarketEvent:
    """A single immutable observation of one field of one instrument.

    Identity is ``(collector_session_id, event_id)`` and the raw layer is immutable: a given
    pair always carries the same content. ``event_id`` must be unique within its
    ``collector_session_id``. ``field_value`` is typed at the normalization boundary: a
    numeric observation is an exact ``Decimal``, an absent value is ``None``, and ``str`` is
    reserved for categorical fields not yet in use.
    """

    collector_session_id: str
    event_id: str
    receipt_ts: datetime
    instrument_key: str
    field_name: str
    field_value: Decimal | str | None
    underlying: str
    # Source/leaf identity (e.g. DERIBIT/SAXO/IBKR), distinct from the exchange carried in
    # instrument_key — two sources can quote the same listing. Defaults to DERIBIT because raw
    # history predates this dimension and was captured exclusively from Deribit.
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
