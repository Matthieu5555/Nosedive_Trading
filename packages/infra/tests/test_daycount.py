"""Day-count conventions. Expected year fractions are hand-derived from the convention's
own definition (actual days / basis, or the 30/360 day rule), independent of the code."""

from datetime import date, datetime

import pytest
from algotrading.infra.utils.daycount import DayCountConvention, YearFraction, year_fraction


def test_act_365f_full_year_is_one():
    # 2026-01-01 -> 2027-01-01 is 365 actual days; ACT/365F divides by 365.
    result = year_fraction(date(2026, 1, 1), date(2027, 1, 1), DayCountConvention.ACT_365F)
    assert result == pytest.approx(365 / 365.0)


def test_act_365f_leap_year_exceeds_one():
    # 2024 is a leap year: 2024-01-01 -> 2025-01-01 is 366 actual days over a 365 basis.
    result = year_fraction(date(2024, 1, 1), date(2025, 1, 1), DayCountConvention.ACT_365F)
    assert result == pytest.approx(366 / 365.0)


def test_act_360_half_year():
    # 2026-01-01 -> 2026-06-30: Jan(30 remaining after the 1st)=30? count actual days directly.
    # Actual days = (date(2026,6,30) - date(2026,1,1)).days = 180. ACT/360 divides by 360.
    result = year_fraction(date(2026, 1, 1), date(2026, 6, 30), DayCountConvention.ACT_360)
    assert result == pytest.approx(180 / 360.0)


def test_thirty_360_full_year_is_one():
    # 30/360: 360*(2027-2026) + 30*(1-1) + (15-15) = 360 days over a 360 basis = 1.0.
    result = year_fraction(date(2026, 1, 15), date(2027, 1, 15), DayCountConvention.THIRTY_360)
    assert result == pytest.approx(1.0)


def test_thirty_360_end_day_31_rule():
    # 30/360 US/NASD: with d1 = 30 (capped) and end day 31, d2 collapses to 30.
    # 2026-01-30 -> 2026-02-28: 360*0 + 30*(2-1) + (28-30) = 30 - 2 = 28 days.
    result = year_fraction(date(2026, 1, 30), date(2026, 2, 28), DayCountConvention.THIRTY_360)
    assert result == pytest.approx(28 / 360.0)
    # And the 31st-of-month collapse: 2026-01-30 -> 2026-03-31 => d2 = 30, not 31.
    # 360*0 + 30*(3-1) + (30-30) = 60 days.
    collapsed = year_fraction(date(2026, 1, 30), date(2026, 3, 31), DayCountConvention.THIRTY_360)
    assert collapsed == pytest.approx(60 / 360.0)


def test_zero_interval_is_zero():
    same = date(2026, 1, 1)
    assert year_fraction(same, same, DayCountConvention.ACT_365F) == pytest.approx(0.0)


def test_end_before_start_raises():
    with pytest.raises(ValueError, match="precedes start"):
        year_fraction(date(2027, 1, 1), date(2026, 1, 1), DayCountConvention.ACT_365F)


def test_datetime_subday_precision():
    # A 12-hour interval is half a day; ACT/365F divides by 365.
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = datetime(2026, 1, 1, 12, 0, 0)
    result = year_fraction(start, end, DayCountConvention.ACT_365F)
    assert result == pytest.approx(0.5 / 365.0)


def test_year_fraction_carries_convention():
    yf = YearFraction.between(date(2026, 1, 1), date(2027, 1, 1), DayCountConvention.ACT_365F)
    assert yf.convention is DayCountConvention.ACT_365F
    assert yf.value == pytest.approx(1.0)
