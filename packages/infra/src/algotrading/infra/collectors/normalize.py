"""Turn a broker-agnostic tick into the canonical raw event.

The adapter converts every broker callback into a :class:`BrokerTick` — one observed field of
one instrument, stripped of any vendor type. Normalization then stamps it with the collector's
session id, a per-session event id, and the receipt time, producing the immutable
``RawMarketEvent`` the rest of the stack consumes. Keeping this step pure means the live and
replay paths share one event shape and the transformation is testable without a broker.

A non-finite numeric value (NaN/inf, the broker's way of saying "no quote right now") becomes a
recorded absence (``None``) rather than a dropped event: the raw layer is loss-aware, so a
missing field is evidence, not silence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from algotrading.infra.storage.events import RawMarketEvent


@dataclass(frozen=True)
class BrokerTick:
    """One observed field of one instrument, as handed over by the broker adapter.

    Broker-agnostic by construction: ``value`` is a plain ``float`` for a numeric observation
    (``NaN``/``inf`` meaning "no value available"), a ``str`` for a categorical field, or
    ``None`` when the field is explicitly absent. No vendor enums or callback objects leak past
    this boundary.
    """

    instrument_key: str
    field_name: str
    value: float | str | None
    underlying: str
    # The source/leaf that produced this tick (DERIBIT/SAXO/IBKR); the adapter sets it explicitly.
    provider: str = "DERIBIT"
    exchange_ts: datetime | None = None
    contract_id_broker: str | None = None


def normalize_event(
    tick: BrokerTick,
    *,
    collector_session_id: str,
    event_id: str,
    receipt_ts: datetime,
) -> RawMarketEvent:
    """Map a broker tick to an immutable raw event, stamped with session and receipt identity.

    A finite numeric value is converted to an exact ``Decimal`` (via ``str`` so binary float
    noise never enters the record); a non-finite numeric value is recorded as ``None`` (absence,
    not silence). Categorical strings and explicit ``None`` pass through unchanged.
    """
    return RawMarketEvent(
        collector_session_id=collector_session_id,
        event_id=event_id,
        receipt_ts=receipt_ts,
        instrument_key=tick.instrument_key,
        field_name=tick.field_name,
        field_value=_normalize_value(tick.value),
        underlying=tick.underlying,
        provider=tick.provider,
        exchange_ts=tick.exchange_ts,
        contract_id_broker=tick.contract_id_broker,
    )


def _normalize_value(value: float | str | None) -> Decimal | str | None:
    """Numeric -> exact Decimal (or None when non-finite); str/None pass through."""
    if isinstance(value, str) or value is None:
        return value
    if not math.isfinite(value):
        return None
    return Decimal(str(value))
