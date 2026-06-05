"""The daily collector summary: counts, missing intervals, reconnects, coverage.

A pure function over the events a session persisted plus a little session metadata, so
it is checkable against a hand-derived expected summary independent of the collector
that produced the events. Observations and gap meta-events are told apart by the
reserved field prefix; coverage is measured against the instruments that were actually
subscribed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import RawMarketEvent

from .normalization import GAP_FIELD, is_observation
from .notices import ENTITLEMENT, PACING, FeedNotice


@dataclass(frozen=True, slots=True)
class CollectorSummary:
    """What one collection session captured, in numbers.

    ``event_count`` counts real observations (not gap meta-events); ``gap_count`` is
    the recorded missing intervals; ``coverage_ratio`` is the fraction of subscribed
    instruments that produced at least one observation.
    """

    session_id: str
    trade_date: date
    event_count: int
    gap_count: int
    reconnect_count: int
    subscribed_count: int
    covered_count: int
    per_field_counts: tuple[tuple[str, int], ...]
    pacing_failures: int
    entitlement_failures: int

    @property
    def coverage_ratio(self) -> float:
        """Fraction of subscribed instruments that produced at least one observation."""
        if self.subscribed_count == 0:
            return 0.0
        return self.covered_count / self.subscribed_count


def summarize_session(
    events: Sequence[RawMarketEvent],
    *,
    session_id: str,
    trade_date: date,
    subscribed_keys: Collection[str],
    reconnect_count: int,
    notices: Sequence[FeedNotice] = (),
) -> CollectorSummary:
    """Summarize a session's persisted events into a :class:`CollectorSummary`."""
    per_field: dict[str, int] = defaultdict(int)
    covered: set[str] = set()
    gap_count = 0
    for event in events:
        if event.field_name == GAP_FIELD:
            gap_count += 1
        elif is_observation(event.field_name):
            per_field[event.field_name] += 1
            covered.add(event.instrument_key)
    subscribed = set(subscribed_keys)
    return CollectorSummary(
        session_id=session_id,
        trade_date=trade_date,
        event_count=sum(per_field.values()),
        gap_count=gap_count,
        reconnect_count=reconnect_count,
        subscribed_count=len(subscribed),
        covered_count=len(covered & subscribed),
        per_field_counts=tuple(sorted(per_field.items())),
        pacing_failures=sum(1 for notice in notices if notice.kind == PACING),
        entitlement_failures=sum(1 for notice in notices if notice.kind == ENTITLEMENT),
    )
