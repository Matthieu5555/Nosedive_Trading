"""Append-only, loss-aware market-data collection over A's raw layer.

Import the :class:`MarketDataCollector` (subscribe, normalize, stamp, persist), the
pure normalization helpers and the reserved gap field, the daily
:class:`CollectorSummary` and its pure builder, the feed-notice classification, and the
disk :func:`replay_day` from here.
"""

from __future__ import annotations

from .collector import MarketDataCollector
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

__all__ = [
    "ENTITLEMENT",
    "GAP_FIELD",
    "OTHER",
    "PACING",
    "RESERVED_PREFIX",
    "CollectorError",
    "CollectorSummary",
    "FeedNotice",
    "MarketDataCollector",
    "ReservedFieldError",
    "build_gap_event",
    "classify_feed_notice",
    "is_observation",
    "meta_event_id",
    "normalize_tick",
    "replay_day",
    "summarize_session",
]
