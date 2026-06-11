"""The as-of read — the look-ahead boundary of the whole platform.

``latest_by_field_before`` answers "what was the latest value of each field at the
snapshot instant?" for one instrument's events. Two rules make it the defense
against look-ahead bias and the foundation of deterministic replay:

* **Inclusive boundary.** An event stamped *exactly* at ``snapshot_ts`` is
  information known at ``snapshot_ts`` and is used; an event even one microsecond
  later is the future and is never used. The comparison is ``canonical_ts <=
  snapshot_ts`` — strictly-later events are dropped. (canonical_ts is the field A
  designates for ordering and as-of reads.)

* **Order independence.** The result does not depend on the order events were fed
  in: the latest ``canonical_ts`` per field wins, and exact-timestamp ties are
  broken deterministically by ``event_id``. Feeding the same events shuffled
  yields the same result, which is what makes a snapshot reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import duckdb
from algotrading.infra.contracts import RawMarketEvent

# The one as-of resolution, expressed the way the storage layer already does it
# (membership.py:238-269): a DuckDB QUALIFY row_number() window. PARTITION BY
# field_name gives one winner per field; ORDER BY canonical_ts DESC, event_id DESC
# is the exact transcription of the old _supersedes rule — later canonical_ts wins,
# an exact-timestamp tie is broken by the larger event_id. The WHERE enforces the
# inclusive boundary (canonical_ts <= snapshot_ts; strictly-later events dropped).
# DuckDB's TIMESTAMPTZ comparison and its VARCHAR ordering match Python's datetime
# and str comparisons used by the prior loop, so the chosen winner is identical
# (verified by the shuffle/tie property tests in test_snapshots.py).
_LATEST_BY_FIELD_SQL = """
SELECT field_name, session_id, event_id
FROM events
WHERE canonical_ts <= $snapshot_ts
QUALIFY row_number() OVER (
    PARTITION BY field_name
    ORDER BY canonical_ts DESC, event_id DESC
) = 1
"""


def latest_by_field_before(
    events: Sequence[RawMarketEvent], snapshot_ts: datetime
) -> dict[str, RawMarketEvent]:
    """Return the latest event of each field at or before ``snapshot_ts``.

    Events strictly after ``snapshot_ts`` are excluded (the inclusive boundary).
    The mapping is keyed by ``field_name`` (``"bid"``, ``"ask"``, ``"last"``, ...);
    a field with no eligible event is simply absent, never guessed.

    The winner of each field is chosen by a DuckDB window query — the same
    ``QUALIFY row_number()`` as-of idiom the membership resolver uses — but the
    returned values are the original :class:`RawMarketEvent` objects, never
    round-tripped through the engine: the query only decides *which* event wins
    (by ``(session_id, event_id)``, the raw-event key), and the untouched original
    is handed back.
    """
    if not events:
        return {}
    by_key = {(event.session_id, event.event_id): event for event in events}
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute(
            "CREATE TABLE events("
            "session_id VARCHAR, event_id VARCHAR, field_name VARCHAR, "
            "canonical_ts TIMESTAMPTZ)"
        )
        connection.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            [
                (event.session_id, event.event_id, event.field_name, event.canonical_ts)
                for event in events
            ],
        )
        winners = connection.execute(
            _LATEST_BY_FIELD_SQL, {"snapshot_ts": snapshot_ts}
        ).fetchall()
    finally:
        connection.close()
    return {
        field_name: by_key[(session_id, event_id)]
        for field_name, session_id, event_id in winners
    }
