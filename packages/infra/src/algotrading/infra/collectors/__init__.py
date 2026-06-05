"""Append-only, loss-aware market-data collection over the raw layer.

One collection seam (ADR 0027): a broker adapter is a push :class:`MarketDataAdapter`
(``subscribe`` / ``set_tick_callback`` / ``set_fault_callback`` / ``unsubscribe_all``); the
:class:`RawCollector` normalizes each unified :class:`BrokerTick` into a
:class:`~algotrading.infra.contracts.RawMarketEvent`, content-addresses its ``event_id`` for
exactly-once capture, and persists batches into the store. Reconnect/backoff live in the
session beneath the adapter (``connectivity.SessionSupervisor``); the collector turns each
reported outage into a loss-aware gap meta-event.

Import the collector and the unified tick, the pure gap/meta helpers and the reserved-field
predicate, the daily :class:`CollectorSummary` and its pure builder, the feed-notice
classification, and the disk replay (:func:`replay_day` read + :class:`ReplaySource` push
source) from here. All four live broker leaves (Deribit, Saxo, IBKR) ride this one seam.
"""

from __future__ import annotations

from .collector import FeedFault, MarketDataAdapter, RawCollector
from .errors import CollectorError, ReservedFieldError
from .live import SequenceStamping
from .normalization import GAP_FIELD, build_gap_event, meta_event_id
from .normalize import (
    RESERVED_PREFIX,
    BrokerTick,
    is_observation,
    normalize_event,
)
from .notices import ENTITLEMENT, OTHER, PACING, FeedNotice, classify_feed_notice
from .replay import ReplaySource, next_sequence, replay_day
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
    "FeedFault",
    "FeedNotice",
    "MarketDataAdapter",
    "RawCollector",
    "ReplaySource",
    "ReservedFieldError",
    "SequenceStamping",
    "build_gap_event",
    "classify_feed_notice",
    "is_observation",
    "meta_event_id",
    "next_sequence",
    "normalize_event",
    "replay_day",
    "summarize_session",
]
