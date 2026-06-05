"""Append-only, loss-aware market-data collection over the raw layer.

Import the pure normalization helpers and the reserved gap field, the daily
:class:`CollectorSummary` and its pure builder, the feed-notice classification, and the
disk :func:`replay_day` from here.

This package carries two collectors that coexist by design (ADR 0023). The relocated
helpers above feed the analytics pipeline. The vendored M5 push collector
(:class:`RawCollector` + :class:`BrokerTick` + :class:`MarketDataAdapter` +
:class:`FeedFault`, re-exported below) is the capture path the **kept** Saxo/Deribit
broker leaves ride on — ADR 0023 keeps Vincent's adapters as survivors, so this is a
permanent export, not a transitional one. (IBKR captures through Nautilus instead.)
"""

from __future__ import annotations

# --- re-exports of the vendored M5 capture slice (kept per ADR 0023; Saxo/Deribit ride it) ---
from .collector import (  # noqa: E402  (vendored push-collector module)
    EventWriter,
    FeedFault,
    MarketDataAdapter,
    RawCollector,
)
from .errors import CollectorError, ReservedFieldError
from .normalization import (
    GAP_FIELD,
    RESERVED_PREFIX,
    build_gap_event,
    is_observation,
    meta_event_id,
    normalize_tick,
)
from .normalize import BrokerTick, normalize_event  # noqa: E402  (vendored EAV tick)
from .notices import ENTITLEMENT, OTHER, PACING, FeedNotice, classify_feed_notice
from .replay import replay_day
from .summary import CollectorSummary, summarize_session

__all__ = [
    "ENTITLEMENT",
    "GAP_FIELD",
    "OTHER",
    "PACING",
    "RESERVED_PREFIX",
    "BrokerTick",
    "CollectorError",
    "CollectorSummary",
    "EventWriter",
    "FeedFault",
    "FeedNotice",
    "MarketDataAdapter",
    "RawCollector",
    "ReservedFieldError",
    "build_gap_event",
    "classify_feed_notice",
    "is_observation",
    "meta_event_id",
    "normalize_event",
    "normalize_tick",
    "replay_day",
    "summarize_session",
]
