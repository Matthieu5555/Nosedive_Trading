"""Hold the broker session: backoff reconnect in one place, client-id convention.

The supervisor is the single home for connect/reconnect/retry behaviour — the blueprint's
"reconnect and retry behavior lives in exactly one place" — and it lives *beneath* the
push :class:`~algotrading.infra.collectors.MarketDataAdapter` (ADR 0027). It owns a session,
a client id, an injected clock, and a backoff schedule. It re-subscribes after every reconnect
and records each outage as a :class:`GapInterval`, which the collector turns into a loss-aware
gap meta-event. It no longer defines a tick type or a pull loop: the adapter pushes ticks at
the collector; the supervisor only manages the session lifecycle under it.

The client-id convention lives here too: each service draws from its own reserved band so two
services connecting to the same gateway can never request the same id.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .clock import Clock
from .errors import ClientIdError, ConnectionFailed, UnknownServiceError

# Reserved client-id bands, one per service. Bands are spaced by _BAND_WIDTH so a
# service's instance ids (band + instance) never reach into the next service's band.
# A live gateway rejects a second connection that reuses a client id, so two services
# must never collide; drawing from disjoint bands guarantees they cannot.
_CLIENT_ID_BANDS: dict[str, int] = {
    "universe": 1000,
    "collector": 2000,
    "replay": 3000,
    "smoke": 9000,
}
_BAND_WIDTH = 1000

# Cap the backoff exponent so base * factor**attempt can never overflow a float on a
# very long outage. Once the delay reaches the cap it stays there, so clamping the
# exponent well past that point changes no observable delay.
_MAX_BACKOFF_EXPONENT = 32


def client_id_for(service: str, instance: int = 0) -> int:
    """Return the gateway client id for one instance of a named service.

    Different services get ids from disjoint bands, so they never collide; instances
    of the same service get distinct ids within its band. An unknown service or an
    out-of-band instance is refused with diagnostics rather than handed a colliding
    id.
    """
    try:
        band = _CLIENT_ID_BANDS[service]
    except KeyError:
        raise UnknownServiceError(service, tuple(sorted(_CLIENT_ID_BANDS))) from None
    if not 0 <= instance < _BAND_WIDTH:
        raise ClientIdError(service, instance, _BAND_WIDTH)
    return band + instance


class SupervisedSession(Protocol):
    """The minimal broker session lifecycle the supervisor manages — no tick pulling.

    A concrete session connects under a client id, subscribes instruments, and reports
    whether it is currently connected; the push adapter feeds ticks at the collector
    separately. Every type here is broker-agnostic: ids and symbols are strings, chain rows
    are plain mappings.
    """

    def connect(self, client_id: int) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]: ...

    def subscribe(self, broker_contract_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class BackoffSchedule:
    """Exponential-with-cap reconnect delays. Deterministic: no jitter.

    ``delay_for(attempt) = min(cap, base * factor**attempt)`` with ``attempt`` counted
    from 0. For the defaults (base=1, factor=2, cap=30) the documented delay sequence
    is 1, 2, 4, 8, 16, 30, 30, ... seconds. There is deliberately no jitter, so the
    sequence is exactly assertable against this formula — the determinism the
    reconnect test pins.
    """

    base_seconds: float = 1.0
    factor: float = 2.0
    cap_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before retry number ``attempt`` (0-based)."""
        if attempt < 0:
            raise ValueError(f"backoff attempt must be >= 0, got {attempt}")
        exponent = min(attempt, _MAX_BACKOFF_EXPONENT)
        return min(self.cap_seconds, self.base_seconds * self.factor**exponent)


@dataclass(frozen=True, slots=True)
class GapInterval:
    """An outage: the window between a drop and the resumption after reconnect."""

    started_at: datetime
    ended_at: datetime

    def duration_seconds(self) -> float:
        """Length of the outage in seconds."""
        return (self.ended_at - self.started_at).total_seconds()


class SessionSupervisor:
    """Owns the broker session: one place for connect/reconnect-with-backoff + ids.

    Construct it with a session, the client id it should connect under, an injected clock, and
    an optional backoff schedule. ``connect`` establishes the session (retrying failures on the
    backoff schedule); ``recover`` reconnects and re-subscribes across a mid-stream drop,
    recording the outage as a :class:`GapInterval` the caller hands to the collector. It is the
    single home for reconnect; nothing above it owns that behaviour.
    """

    def __init__(
        self,
        session: SupervisedSession,
        *,
        client_id: int,
        clock: Clock,
        backoff: BackoffSchedule | None = None,
        max_reconnect_attempts: int | None = None,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._clock = clock
        self._backoff = backoff if backoff is not None else BackoffSchedule()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._subscriptions: list[str] = []
        self.reconnects: list[GapInterval] = []

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def reconnect_count(self) -> int:
        """How many times the session has dropped and been recovered."""
        return len(self.reconnects)

    def connect(self) -> None:
        """Establish the session, retrying connect failures on the backoff schedule."""
        self._connect_with_backoff()

    def is_healthy(self) -> bool:
        """Whether the underlying session currently reports itself connected.

        A real implementation would also round-trip a heartbeat request here; the
        seam is the same either way.
        """
        return self._session.is_connected()

    def disconnect(self) -> None:
        self._session.disconnect()

    def subscribe(self, broker_contract_id: str) -> None:
        """Subscribe to an instrument and remember it, so reconnects re-subscribe it."""
        self._subscriptions.append(broker_contract_id)
        self._session.subscribe(broker_contract_id)

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        """Pass an option-chain discovery request through to the session."""
        return self._session.request_option_chain(symbol)

    def recover(self, dropped_at: datetime) -> GapInterval:
        """Reconnect and re-subscribe after a mid-stream drop, recording the outage.

        Reconnects on the backoff schedule from the moment the link ``dropped_at``,
        re-subscribes every instrument, records the :class:`GapInterval` that just ended, and
        returns it so the caller can hand it to the collector as a loss-aware gap. This is the
        one place reconnect happens; the adapter resumes pushing ticks once it returns.
        """
        self._connect_with_backoff()
        self._resubscribe()
        gap = GapInterval(started_at=dropped_at, ended_at=self._clock.now())
        self.reconnects.append(gap)
        return gap

    def _connect_with_backoff(self) -> None:
        attempt = 0
        while True:
            try:
                self._session.connect(self._client_id)
                return
            except ConnectionFailed:
                if (
                    self._max_reconnect_attempts is not None
                    and attempt >= self._max_reconnect_attempts
                ):
                    raise
                self._clock.sleep(self._backoff.delay_for(attempt))
                attempt += 1

    def _resubscribe(self) -> None:
        for broker_contract_id in self._subscriptions:
            self._session.subscribe(broker_contract_id)
