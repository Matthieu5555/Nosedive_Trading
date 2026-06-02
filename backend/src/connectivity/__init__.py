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
from .sessions import FakeBrokerSession, ReplayBrokerSession, ScriptedDrop, ScriptItem
from .supervisor import (
    BackoffSchedule,
    GapInterval,
    SessionSupervisor,
    SupervisedTick,
    client_id_for,
)

__all__ = [
    "BackoffSchedule",
    "BrokerSession",
    "BrokerTick",
    "ClientIdError",
    "Clock",
    "ConnectionFailed",
    "ConnectivityError",
    "FakeBrokerSession",
    "GapInterval",
    "ManualClock",
    "ReplayBrokerSession",
    "ScriptItem",
    "ScriptedDrop",
    "SessionDisconnected",
    "SessionSupervisor",
    "SupervisedTick",
    "SystemClock",
    "UnknownServiceError",
    "client_id_for",
    "content_event_id",
]
