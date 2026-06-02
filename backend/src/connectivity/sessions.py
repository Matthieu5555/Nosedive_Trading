"""Concrete broker sessions that need no live broker: the fake and the disk replay.

:class:`FakeBrokerSession` is the in-memory stand-in the whole suite (and the step-1
smoke path) drives — it plays a fixed script of ticks and drops, so reconnect,
re-delivery, and pacing behaviour are all reproducible without TWS/Gateway.
:class:`ReplayBrokerSession` re-emits stored :class:`RawMarketEvent` rows as
:class:`BrokerTick`\\ s, which is the seam stub proving E's same-code-path replay:
the exact same Protocol and the exact same tick type, with no broker involved.

A live IBKR/Nautilus session is just one more implementation of the same
:class:`~connectivity.broker.BrokerSession` Protocol; it is not in this repo because
the broker SDK is not a dependency and the spec forbids live IBKR in the suite. The
adapter that maps the broker's native tick-type enum to ``BrokerTick.field_name`` is
where the only broker-specific code lives.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from contracts import RawMarketEvent, broker_contract_id_from_canonical

from .broker import BrokerTick
from .errors import ConnectionFailed, SessionDisconnected


@dataclass(frozen=True, slots=True)
class ScriptedDrop:
    """A marker in a fake feed script: the session drops here exactly once.

    When the fake reaches it, it marks itself disconnected and raises
    :class:`SessionDisconnected`; the cursor has already advanced past it, so the next
    ``ticks()`` call (after the supervisor reconnects) resumes with the following item.
    ``connect_failures_after`` models a flaky gateway: the reconnect that follows this
    drop fails that many times before succeeding, so the supervisor backs off and the
    outage spans a known, hand-derivable number of seconds.
    """

    reason: str = "synthetic drop"
    connect_failures_after: int = 0


# One item of a fake feed script: deliver a tick, or drop once.
ScriptItem = BrokerTick | ScriptedDrop


class FakeBrokerSession:
    """In-memory :class:`BrokerSession` for tests and the smoke path — no real broker.

    Plays a fixed ``script`` of ticks and drops in order. A *re-delivered* tick is
    simply the same :class:`BrokerTick` appearing again later in the script (typically
    after a drop), so the test author controls exactly what the feed replays on
    reconnect rather than relying on hidden buffering. ``connect_failures`` makes the
    first N connect attempts raise :class:`ConnectionFailed`, to drive the backoff
    schedule.
    """

    def __init__(
        self,
        *,
        chains: Mapping[str, tuple[Mapping[str, object], ...]] | None = None,
        script: Sequence[ScriptItem] = (),
        connect_failures: int = 0,
    ) -> None:
        self._chains: dict[str, tuple[Mapping[str, object], ...]] = dict(chains or {})
        self._script: list[ScriptItem] = list(script)
        self._cursor = 0
        self._connected = False
        self._client_id: int | None = None
        self._subscriptions: set[str] = set()
        self._subscribe_calls: list[str] = []
        self._connect_count = 0
        self._remaining_connect_failures = connect_failures

    def connect(self, client_id: int) -> None:
        if self._remaining_connect_failures > 0:
            self._remaining_connect_failures -= 1
            raise ConnectionFailed(
                f"synthetic connect failure ({self._remaining_connect_failures} remaining)"
            )
        self._connected = True
        self._client_id = client_id
        self._connect_count += 1

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def client_id(self) -> int | None:
        return self._client_id

    @property
    def connect_count(self) -> int:
        """How many connects have succeeded — one more than the reconnect count."""
        return self._connect_count

    @property
    def subscriptions(self) -> frozenset[str]:
        """The set of instrument ids currently subscribed (re-subscribes are a no-op)."""
        return frozenset(self._subscriptions)

    @property
    def subscribe_calls(self) -> tuple[str, ...]:
        """Every subscribe call in order — a re-subscribe after reconnect appears twice."""
        return tuple(self._subscribe_calls)

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        if not self._connected:
            raise SessionDisconnected("option chain requested while disconnected")
        return self._chains.get(symbol, ())

    def subscribe(self, broker_contract_id: str) -> None:
        self._subscriptions.add(broker_contract_id)
        self._subscribe_calls.append(broker_contract_id)

    def ticks(self) -> Iterator[BrokerTick]:
        while self._cursor < len(self._script):
            item = self._script[self._cursor]
            self._cursor += 1
            if isinstance(item, ScriptedDrop):
                self._connected = False
                self._remaining_connect_failures = item.connect_failures_after
                raise SessionDisconnected(item.reason)
            yield item


class ReplayBrokerSession:
    """A :class:`BrokerSession` that re-emits stored events as :class:`BrokerTick`\\ s.

    The seam stub behind E's same-code-path replay: it implements the same Protocol
    and yields the same tick type as the live adapter, with no broker involved. The
    broker contract id a live tick carries is recovered from the stored event's
    canonical instrument key (the key embeds it verbatim), so a replayed tick resolves
    against the universe through the *same* collector code as a live one — not skipped
    as an unknown contract. The authoritative "replay a stored day" path is
    :func:`collectors.replay.replay_day`; this stub proves the type-and-resolution
    identity that lets the live collector run unchanged over replayed ticks.
    """

    def __init__(self, events: Sequence[RawMarketEvent]) -> None:
        self._events = tuple(events)
        self._connected = False

    def connect(self, client_id: int) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        return ()

    def subscribe(self, broker_contract_id: str) -> None:
        return None

    def ticks(self) -> Iterator[BrokerTick]:
        for sequence, event in enumerate(self._events):
            yield BrokerTick(
                broker_contract_id=broker_contract_id_from_canonical(event.instrument_key),
                field_name=event.field_name,
                value=event.value,
                sequence=sequence,
                exchange_ts=event.exchange_ts,
            )
