from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class DayCountConvention(StrEnum):

    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360 = "30/360"


def _interval_days(start: date, end: date) -> float:
    return (end - start).total_seconds() / 86_400.0


def _thirty_360_days(start: date, end: date) -> float:
    d1 = min(start.day, 30)
    d2 = 30 if (d1 == 30 and end.day == 31) else end.day
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def year_fraction(start: date, end: date, convention: DayCountConvention) -> float:
    if end < start:
        raise ValueError(f"end ({end}) precedes start ({start})")
    if convention is DayCountConvention.ACT_365F:
        return _interval_days(start, end) / 365.0
    if convention is DayCountConvention.ACT_360:
        return _interval_days(start, end) / 360.0
    if convention is DayCountConvention.THIRTY_360:
        return _thirty_360_days(start, end) / 360.0
    raise ValueError(f"unsupported convention: {convention!r}")


@dataclass(frozen=True)
class YearFraction:

    value: float
    convention: DayCountConvention

    @classmethod
    def between(cls, start: date, end: date, convention: DayCountConvention) -> YearFraction:
        return cls(value=year_fraction(start, end, convention), convention=convention)
