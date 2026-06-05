"""Day-count conventions: convert a date interval to a year fraction.

The convention used to turn maturity dates into year fractions must be explicit and
stored with the value: a bare float is ambiguous (a 365 vs 360 basis gives different
numbers for the same dates). :class:`YearFraction` binds the convention to the number
so it never travels alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class DayCountConvention(StrEnum):
    """Supported conventions for converting a date interval to a year fraction."""

    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360 = "30/360"


def _interval_days(start: date, end: date) -> float:
    """Calendar days between two points, with full sub-day precision for datetimes."""
    return (end - start).total_seconds() / 86_400.0


def _thirty_360_days(start: date, end: date) -> float:
    """Day count under the 30/360 (US/NASD) convention."""
    d1 = min(start.day, 30)
    d2 = 30 if (d1 == 30 and end.day == 31) else end.day
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def year_fraction(start: date, end: date, convention: DayCountConvention) -> float:
    """Return the year fraction between ``start`` and ``end`` under ``convention``.

    Raises:
        ValueError: if ``end`` precedes ``start``.
    """
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
    """A year fraction bound to the convention that produced it."""

    value: float
    convention: DayCountConvention

    @classmethod
    def between(cls, start: date, end: date, convention: DayCountConvention) -> YearFraction:
        return cls(value=year_fraction(start, end, convention), convention=convention)
