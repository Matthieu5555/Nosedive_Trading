"""Broker-agnostic connectivity: the session seam, the backoff supervisor, fakes.

Import the broker-agnostic :class:`BrokerTick` / :class:`BrokerSession`, the
:class:`SessionSupervisor` (the one home for reconnect-with-backoff and the client-id
convention), an injected :class:`Clock`, and the no-broker :class:`FakeBrokerSession`
/ :class:`ReplayBrokerSession` from here. No broker SDK type is exported, because none
crosses this boundary.
"""

from __future__ import annotations

from .broker import BrokerSession, BrokerTick, content_event_id
from .clock import Clock, ManualClock, SystemClock
from .errors import (
    ClientIdError,
    ConnectionFailed,
    ConnectivityError,
    SessionDisconnected,
    UnknownServiceError,
)
from .ibkr_session import IbkrBrokerSession, ibkr_field_name
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
from .sessions import FakeBrokerSession, ReplayBrokerSession, ScriptedDrop, ScriptItem
from .supervisor import (
    BackoffSchedule,
    GapInterval,
    SessionSupervisor,
    SupervisedTick,
    client_id_for,
)

__all__ = [
    "ENTITLEMENT",
    "OTHER",
    "PACING",
    "BackoffSchedule",
    "BrokerSession",
    "BrokerTick",
    "ClientIdError",
    "Clock",
    "ConnectionFailed",
    "ConnectivityError",
    "FakeBrokerSession",
    "FeedNotice",
    "GapInterval",
    "IbkrBrokerSession",
    "ManualClock",
    "MarketDataStatus",
    "ReplayBrokerSession",
    "ScriptItem",
    "ScriptedDrop",
    "SessionDisconnected",
    "SessionSupervisor",
    "SupervisedTick",
    "SystemClock",
    "UnknownServiceError",
    "assess_market_data",
    "classify_feed_notice",
    "client_id_for",
    "content_event_id",
    "ibkr_field_name",
    "market_data_type_name",
]
