"""Broker-agnostic connectivity: session lifecycle, the backoff supervisor, clocks.

The supervisor (:class:`SessionSupervisor`) is the one home for reconnect-with-backoff, the
client-id convention, and loss-aware :class:`GapInterval` recording — it sits *beneath* the
push :class:`~algotrading.infra.collectors.MarketDataAdapter` (ADR 0027) and manages only the
session lifecycle, never a tick type or a pull loop. Import it, an injected :class:`Clock`, the
market-data entitlement policy, and the content-addressed :func:`content_event_id` (the
idempotency primitive, re-exported from the frozen ``contracts`` seam) from here. No broker SDK
type is exported — the concrete live adapters live in the ``infra-{ibkr,saxo,deribit}`` leaves.
"""

from __future__ import annotations

from algotrading.infra.contracts import content_event_id

from .clock import Clock, ManualClock, SystemClock
from .errors import (
    ClientIdError,
    ConnectionFailed,
    ConnectivityError,
    SessionDisconnected,
    UnknownServiceError,
)
from .market_data_policy import (
    ENTITLEMENT,
    OTHER,
    PACING,
    FeedNotice,
    MarketDataStatus,
    assess_market_data,
    classify_feed_notice,
    market_data_type_name,
)
from .supervisor import (
    BackoffSchedule,
    BrokerConfig,
    GapInterval,
    SessionSupervisor,
    SupervisedSession,
    load_broker_config,
)

__all__ = [
    "ENTITLEMENT",
    "OTHER",
    "PACING",
    "BackoffSchedule",
    "BrokerConfig",
    "ClientIdError",
    "Clock",
    "ConnectionFailed",
    "ConnectivityError",
    "FeedNotice",
    "GapInterval",
    "ManualClock",
    "MarketDataStatus",
    "SessionDisconnected",
    "SessionSupervisor",
    "SupervisedSession",
    "SystemClock",
    "UnknownServiceError",
    "assess_market_data",
    "classify_feed_notice",
    "load_broker_config",
    "content_event_id",
    "market_data_type_name",
]
