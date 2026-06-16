from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

_DEFAULT_START = datetime(2026, 6, 1, 13, 30, 0, tzinfo=UTC)


@runtime_checkable
class Clock(Protocol):

    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(slots=True)
class ManualClock:

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
        self._now = self._now + timedelta(seconds=seconds)
