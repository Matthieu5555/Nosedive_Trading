"""The content-addressed event id — the idempotency primitive the collector relies on.

The one collection seam is the push :class:`~algotrading.infra.collectors.BrokerTick` +
:class:`~algotrading.infra.collectors.MarketDataAdapter` (ADR 0027); the tick type lives with
the collector, not here. What remains at the contract layer is the deterministic event id that
makes capture exactly-once: a tick re-delivered after a reconnect, or re-fed after a
kill/restart, hashes to the *same* id and is therefore deduplicated by the append-only store.
"""

from __future__ import annotations

import hashlib

# ASCII unit separator: cannot occur in an instrument key or a field name, so joining
# on it builds a string whose parts are unambiguous before it is hashed into an id.
_ID_SEPARATOR = "\x1f"


def content_event_id(instrument_key: str, field_name: str, sequence: int) -> str:
    """Deterministic, cross-process-stable event id for one observation.

    Keyed on the observation's content — the instrument, the field, and the feed's stable
    per-session sequence — so a tick re-delivered after a reconnect hashes to the *same* id
    (and is therefore deduplicated by the append-only store), while two genuinely distinct
    observations get distinct ids. It is SHA-256 of a canonical string, never Python's salted
    ``hash()``, so the id is identical across processes and machines without depending on
    ``PYTHONHASHSEED`` (the determinism rule in ``tasks/TESTING.md``).
    """
    payload = _ID_SEPARATOR.join((instrument_key, field_name, str(sequence)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
