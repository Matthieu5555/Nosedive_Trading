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

from algotrading.infra.contracts import RawMarketEvent


def _supersedes(candidate: RawMarketEvent, current: RawMarketEvent) -> bool:
    """Whether ``candidate`` should replace ``current`` as the latest for a field.

    Later ``canonical_ts`` wins; an exact tie is broken by the larger ``event_id``
    so the choice is deterministic and independent of input order.
    """
    if candidate.canonical_ts != current.canonical_ts:
        return candidate.canonical_ts > current.canonical_ts
    return candidate.event_id > current.event_id


def latest_by_field_before(
    events: Sequence[RawMarketEvent], snapshot_ts: datetime
) -> dict[str, RawMarketEvent]:
    """Return the latest event of each field at or before ``snapshot_ts``.

    Events strictly after ``snapshot_ts`` are excluded (the inclusive boundary).
    The mapping is keyed by ``field_name`` (``"bid"``, ``"ask"``, ``"last"``, ...);
    a field with no eligible event is simply absent, never guessed.
    """
    latest: dict[str, RawMarketEvent] = {}
    for candidate in events:
        if candidate.canonical_ts > snapshot_ts:
            continue
        current = latest.get(candidate.field_name)
        if current is None or _supersedes(candidate, current):
            latest[candidate.field_name] = candidate
    return latest
