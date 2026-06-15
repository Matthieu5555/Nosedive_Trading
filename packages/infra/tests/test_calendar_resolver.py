"""1J — the calendar resolver: session vs holiday, tz-correct close, half-day, no clock.

The resolver answers, per index and injected date, "is this a session, and when does it
close?" off the ``exchange_calendars`` library (ADR 0035 §2). These tests pin:

* **session vs holiday** for *hand-picked* known holidays — different dates for Eurex vs
  NYSE — proving the two indices resolve to *different* session sets (per-index, not one
  global calendar);
* **session close is timezone-correct** — SX5E (Eurex) and SPX (NYSE) close at *different
  UTC instants* on the same date, asserted against hand-computed UTC values;
* **a half-day early close** resolves to the early close, not the regular one;
* **labeled failures** — non-session close, unknown index, out-of-coverage date — never a
  silent wrong answer;
* **no wall clock** — the resolve path takes an injected date and reads no ``now()``.

Independent oracle: every calendar fact below (holiday dates, close instants, the half-day)
is hand-encoded from the published exchange calendars and named in the comment — NEVER read
back from the resolver under test.
"""

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

# A two-index registry: SX5E on Eurex (XEUR), SPX on NYSE (XNYS). Both enabled here so the
# resolver is exercised for each; the on-disk seed keeps them disabled for capture safety.
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


# --- session vs holiday: hand-picked, different dates per exchange ----------------------
def test_known_normal_trading_day_is_a_session_for_both() -> None:
    # 2026-06-08 is an ordinary Monday — a session on both Eurex and NYSE.
    res = _resolver()
    assert res.is_session("SX5E", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 6, 8)) is True


def test_eurex_holiday_is_closed_while_nyse_is_open() -> None:
    # 2026-05-01 is Labour Day — Eurex (XEUR) is closed; NYSE (XNYS) trades normally.
    res = _resolver()
    assert res.is_session("SX5E", date(2026, 5, 1)) is False
    assert res.is_session("SPX", date(2026, 5, 1)) is True


def test_nyse_holiday_is_closed_while_eurex_is_open() -> None:
    # 2025-12-25 is Christmas — both closed; pick US Thanksgiving 2025-11-27 instead, when
    # NYSE is closed but Eurex trades. Independent fact: US Thanksgiving = 4th Thu of Nov.
    res = _resolver()
    assert res.is_session("SPX", date(2025, 11, 27)) is False
    assert res.is_session("SX5E", date(2025, 11, 27)) is True


def test_weekend_is_not_a_session() -> None:
    res = _resolver()
    # 2026-06-06 is a Saturday.
    assert res.is_session("SPX", date(2026, 6, 6)) is False
    assert res.is_session("SX5E", date(2026, 6, 7)) is False  # Sunday


def test_the_two_indices_resolve_to_different_session_sets() -> None:
    # Proof it is per-index, not one global calendar: at least one date differs.
    res = _resolver()
    eurex_holiday = date(2026, 5, 1)  # Eurex closed, NYSE open
    nyse_holiday = date(2025, 11, 27)  # NYSE closed, Eurex open
    assert res.is_session("SX5E", eurex_holiday) != res.is_session("SPX", eurex_holiday)
    assert res.is_session("SX5E", nyse_holiday) != res.is_session("SPX", nyse_holiday)


# --- session close is timezone-correct, different UTC per exchange ----------------------
def test_session_close_is_tz_aware_utc() -> None:
    res = _resolver()
    close = res.session_close("SPX", date(2026, 6, 8))
    assert isinstance(close, datetime)
    assert close.tzinfo is not None
    assert close.utcoffset() == UTC.utcoffset(None)


def test_eurex_and_nyse_close_at_different_utc_instants() -> None:
    # On 2026-03-10 the US is already on DST (2nd Sun of March = Mar 8) but the EU is not
    # (last Sun of March = Mar 29), so the two close instants differ by an hour in UTC.
    # Hand-computed independent oracle (from the published calendars):
    #   NYSE  16:00 ET  = 20:00 UTC (EDT, UTC-4)
    #   Eurex 22:00 CET = 21:00 UTC (CET, UTC+1; the XEUR session runs to 22:00 Berlin)
    res = _resolver()
    d = date(2026, 3, 10)
    spx_close = res.session_close("SPX", d)
    sx5e_close = res.session_close("SX5E", d)
    assert spx_close == datetime(2026, 3, 10, 20, 0, tzinfo=UTC)
    assert sx5e_close == datetime(2026, 3, 10, 21, 0, tzinfo=UTC)
    assert spx_close != sx5e_close


def test_nyse_half_day_resolves_to_the_early_close() -> None:
    # 2025-11-28 (day after Thanksgiving) is a NYSE half day: early close 13:00 ET = 18:00
    # UTC (EST, UTC-5), NOT the regular 16:00 ET / 21:00 UTC. Independent oracle: the NYSE
    # shortened-session schedule.
    res = _resolver()
    early = res.session_close("SPX", date(2025, 11, 28))
    assert early == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)
    # A normal full session the same week closes at 21:00 UTC — confirm the half day differs.
    normal = res.session_close("SPX", date(2025, 11, 26))  # ordinary Wednesday
    assert normal == datetime(2025, 11, 26, 21, 0, tzinfo=UTC)
    assert early != normal


# --- labeled failures, never a silent wrong answer -------------------------------------
def test_session_close_on_a_holiday_raises_labeled_error() -> None:
    res = _resolver()
    with pytest.raises(CalendarResolutionError) as exc:
        res.session_close("SX5E", date(2026, 5, 1))  # Eurex Labour Day
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


# --- no wall clock on the resolve path -------------------------------------------------
def test_resolver_reads_no_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same discipline as the EOD-run tests: the answer depends only on the injected date,
    # never on when the code ran. Poison datetime.now / date.today; if the resolver secretly
    # read either, it would raise. The injected-date answer must still be correct.
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
    # Both answers for an injected date resolve without touching the poisoned clock.
    assert res.is_session("SPX", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 5, 25)) is False  # Memorial Day 2026 (NYSE closed)


def test_two_injected_dates_give_each_dates_answer() -> None:
    # The resolver is a pure function of (index, injected date): two dates, two answers.
    res = _resolver()
    assert res.is_session("SPX", date(2026, 6, 8)) is True
    assert res.is_session("SPX", date(2026, 7, 4)) is False  # Independence Day (Sat) / closed


# --- next_session_open: the upper bound of the close set (the post-close-drop fix) ----------
def test_next_session_open_is_the_following_session_open() -> None:
    # 2026-06-10 (Wed) → next session 2026-06-11 (Thu). Independent oracle: NYSE opens 09:30 ET
    # = 13:30 UTC (EDT); Eurex's session opens 08:00 CEST = 06:00 UTC.
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 10))
    assert res.next_session_open("SPX", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 13, 30, tzinfo=UTC
    )
    assert res.next_session_open("SX5E", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 6, 0, tzinfo=UTC
    )


def test_next_session_open_skips_a_weekend() -> None:
    # Friday 2026-06-12 → the next NYSE session is Monday 2026-06-15 (open 13:30 UTC).
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 12))
    assert res.next_session_open("SPX", date(2026, 6, 12)) == datetime(
        2026, 6, 15, 13, 30, tzinfo=UTC
    )


def test_next_session_open_skips_a_holiday() -> None:
    # Thursday 2026-06-18 → Friday 2026-06-19 is Juneteenth (NYSE closed), so the next session is
    # Monday 2026-06-22 (open 13:30 UTC). The library skips the holiday, not this code.
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 18))
    assert res.next_session_open("SPX", date(2026, 6, 18)) == datetime(
        2026, 6, 22, 13, 30, tzinfo=UTC
    )


def test_next_session_open_does_not_widen_backward_coverage() -> None:
    # next_session_open looks one session past the as-of, but the forward margin must NOT leak
    # into the backward queries: session_close / is_session for a date after the as-of still raise
    # (the look-ahead guard the medallion replay relies on). Same resolver, same as-of.
    res = CalendarResolver(parse_index_registry(_BLOCK), as_of=date(2026, 6, 10))
    # The forward query resolves the next session fine...
    assert res.next_session_open("SPX", date(2026, 6, 10)) == datetime(
        2026, 6, 11, 13, 30, tzinfo=UTC
    )
    # ...but the backward queries still reject any date past the as-of.
    with pytest.raises(CalendarResolutionError):
        res.session_close("SPX", date(2026, 6, 11))
    with pytest.raises(CalendarResolutionError):
        res.is_session("SPX", date(2026, 6, 11))


# --- option_settlement_close override: OESX 17:30 CET, not the XEUR 22:00 futures close ----
# A registry where SX5E carries the OESX option-settlement-close override; SPX deliberately has
# none (its options share the index's 16:00 ET close), proving the override is per-index and
# opt-in. Independent oracle: OESX (Euro Stoxx 50 options) settle 17:30 Europe/Berlin, while the
# XEUR calendar close is the 22:00 Berlin futures close.
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
    # The override pins 17:30 Berlin; the UTC instant must track DST off the resolved session.
    # Independent oracle (hand-computed from the Europe/Berlin offset on each date):
    #   2026-06-12 is CEST (UTC+2): 17:30 CEST = 15:30 UTC
    #   2026-01-15 is CET  (UTC+1): 17:30 CET  = 16:30 UTC
    # Both are ordinary Eurex sessions (Fri / Thu).
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    assert res.session_close("SX5E", date(2026, 6, 12)) == datetime(2026, 6, 12, 15, 30, tzinfo=UTC)
    assert res.session_close("SX5E", date(2026, 1, 15)) == datetime(2026, 1, 15, 16, 30, tzinfo=UTC)


def test_override_pulls_the_close_earlier_than_the_calendar_futures_close() -> None:
    # The whole point: with the override the close is the 17:30 settlement, NOT the 22:00 futures
    # close the same calendar resolves without it. Same date, two registries, different instant.
    d = date(2026, 6, 12)
    with_override = CalendarResolver(parse_index_registry(_BLOCK_OESX)).session_close("SX5E", d)
    without = CalendarResolver(parse_index_registry(_BLOCK)).session_close("SX5E", d)
    assert with_override == datetime(2026, 6, 12, 15, 30, tzinfo=UTC)  # 17:30 CEST
    assert without == datetime(2026, 6, 12, 20, 0, tzinfo=UTC)  # 22:00 CEST futures close
    assert with_override < without


def test_no_override_leaves_the_library_close_verbatim() -> None:
    # SPX carries no override in either block — its close is the library's, unchanged.
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    # 2026-06-08 NYSE close 16:00 EDT = 20:00 UTC.
    assert res.session_close("SPX", date(2026, 6, 8)) == datetime(2026, 6, 8, 20, 0, tzinfo=UTC)


def test_override_still_raises_on_a_non_session() -> None:
    # The override applies only AFTER the session check — a holiday close still raises labeled.
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX))
    with pytest.raises(CalendarResolutionError) as exc:
        res.session_close("SX5E", date(2026, 5, 1))  # Eurex Labour Day
    assert "not a trading session" in exc.value.reason


def test_override_close_stays_inside_the_half_open_close_set() -> None:
    # The 1C close set is [session_close, next_session_open); pulling the close EARLIER (17:30 vs
    # 22:00) only tightens it — the invariant close < next_open must still hold.
    res = CalendarResolver(parse_index_registry(_BLOCK_OESX), as_of=date(2026, 6, 12))
    close = res.session_close("SX5E", date(2026, 6, 11))  # 17:30 CEST = 15:30 UTC, Thu
    next_open = res.next_session_open("SX5E", date(2026, 6, 11))  # Fri open 06:00 UTC
    assert close == datetime(2026, 6, 11, 15, 30, tzinfo=UTC)
    assert close < next_open


def test_malformed_settlement_close_is_a_labeled_parse_error() -> None:
    # A bad time-of-day is rejected at parse, naming the field — never silently dropped to the
    # calendar close (that would be the wrong-instant look-ahead the validator guards).
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
    """Every registry calendar's widest closure fits inside ``_NEXT_SESSION_MARGIN``.

    The margin is what ``next_session_open`` builds its forward calendar with; if any calendar
    ever has a gap between consecutive sessions wider than the margin, the forward resolution
    would fail in production. This pins the constant against each calendar's real multi-year
    schedule, so adding a longer-closing calendar (e.g. a Lunar-New-Year market) fails loudly
    here rather than silently mis-capturing in prod — exactly the bump-the-margin signal.
    """
    codes = {spec["calendar"] for spec in _BLOCK.values()}
    for code in codes:
        cal = xcals.get_calendar(code, start="2015-01-01", end="2026-12-31")
        sessions = [ts.date() for ts in cal.sessions]
        widest_gap = max((b - a).days for a, b in zip(sessions, sessions[1:], strict=False))
        assert widest_gap <= _NEXT_SESSION_MARGIN.days, (
            f"{code} has a {widest_gap}-day closure exceeding the "
            f"{_NEXT_SESSION_MARGIN.days}-day margin — bump _NEXT_SESSION_MARGIN"
        )
