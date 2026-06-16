from datetime import date, datetime

import pytest
from algotrading.infra.utils.daycount import DayCountConvention, YearFraction, year_fraction


def test_act_365f_full_year_is_one():
    result = year_fraction(date(2026, 1, 1), date(2027, 1, 1), DayCountConvention.ACT_365F)
    assert result == pytest.approx(365 / 365.0)


def test_act_365f_leap_year_exceeds_one():
    result = year_fraction(date(2024, 1, 1), date(2025, 1, 1), DayCountConvention.ACT_365F)
    assert result == pytest.approx(366 / 365.0)


def test_act_360_half_year():
    result = year_fraction(date(2026, 1, 1), date(2026, 6, 30), DayCountConvention.ACT_360)
    assert result == pytest.approx(180 / 360.0)


def test_thirty_360_full_year_is_one():
    result = year_fraction(date(2026, 1, 15), date(2027, 1, 15), DayCountConvention.THIRTY_360)
    assert result == pytest.approx(1.0)


def test_thirty_360_end_day_31_rule():
    result = year_fraction(date(2026, 1, 30), date(2026, 2, 28), DayCountConvention.THIRTY_360)
    assert result == pytest.approx(28 / 360.0)
    collapsed = year_fraction(date(2026, 1, 30), date(2026, 3, 31), DayCountConvention.THIRTY_360)
    assert collapsed == pytest.approx(60 / 360.0)


def test_zero_interval_is_zero():
    same = date(2026, 1, 1)
    assert year_fraction(same, same, DayCountConvention.ACT_365F) == pytest.approx(0.0)


def test_end_before_start_raises():
    with pytest.raises(ValueError, match="precedes start"):
        year_fraction(date(2027, 1, 1), date(2026, 1, 1), DayCountConvention.ACT_365F)


def test_datetime_subday_precision():
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = datetime(2026, 1, 1, 12, 0, 0)
    result = year_fraction(start, end, DayCountConvention.ACT_365F)
    assert result == pytest.approx(0.5 / 365.0)


def test_year_fraction_carries_convention():
    yf = YearFraction.between(date(2026, 1, 1), date(2027, 1, 1), DayCountConvention.ACT_365F)
    assert yf.convention is DayCountConvention.ACT_365F
    assert yf.value == pytest.approx(1.0)
