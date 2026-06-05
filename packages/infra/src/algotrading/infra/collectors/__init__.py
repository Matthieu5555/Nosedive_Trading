"""Append-only, loss-aware market-data collection over the raw layer.

Import the pure normalization helpers and the reserved gap field, the daily
:class:`CollectorSummary` and its pure builder, the feed-notice classification, and the
disk :func:`replay_day` from here. (The :class:`MarketDataCollector` driver lands in
C1 commit 2, once the forked ``collector.py`` below is removed.)

TRANSITIONAL (C1 commit 1): the forked M5 ``collector.py``/``normalize.py`` still live
here and the broker leaves still import :class:`BrokerTick` / :class:`FeedFault` from
them. Both are removed and the leaves retargeted onto the frozen seam in C1 commit 2
(see ADR 0023). Until then this package exports the union so the gate stays green.
"""

from __future__ import annotations

from .errors import CollectorError, ReservedFieldError
from .normalization import (
    GAP_FIELD,
    RESERVED_PREFIX,
    build_gap_event,
    is_observation,
    meta_event_id,
    normalize_tick,
)
from .notices import ENTITLEMENT, OTHER, PACING, FeedNotice, classify_feed_notice
from .replay import replay_day
from .summary import CollectorSummary, summarize_session

# --- transitional re-exports of the forked M5 slice (removed in C1 commit 2) ---------
from .collector import FeedFault  # noqa: E402  (forked RawCollector module)
from .normalize import BrokerTick  # noqa: E402  (forked EAV tick)

__all__ = [
    "ENTITLEMENT",
    "GAP_FIELD",
    "OTHER",
    "PACING",
    "RESERVED_PREFIX",
    "BrokerTick",
    "CollectorError",
    "CollectorSummary",
    "FeedFault",
    "FeedNotice",
    "ReservedFieldError",
    "build_gap_event",
    "classify_feed_notice",
    "is_observation",
    "meta_event_id",
    "normalize_tick",
    "replay_day",
    "summarize_session",
]
