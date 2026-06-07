"""Resolve an index's trading sessions and session close from its calendar code.

This is the thin port over the ``exchange_calendars`` library (ADR 0035 §2). The rest of
the platform depends on *our* signature — :meth:`CalendarResolver.is_session` and
:meth:`CalendarResolver.session_close` — not on ``exchange_calendars`` directly, so the
library is swappable and there is one place to test the wrapping. The two answers are
exactly what the capture pipeline needs: 1C captures the right close instant, 1G fires the
timer after that close, per exchange, with holidays / half-days / DST handled by the
library, never by hand.

Two disciplines are load-bearing here:

* **No wall clock.** Every method takes the date as an argument. The resolver never reads
  ``date.today()`` / ``datetime.now()``. 1C's byte-identical replay and 1G's idempotent
  ledger both break if the close instant depends on *when* the code ran, so "today" is
  always injected by the caller (the same discipline the EOD-run tests pin).
* **Labeled failures, never a silent wrong answer.** An unknown index, a date outside the
  calendar's coverage window, or the close of a non-session date all raise a labeled
  :class:`CalendarResolutionError`, never a bare library traceback and never a defaulted
  instant.

The returned instant is a stdlib timezone-aware :class:`datetime.datetime` (UTC), converted
off the library's pandas ``Timestamp`` so the resolver's signature carries no pandas type.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import cache

import exchange_calendars as xcals
from exchange_calendars.exchange_calendar import ExchangeCalendar

from .errors import CalendarResolutionError
from .index_registry import IndexRegistry


@cache
def _calendar(code: str) -> ExchangeCalendar:
    """Return (and cache) the library calendar for a code.

    Cached because constructing an ``exchange_calendars`` calendar is non-trivial and the
    same handful of codes are resolved repeatedly across a run. The result is immutable
    for our read-only use, so sharing one instance is safe.
    """
    return xcals.get_calendar(code)


class CalendarResolver:
    """Answer ``is_session`` / ``session_close`` per registry index, off the calendar lib.

    Built over an :class:`IndexRegistry`: it resolves an index symbol to its ``calendar``
    code and asks the library. Because each index carries its own code, two indices resolve
    to genuinely different session sets and close instants (it is per-index, not one global
    calendar).
    """

    def __init__(self, registry: IndexRegistry) -> None:
        self._registry = registry

    def _entry_calendar(self, index: str) -> tuple[str, ExchangeCalendar]:
        # `registry.get` raises a labeled IndexRegistryError for an unknown index; the
        # calendar code is already validated at parse time, so `_calendar` will not miss.
        entry = self._registry.get(index)
        return entry.calendar, _calendar(entry.calendar)

    def _check_coverage(self, index: str, code: str, cal: ExchangeCalendar, on: date) -> None:
        first = cal.first_session.date()
        last = cal.last_session.date()
        if on < first or on > last:
            raise CalendarResolutionError(
                index,
                code,
                on,
                f"date outside calendar coverage window [{first.isoformat()}, "
                f"{last.isoformat()}]",
            )

    def is_session(self, index: str, on_date: date) -> bool:
        """Whether ``on_date`` is a trading session for ``index`` (injected date, no clock).

        ``True`` for a normal trading day, ``False`` for a weekend or exchange holiday.
        A date outside the calendar's coverage window raises a labeled
        :class:`CalendarResolutionError` rather than answering ``False`` for a date the
        library simply cannot speak to.
        """
        code, cal = self._entry_calendar(index)
        self._check_coverage(index, code, cal, on_date)
        return bool(cal.is_session(on_date))

    def session_close(self, index: str, on_date: date) -> datetime:
        """The timezone-aware UTC close instant for ``index`` on session ``on_date``.

        This is the exact look-ahead-sensitive value 1C captures and 1G fires after. The
        library owns the timezone, DST, holiday, and half-day (early-close) handling, so a
        shortened session resolves to its early close, not the regular one. ``on_date`` must
        be a trading session: the close of a non-session date (a holiday/weekend) raises a
        labeled :class:`CalendarResolutionError`, never a guessed instant.
        """
        code, cal = self._entry_calendar(index)
        self._check_coverage(index, code, cal, on_date)
        if not cal.is_session(on_date):
            raise CalendarResolutionError(
                index, code, on_date, "not a trading session (holiday/weekend)"
            )
        try:
            close = cal.session_close(on_date)
        except xcals.errors.CalendarError as exc:  # pragma: no cover - guarded above
            raise CalendarResolutionError(
                index, code, on_date, f"library could not resolve close: {exc}"
            ) from exc
        # The library returns a UTC-tz pandas Timestamp; hand back a stdlib aware datetime so
        # our signature carries no pandas type. Normalise to UTC explicitly.
        return close.to_pydatetime().astimezone(UTC)
