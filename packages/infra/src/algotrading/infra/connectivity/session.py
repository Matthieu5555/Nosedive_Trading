"""Broker session lifecycle: reliable transport lifecycle for any broker adapter.

Owns the connection state machine and nothing else — no volatility mathematics. A session
connects, proves itself with a broker round-trip, heart-beats to stay healthy, and reconnects
with exponential backoff and jitter when the link degrades. The broker is reached through an
injected transport, so the lifecycle is exercised deterministically with a fake while the
concrete IBKR client stays swappable. Clock, sleep and jitter source are injected too: nothing
here reads wall-clock time or sleeps except through those seams.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from algotrading.core.config import LoadedConfig
from algotrading.core.log import get_logger

_log = get_logger(__name__)


class SessionState(StrEnum):
    """Lifecycle states of a broker session."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"


class TransportError(Exception):
    """Raised by a BrokerTransport when a broker operation fails."""


class BrokerTransport(Protocol):
    """The minimal broker transport a session drives; the real impl wraps the IBKR client."""

    def open(self, host: str, port: int, client_id: int) -> None:
        """Establish the broker connection. Raise TransportError on failure."""
        ...

    def close(self) -> None:
        """Tear down the broker connection."""
        ...

    def current_time(self) -> datetime:
        """One broker round-trip; return the broker clock or raise TransportError."""
        ...


@dataclass(frozen=True)
class ReconnectPolicy:
    """Exponential-backoff-with-jitter parameters for reconnection."""

    base_delay_seconds: float
    max_delay_seconds: float
    multiplier: float
    max_jitter_seconds: float
    max_attempts: int  # 0 means retry indefinitely


@dataclass(frozen=True)
class SessionConfig:
    """Connection parameters, loaded from the versioned ``broker.yaml``."""

    host: str
    port: int
    client_id: int
    heartbeat_interval_seconds: float
    heartbeat_max_age_seconds: float
    clock_skew_tolerance_seconds: float
    reconnect: ReconnectPolicy

    @classmethod
    def from_config(cls, config: LoadedConfig) -> SessionConfig:
        """Build a SessionConfig from the ``broker`` section of a loaded config."""
        section = config.data["broker"]
        rc = section["reconnect"]
        return cls(
            host=str(section["host"]),
            port=int(section["port"]),
            client_id=int(section["client_id"]),
            heartbeat_interval_seconds=float(section["heartbeat_interval_seconds"]),
            heartbeat_max_age_seconds=float(section["heartbeat_max_age_seconds"]),
            clock_skew_tolerance_seconds=float(section["clock_skew_tolerance_seconds"]),
            reconnect=ReconnectPolicy(
                base_delay_seconds=float(rc["base_delay_seconds"]),
                max_delay_seconds=float(rc["max_delay_seconds"]),
                multiplier=float(rc["multiplier"]),
                max_jitter_seconds=float(rc["max_jitter_seconds"]),
                max_attempts=int(rc["max_attempts"]),
            ),
        )


def validate_client_id(client_id: int, reserved: frozenset[int]) -> None:
    """Raise when ``client_id`` collides with another configured service's id."""
    if client_id in reserved:
        raise ValueError(f"client_id {client_id} already reserved by another service")


def next_delay(attempt: int, policy: ReconnectPolicy, rng: random.Random) -> float:
    """Backoff delay for a 0-based reconnect attempt: exponential, capped, plus jitter."""
    raw = policy.base_delay_seconds * (policy.multiplier**attempt)
    capped = min(raw, policy.max_delay_seconds)
    return capped + rng.uniform(0.0, policy.max_jitter_seconds)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BrokerSession:
    """Drives a broker connection through its lifecycle states.

    Never claims health until a broker round-trip has succeeded, and reconnects with
    exponential backoff and jitter when the link drops or a heartbeat fails.
    """

    def __init__(
        self,
        transport: BrokerTransport,
        config: SessionConfig,
        *,
        reserved_client_ids: frozenset[int] = frozenset(),
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        validate_client_id(config.client_id, reserved_client_ids)
        self._transport = transport
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._state = SessionState.DISCONNECTED
        self._last_heartbeat: datetime | None = None
        self._last_broker_time: datetime | None = None
        self._round_trip_ok = False
        self._reconnects = 0

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def reconnect_count(self) -> int:
        """How many times the session has recovered via a successful reconnect this run."""
        return self._reconnects

    def _transition(self, new_state: SessionState) -> None:
        if new_state is self._state:
            return
        old = self._state
        self._state = new_state
        _log.info(
            "broker session state transition",
            extra={
                "from_state": str(old),
                "to_state": str(new_state),
                "client_id": self._config.client_id,
            },
        )

    def _round_trip(self, now: datetime) -> bool:
        """Fetch the broker clock; on success record heartbeat, broker time, and warn on skew."""
        try:
            broker_time = self._transport.current_time()
        except TransportError:
            return False
        self._round_trip_ok = True
        self._last_heartbeat = now
        self._last_broker_time = broker_time
        skew = (broker_time - now).total_seconds()
        if abs(skew) > self._config.clock_skew_tolerance_seconds:
            _log.warning(
                "broker clock skew beyond tolerance",
                extra={
                    "skew_seconds": skew,
                    "tolerance_seconds": self._config.clock_skew_tolerance_seconds,
                    "client_id": self._config.client_id,
                },
            )
        return True

    def connect(self) -> None:
        """Open the session and prove it with a broker round-trip; reconnect on failure."""
        self._transition(SessionState.CONNECTING)
        try:
            self._transport.open(self._config.host, self._config.port, self._config.client_id)
        except TransportError:
            _log.warning("connect failed to open transport (client_id=%s)", self._config.client_id)
            self._transition(SessionState.DISCONNECTED)
            self._reconnect()
            return
        if self._round_trip(self._clock()):
            self._transition(SessionState.CONNECTED)
        else:
            self._transition(SessionState.DISCONNECTED)
            self._reconnect()

    def _reconnect(self) -> None:
        """Retry the connection with backoff until a round-trip succeeds or attempts run out."""
        self._transition(SessionState.RECONNECTING)
        policy = self._config.reconnect
        attempt = 0
        while policy.max_attempts == 0 or attempt < policy.max_attempts:
            self._sleep(next_delay(attempt, policy, self._rng))
            try:
                self._transport.open(self._config.host, self._config.port, self._config.client_id)
            except TransportError:
                _log.warning(
                    "reconnect attempt %d failed to open (client_id=%s)",
                    attempt,
                    self._config.client_id,
                )
                attempt += 1
                continue
            if self._round_trip(self._clock()):
                self._reconnects += 1
                self._transition(SessionState.CONNECTED)
                return
            attempt += 1
        _log.error(
            "reconnect exhausted after %d attempts (client_id=%s)", attempt, self._config.client_id
        )
        self._transition(SessionState.DISCONNECTED)
        raise TransportError(f"reconnect failed after {attempt} attempts")

    def pulse(self) -> None:
        """One heartbeat tick: round-trip the broker; degrade and reconnect if it fails."""
        if self._round_trip(self._clock()):
            if self._state is not SessionState.CONNECTED:
                self._transition(SessionState.CONNECTED)
        else:
            _log.warning("heartbeat round-trip failed (client_id=%s)", self._config.client_id)
            self._transition(SessionState.DEGRADED)
            self._reconnect()

    def heartbeat_loop(self) -> None:
        """Pulse forever at the configured interval — a thin wrapper over ``pulse``."""
        while True:
            self.pulse()
            self._sleep(self._config.heartbeat_interval_seconds)

    def disconnect(self) -> None:
        """Close the broker connection and return to the DISCONNECTED state."""
        self._transport.close()
        self._round_trip_ok = False
        self._transition(SessionState.DISCONNECTED)

    def heartbeat_age_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds since the last successful round-trip, or None if there has been none."""
        if self._last_heartbeat is None:
            return None
        moment = now if now is not None else self._clock()
        return (moment - self._last_heartbeat).total_seconds()

    @property
    def broker_time(self) -> datetime | None:
        """Broker clock captured at the last successful round-trip; None if there has been none."""
        return self._last_broker_time

    def clock_skew_seconds(self) -> float | None:
        """Broker-minus-local clock offset at the last round-trip; None if there has been none."""
        if self._last_broker_time is None or self._last_heartbeat is None:
            return None
        return (self._last_broker_time - self._last_heartbeat).total_seconds()

    def is_clock_synced(self) -> bool | None:
        """Whether the last round-trip's skew is within tolerance; None before any round-trip."""
        skew = self.clock_skew_seconds()
        if skew is None:
            return None
        return abs(skew) <= self._config.clock_skew_tolerance_seconds

    def is_healthy(self, now: datetime | None = None) -> bool:
        """Healthy only when CONNECTED, a round-trip has succeeded, and the heartbeat is fresh."""
        if self._state is not SessionState.CONNECTED or not self._round_trip_ok:
            return False
        age = self.heartbeat_age_seconds(now)
        return age is not None and age <= self._config.heartbeat_max_age_seconds
