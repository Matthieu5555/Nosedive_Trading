from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import duckdb
from algotrading.infra.contracts import RawMarketEvent

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
