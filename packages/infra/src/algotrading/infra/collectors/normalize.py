"""The one broker tick, and the push-tick → ``RawMarketEvent`` normalizer.

A broker adapter turns every vendor callback into a :class:`BrokerTick` — one observed
field of one instrument, stripped of any vendor type — and pushes it at the collector.
This module owns the tick shape and the pure transformation that stamps a tick into the
immutable :class:`~algotrading.infra.contracts.RawMarketEvent` the rest of the stack
reads. Keeping the transform pure means the live and replay paths share one event shape
and one normalizer, which is what makes same-code-path replay byte-identical (ADR 0027).

``event_id`` is content-addressed on ``(instrument_key, field_name, sequence)`` (ADR
0003): a tick re-delivered after a reconnect, or re-fed after a kill/restart, carries the
*same* ``sequence`` and therefore the *same* id, so the append-only store writes it
exactly once. ``sequence`` is the feed's stable per-(instrument, field) ordinal; the
adapter assigns it on the live path and the replay source re-emits the stored ordinal.

A tick whose numeric value is absent — ``None`` or non-finite (``NaN``/``inf``, the
broker's way of saying "no quote right now") — is not a storable observation, because the
raw layer's ``value`` is a required finite float. It is reported back to the collector as
a skip (the normalizer returns ``None``) rather than written as a fake record; the loss is
still visible, as a coverage gap in the session summary rather than a silent zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.infra.contracts import RawMarketEvent, content_event_id

from .errors import ReservedFieldError

# Field names beginning with this prefix are collector meta-events (e.g. a recorded gap),
# never observations; a real tick may not claim one.
RESERVED_PREFIX = "__"


@dataclass(frozen=True, slots=True)
class BrokerTick:
    """One observed field of one instrument, as handed over by a broker adapter.

    Broker-agnostic by construction: ``value`` is a plain ``float`` for a numeric
    observation (``NaN``/``inf`` meaning "no value available"), a ``str`` for a categorical
    field, or ``None`` when the field is explicitly absent. No vendor enums or callback
    objects leak past this boundary.

    ``sequence`` is the feed's stable per-(instrument, field) ordinal for this observation;
    a tick re-delivered after a reconnect carries the *same* sequence, which is exactly what
    makes the collector's event id stable and its writes idempotent. ``exchange_ts`` is
    ``None`` when the feed provides no exchange time; normalization falls back to the
    receipt time for ordering. ``provider`` names the source/leaf (DERIBIT/SAXO/IBKR);
    ``contract_id_broker`` keeps the broker's own contract id as an optional cross-reference.
    """

    instrument_key: str
    field_name: str
    value: float | str | None
    underlying: str
    sequence: int = 0
    provider: str = "DERIBIT"
    exchange_ts: datetime | None = None
    contract_id_broker: str | None = None


def is_observation(field_name: str) -> bool:
    """True for a real market observation, False for a reserved meta-event field."""
    return not field_name.startswith(RESERVED_PREFIX)


def is_storable_observation(tick: BrokerTick) -> bool:
    """True iff this tick becomes a stored :class:`RawMarketEvent` — the sequence-advance rule.

    A tick is stored only when it is a real observation (not a reserved meta field) *and* it
    carries a finite numeric value — exactly the two conditions under which
    :func:`normalize_event` returns a non-``None`` event. The live sequence stamp must advance its
    per-(instrument, field) counter only for these ticks, because the replay source re-derives the
    sequence by iterating only the stored events. Advancing the counter for a dropped ``None`` /
    ``NaN`` / categorical tick (the broker's "no quote" sentinel) would make the next stored tick's
    sequence — and therefore its content-addressed ``event_id`` — differ between live capture and
    replay, breaking the byte-identical-recapture guarantee.
    """
    return is_observation(tick.field_name) and _finite_value(tick.value) is not None


def normalize_event(
    tick: BrokerTick,
    *,
    session_id: str,
    trade_date: date,
    receipt_ts: datetime,
) -> RawMarketEvent | None:
    """Map a push broker tick to an immutable raw event, or ``None`` when it is not storable.

    Returns a stamped :class:`RawMarketEvent` for a tick carrying a finite numeric value, and
    ``None`` for an absent value (``None`` or non-finite) — the loss is recorded as a coverage
    gap by the session summary, never as a fake zero observation. The event id is
    content-addressed on ``(instrument_key, field_name, sequence)`` so a re-delivered tick is
    idempotent. ``canonical_ts`` is the exchange time when present (so out-of-order arrival
    keeps event order), else the receipt time; ``exchange_ts`` falls back to receipt too,
    because the contract requires it. A tick whose field name is in the reserved meta-event
    namespace is refused with :class:`ReservedFieldError`.
    """
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
    """A finite float passes through; ``None``, a non-finite float, or a ``str`` is absence.

    The raw layer's ``value`` is a required finite ``float``: a categorical or missing
    observation has no storable numeric value, so it is reported as absence (``None``) for the
    caller to record as a coverage gap rather than coerced into a record.
    """
    if value is None or isinstance(value, str):
        return None
    if not math.isfinite(value):
        return None
    return float(value)
