from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Protocol, runtime_checkable

import exchange_calendars as xcals
from exchange_calendars.exchange_calendar import ExchangeCalendar

from .errors import CalendarResolutionError
from .index_registry import IndexEntry, IndexRegistry

_CALENDAR_LOOKBACK_YEARS = 30

_CALENDAR_CACHE_SIZE = 32

_NEXT_SESSION_MARGIN = timedelta(days=16)


@runtime_checkable
class _DayClock(Protocol):

    def now(self) -> datetime: ...


@lru_cache(maxsize=_CALENDAR_CACHE_SIZE)
def _calendar(code: str, as_of: date, forward: timedelta = timedelta(0)) -> ExchangeCalendar:
    start = as_of.replace(year=as_of.year - _CALENDAR_LOOKBACK_YEARS)
    return xcals.get_calendar(code, start=start, end=as_of + forward)


def _resolve_as_of(as_of: date | _DayClock | None) -> date | None:
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return as_of.now().date()


class CalendarResolver:

    def __init__(self, registry: IndexRegistry, *, as_of: date | _DayClock | None = None) -> None:
        self._registry = registry
        self._as_of = _resolve_as_of(as_of)

    def _entry_calendar(self, index: str) -> tuple[IndexEntry, ExchangeCalendar]:
        entry = self._registry.get(index)
        if self._as_of is None:
            return entry, xcals.get_calendar(entry.calendar)
        return entry, _calendar(entry.calendar, self._as_of)

    def _check_coverage(self, index: str, code: str, cal: ExchangeCalendar, on: date) -> None:
        first = cal.first_session.date()
        last = cal.last_session.date()
        if on < first or on > last:
            raise CalendarResolutionError(
                index,
                code,
                on,
                f"date outside calendar coverage window [{first.isoformat()}, {last.isoformat()}]",
            )

    def is_session(self, index: str, on_date: date) -> bool:
        entry, cal = self._entry_calendar(index)
        self._check_coverage(index, entry.calendar, cal, on_date)
        return bool(cal.is_session(on_date))

    def session_close(self, index: str, on_date: date) -> datetime:
        entry, cal = self._entry_calendar(index)
        self._check_coverage(index, entry.calendar, cal, on_date)
        if not cal.is_session(on_date):
            raise CalendarResolutionError(
                index, entry.calendar, on_date, "not a trading session (holiday/weekend)"
            )
        try:
            close = cal.session_close(on_date)
        except xcals.errors.CalendarError as exc:  # pragma: no cover - guarded above
            raise CalendarResolutionError(
                index, entry.calendar, on_date, f"library could not resolve close: {exc}"
            ) from exc
        if entry.option_settlement_close is None:
            return close.to_pydatetime().astimezone(UTC)
        local_close = close.tz_convert(cal.tz).to_pydatetime()
        settled_local = local_close.replace(
            hour=entry.option_settlement_close.hour,
            minute=entry.option_settlement_close.minute,
            second=0,
            microsecond=0,
        )
        return settled_local.astimezone(UTC)

    def next_session_open(self, index: str, on_date: date) -> datetime:
        entry, _ = self._entry_calendar(index)
        code = entry.calendar
        cal = (
            xcals.get_calendar(code)
            if self._as_of is None
            else _calendar(code, self._as_of, _NEXT_SESSION_MARGIN)
        )
        self._check_coverage(index, code, cal, on_date)
        if not cal.is_session(on_date):
            raise CalendarResolutionError(
                index, code, on_date, "not a trading session (holiday/weekend)"
            )
        try:
            nxt = cal.next_session(on_date)
            open_instant = cal.session_open(nxt)
        except xcals.errors.CalendarError as exc:
            raise CalendarResolutionError(
                index,
                code,
                on_date,
                f"next session falls beyond the {_NEXT_SESSION_MARGIN.days}-day margin: {exc}",
            ) from exc
        return open_instant.to_pydatetime().astimezone(UTC)
