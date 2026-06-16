from __future__ import annotations

from datetime import UTC, date, datetime

import exchange_calendars as xcals
import pytest
from algotrading.infra.universe import (
    CalendarResolutionError,
    CalendarResolver,
    IndexRegistryError,
    parse_index_registry,
)
from algotrading.infra.universe.calendar_resolver import _NEXT_SESSION_MARGIN

_BLOCK = {
    "SX5E": {
        "name": "EURO STOXX 50",
        "calendar": "XEUR",
        "currency": "EUR",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
        "enabled": True,
    },
    "SPX": {
        "name": "S&P 500",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 2, "secType": "IND", "exchange": "CBOE"},
        "enabled": True,
    },
}


def _resolver() -> CalendarResolver:
    return CalendarResolver(parse_index_registry(_BLOCK))


def test_known_normal_trading_day_is_a_session_for_both() -> None:
    res = _resolver()
    assert res.is_session("SX5E", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 6, 8)) is True


def test_eurex_holiday_is_closed_while_nyse_is_open() -> None:
    res = _resolver()
    assert res.is_session("SX5E", date(2026, 5, 1)) is False
    assert res.is_session("SPX", date(2026, 5, 1)) is True


def test_nyse_holiday_is_closed_while_eurex_is_open() -> None:
    res = _resolver()
    assert res.is_session("SPX", date(2025, 11, 27)) is False
    assert res.is_session("SX5E", date(2025, 11, 27)) is True


def test_weekend_is_not_a_session() -> None:
    res = _resolver()
    assert res.is_session("SPX", date(2026, 6, 6)) is False
    assert res.is_session("SX5E", date(2026, 6, 7)) is False


def test_the_two_indices_resolve_to_different_session_sets() -> None:
    res = _resolver()
    eurex_holiday = date(2026, 5, 1)
    nyse_holiday = date(2025, 11, 27)
    assert res.is_session("SX5E", eurex_holiday) != res.is_session("SPX", eurex_holiday)
    assert res.is_session("SX5E", nyse_holiday) != res.is_session("SPX", nyse_holiday)


def test_session_close_is_tz_aware_utc() -> None:
    res = _resolver()
    close = res.session_close("SPX", date(2026, 6, 8))
    assert isinstance(close, datetime)
    assert close.tzinfo is not None
    assert close.utcoffset() == UTC.utcoffset(None)


def test_eurex_and_nyse_close_at_different_utc_instants() -> None:
    res = _resolver()
    d = date(2026, 3, 10)
    spx_close = res.session_close("SPX", d)
    sx5e_close = res.session_close("SX5E", d)
    assert spx_close == datetime(2026, 3, 10, 20, 0, tzinfo=UTC)
    assert sx5e_close == datetime(2026, 3, 10, 21, 0, tzinfo=UTC)
    assert spx_close != sx5e_close


def test_nyse_half_day_resolves_to_the_early_close() -> None:
    res = _resolver()
    early = res.session_close("SPX", date(2025, 11, 28))
    assert early == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)
    normal = res.session_close("SPX", date(2025, 11, 26))
    assert normal == datetime(2025, 11, 26, 21, 0, tzinfo=UTC)
    assert early != normal


def test_session_close_on_a_holiday_raises_labeled_error() -> None:
    res = _resolver()
    with pytest.raises(CalendarResolutionError) as exc:
        res.session_close("SX5E", date(2026, 5, 1))
    assert exc.value.index == "SX5E"
    assert "not a trading session" in exc.value.reason


def test_unknown_index_raises_labeled_error() -> None:
    res = _resolver()
    with pytest.raises(IndexRegistryError):
        res.is_session("NDX", date(2026, 6, 8))


def test_date_before_coverage_window_is_labeled_not_a_silent_wrong_answer() -> None:
    res = _resolver()
    with pytest.raises(CalendarResolutionError) as exc:
        res.is_session("SPX", date(1800, 1, 1))
    assert "coverage window" in exc.value.reason


def test_date_after_coverage_window_is_labeled() -> None:
    res = _resolver()
    with pytest.raises(CalendarResolutionError) as exc:
        res.session_close("SPX", date(2100, 1, 1))
    assert "coverage window" in exc.value.reason


def test_resolver_reads_no_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    import algotrading.infra.universe.calendar_resolver as mod

    class _PoisonDateTime:
        @staticmethod
        def now(*_a: object, **_k: object) -> datetime:  # pragma: no cover - must not run
            raise AssertionError("resolver read a wall clock via datetime.now()")

    class _PoisonDate:
        @staticmethod
        def today() -> date:  # pragma: no cover - must not run
            raise AssertionError("resolver read a wall clock via date.today()")

    monkeypatch.setattr(mod, "datetime", _PoisonDateTime)
    monkeypatch.setattr(mod, "date", _PoisonDate)

    res = _resolver()
    assert res.is_session("SPX", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 5, 25)) is False


def test_two_injected_dates_give_each_dates_answer() -> None:
    res = _resolver()
    assert res.is_session("SPX", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 7, 4)) is False


def test_next_session_open_is_the_following_session_open() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 10))
    assert res.next_session_open("SPX", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 13, 30, tzinfo=UTC
    )
    assert res.next_session_open("SX5E", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 6, 0, tzinfo=UTC
    )


def test_next_session_open_skips_a_weekend() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 12))
    assert res.next_session_open("SPX", date(2026, 6, 12)) == datetime(
        2026, 6, 15, 13, 30, tzinfo=UTC
    )


def test_next_session_open_skips_a_holiday() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 18))
    assert res.next_session_open("SPX", date(2026, 6, 18)) == datetime(
        2026, 6, 22, 13, 30, tzinfo=UTC
    )


def test_next_session_open_does_not_widen_backward_coverage() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 10))
    assert res.next_session_open("SPX", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 13, 30, tzinfo=UTC
    )
    with pytest.raises(CalendarResolutionError):
        res.session_close("SPX", date(2026, 6, 11))
    with pytest.raises(CalendarResolutionError):
        res.is_session("SPX", date(2026, 6, 11))


_BLOCK_OESX = {
    "SX5E": {
        "name": "EURO STOXX 50",
        "calendar": "XEUR",
        "option_settlement_close": "17:30",
        "currency": "EUR",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
        "enabled": True,
    },
    "SPX": {
        "name": "S&P 500",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 2, "secType": "IND", "exchange": "CBOE"},
        "enabled": True,
    },
}


def test_option_settlement_close_override_is_dst_correct() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    assert res.session_close("SX5E", date(2026, 6, 12)) == datetime(2026, 6, 12, 15, 30, tzinfo=UTC)
    assert res.session_close("SX5E", date(2026, 1, 15)) == datetime(2026, 1, 15, 16, 30, tzinfo=UTC)


def test_override_pulls_the_close_earlier_than_the_calendar_futures_close() -> None:
    d = date(2026, 6, 12)
    with_override = CalendarResolver(parse_index_registry(_BLOCK_OESX)).session_close("SX5E", d)
    without = CalendarResolver(parse_index_registry(_BLOCK)).session_close("SX5E", d)
    assert with_override == datetime(2026, 6, 12, 15, 30, tzinfo=UTC)
    assert without == datetime(2026, 6, 12, 20, 0, tzinfo=UTC)
    assert with_override < without


def test_no_override_leaves_the_library_close_verbatim() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    assert res.session_close("SPX", date(2026, 6, 8)) == datetime(2026, 6, 8, 20, 0, tzinfo=UTC)


def test_override_still_raises_on_a_non_session() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    with pytest.raises(CalendarResolutionError) as exc:
        res.session_close("SX5E", date(2026, 5, 1))
    assert "not a trading session" in exc.value.reason


def test_override_close_stays_inside_the_half_open_close_set() -> None:
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX), as_of=date(2026, 6, 12))
    close = res.session_close("SX5E", date(2026, 6, 11))
    next_open = res.next_session_open("SX5E", date(2026, 6, 11))
    assert close == datetime(2026, 6, 11, 15, 30, tzinfo=UTC)
    assert close < next_open


def test_malformed_settlement_close_is_a_labeled_parse_error() -> None:
    bad = {
        "SX5E": {
            "name": "EURO STOXX 50",
            "calendar": "XEUR",
            "option_settlement_close": "25:99",
            "currency": "EUR",
            "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
            "enabled": True,
        }
    }
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(bad)
    assert exc.value.field == "option_settlement_close"


def test_next_session_within_margin() -> None:
    codes = {spec["calendar"] for spec in _BLOCK.values()}
    for code in codes:
        cal = xcals.get_calendar(code, start="2015-01-01", end="2026-12-31")
        sessions = [ts.date() for ts in cal.sessions]
        widest_gap = max((b - a).days for a, b in zip(sessions, sessions[1:], strict=False))
        assert widest_gap <= _NEXT_SESSION_MARGIN.days, (
            f"{code} has a {widest_gap}-day closure exceeding the "
            f"{_NEXT_SESSION_MARGIN.days}-day margin — bump _NEXT_SESSION_MARGIN"
        )
