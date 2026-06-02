"""Time as an injected dependency, so backoff and timestamps are deterministic.

Production code takes a :class:`Clock` rather than calling ``datetime.now`` or
``time.sleep`` directly. The real one is :class:`SystemClock`. Tests and
deterministic replays use :class:`ManualClock`, which never really sleeps: it
records each requested delay and advances its own clock by exactly that amount.
That is what lets the reconnect test assert the precise backoff delay sequence
without waiting on wall-clock time, and lets the collector stamp ticks at
predictable, reproducible times.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

# A fixed, timezone-aware default start for the manual clock, so a test that does not
# care about the absolute instant still gets reproducible, tz-aware timestamps.
_DEFAULT_START = datetime(2026, 6, 1, 13, 30, 0, tzinfo=UTC)


@runtime_checkable
class Clock(Protocol):
    """A source of the current time and a way to wait for a number of seconds."""

    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The real clock: timezone-aware UTC ``now`` and a real ``sleep``."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(slots=True)
class ManualClock:
    """A deterministic clock that records sleeps instead of performing them.

    ``now`` starts at ``start`` and advances by exactly the seconds passed to each
    ``sleep`` call; every requested delay is appended to ``sleeps`` so a test can
    assert the backoff schedule. No wall-clock time passes, so the suite stays fast
    and the timestamps it stamps are fully reproducible. Use :meth:`advance` to move
    time forward between ticks without it counting as a backoff sleep.
    """

    start: datetime = _DEFAULT_START
    sleeps: list[float] = field(default_factory=list)
    _now: datetime = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError(f"ManualClock start must be timezone-aware, got {self.start!r}")
        self._now = self.start

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now = self._now + timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        """Move time forward without recording a backoff sleep (e.g. between ticks)."""
        self._now = self._now + timedelta(seconds=seconds)
