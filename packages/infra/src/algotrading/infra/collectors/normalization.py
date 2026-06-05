"""Loss-aware meta-events: the recorded gap, and its content-addressed id.

The collector's tick path (normalize, stamp, persist) lives in :mod:`normalize`. This
module owns the *other* thing written to the same append-only stream: the explicit
missing-interval (gap) meta-event. A gap lives under a field name in the reserved ``__``
namespace, so a real observation can never collide with one and downstream code filters
it out by prefix.

A gap is content-addressed on its resumption timestamp, so the same outage recorded twice
— on a restart that reproduces it — hashes to the same id and the append-only store keeps
exactly one copy.
"""

from __future__ import annotations

import hashlib
from datetime import date

from algotrading.infra.connectivity import GapInterval
from algotrading.infra.contracts import RawMarketEvent

# The reserved field name for a recorded missing-interval (gap) event.
GAP_FIELD = "__gap__"

_ID_SEPARATOR = "\x1f"  # ASCII unit separator, as in contracts.content_event_id


def meta_event_id(instrument_key: str, field_name: str, token: str) -> str:
    """Deterministic, cross-process-stable event id for a meta-event (e.g. a gap).

    Mirrors :func:`contracts.content_event_id` but keys on a string token (a gap is
    identified by its end timestamp, not a feed sequence), so the same gap recorded twice —
    on a restart that reproduces the outage — hashes to the same id and is deduplicated by
    the append-only store.
    """
    payload = _ID_SEPARATOR.join((instrument_key, field_name, token))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    stream resumed. The event id is content-addressed on the resumption time, so the same
    gap is never recorded twice.
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
