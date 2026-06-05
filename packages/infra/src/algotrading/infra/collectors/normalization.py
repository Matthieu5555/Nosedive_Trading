"""Normalize a broker tick into a RawMarketEvent — and nothing else.

The collector path does exactly three things: normalize, stamp, persist. This module
is the normalize-and-stamp half: it turns a broker-agnostic :class:`BrokerTick` into
A's :class:`RawMarketEvent`, choosing the three timestamps and a deterministic event
id. No analytics live here, by design — heavy work on the tick path is the fastest way
to drop market data.

The three timestamps:

* ``receipt_ts`` is always when the collector received the tick (from its clock).
* ``canonical_ts`` — the ordering / as-of time — is the exchange time when the feed
  provides one, else the receipt time. An out-of-order tick keeps its (earlier)
  exchange time as ``canonical_ts``: arrival order is plumbing, event order is truth.
* ``exchange_ts`` is required by the contract, so when the feed gives none it falls
  back to the receipt time too. A consumer that needs to know the exchange clock was
  genuinely present cannot infer it from these three fields alone; that bit is not
  representable in the current ``RawMarketEvent`` and widening it is an A-owned change.

A recorded gap is a *meta-event*: it lives in the same append-only stream under a field
name in the reserved ``__`` namespace, so a real observation can never collide with one
and downstream code can filter it out by prefix.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from algotrading.infra.connectivity import BrokerTick, GapInterval, content_event_id
from algotrading.infra.contracts import RawMarketEvent

from .errors import ReservedFieldError

# Field names beginning with this prefix are collector meta-events, never observations.
RESERVED_PREFIX = "__"
# The reserved field name for a recorded missing-interval (gap) event.
GAP_FIELD = "__gap__"

_ID_SEPARATOR = "\x1f"  # ASCII unit separator, as in connectivity.content_event_id


def is_observation(field_name: str) -> bool:
    """True for a real market observation, False for a reserved meta-event field."""
    return not field_name.startswith(RESERVED_PREFIX)


def meta_event_id(instrument_key: str, field_name: str, token: str) -> str:
    """Deterministic, cross-process-stable event id for a meta-event (e.g. a gap).

    Mirrors :func:`connectivity.content_event_id` but keys on a string token (a gap is
    identified by its end timestamp, not a feed sequence), so the same gap recorded
    twice — on a restart that reproduces the outage — hashes to the same id and is
    deduplicated by the append-only store.
    """
    payload = _ID_SEPARATOR.join((instrument_key, field_name, token))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_tick(
    tick: BrokerTick,
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    trade_date: date,
    receipt_ts: datetime,
) -> RawMarketEvent:
    """Normalize and stamp one broker tick into a :class:`RawMarketEvent`.

    The event id is content-addressed on ``(instrument_key, field, sequence)`` so a
    re-delivered tick is idempotent. A tick whose field name is in the reserved
    meta-event namespace is refused with :class:`ReservedFieldError`.
    """
    if not is_observation(tick.field_name):
        raise ReservedFieldError(tick.field_name)
    canonical_ts = tick.exchange_ts if tick.exchange_ts is not None else receipt_ts
    exchange_ts = tick.exchange_ts if tick.exchange_ts is not None else receipt_ts
    return RawMarketEvent(
        session_id=session_id,
        event_id=content_event_id(instrument_key, tick.field_name, tick.sequence),
        instrument_key=instrument_key,
        exchange_ts=exchange_ts,
        receipt_ts=receipt_ts,
        canonical_ts=canonical_ts,
        field_name=tick.field_name,
        value=tick.value,
        trade_date=trade_date,
        underlying=underlying,
    )


def build_gap_event(
    *,
    instrument_key: str,
    underlying: str,
    session_id: str,
    trade_date: date,
    gap: GapInterval,
) -> RawMarketEvent:
    """Build the explicit gap (missing-interval) event for one instrument and outage.

    The value is the outage length in seconds; all three timestamps are the moment the
    stream resumed. The event id is content-addressed on the resumption time, so the
    same gap is never recorded twice.
    """
    ended = gap.ended_at
    return RawMarketEvent(
        session_id=session_id,
        event_id=meta_event_id(instrument_key, GAP_FIELD, ended.isoformat()),
        instrument_key=instrument_key,
        exchange_ts=ended,
        receipt_ts=ended,
        canonical_ts=ended,
        field_name=GAP_FIELD,
        value=gap.duration_seconds(),
        trade_date=trade_date,
        underlying=underlying,
    )
