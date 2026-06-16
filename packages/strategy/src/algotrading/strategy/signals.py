from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from .contract import SignalKind


@dataclass(frozen=True, slots=True)
class SignalReading:

    kind: SignalKind
    value: float
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class SignalSnapshot:

    as_of: date
    readings: tuple[SignalReading, ...] = field(default_factory=tuple)

    def latest(self, kind: SignalKind, *, subject: str | None = None) -> SignalReading | None:
        for reading in self.readings:
            if reading.kind == kind and reading.subject == subject:
                return reading
        return None

    def all_of(self, kind: SignalKind) -> tuple[SignalReading, ...]:
        return tuple(reading for reading in self.readings if reading.kind == kind)


def signal_snapshot(as_of: date, readings: Mapping[SignalKind, float]) -> SignalSnapshot:
    return SignalSnapshot(
        as_of=as_of,
        readings=tuple(SignalReading(kind=kind, value=value) for kind, value in readings.items()),
    )
