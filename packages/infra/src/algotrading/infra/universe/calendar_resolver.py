"""Resolve an index's trading sessions and session close from its calendar code.

This is the thin port over the ``exchange_calendars`` library (ADR 0035 §2). The rest of
the platform depends on *our* signature — :meth:`CalendarResolver.is_session` and
:meth:`CalendarResolver.session_close` — not on ``exchange_calendars`` directly, so the
library is swappable and there is one place to test the wrapping. The two answers are
exactly what the capture pipeline needs: 1C captures the right close instant, 1G fires the
timer after that close, per exchange, with holidays / half-days / DST handled by the
library, never by hand.

Two disciplines are load-bearing here:

* **No wall clock — not even inside the library.** Every method takes the date as an
  argument, and the resolver never reads ``date.today()`` / ``datetime.now()`` itself. But
  ``exchange_calendars`` *does*: ``get_calendar(code)`` with no explicit bounds builds its
  session index out to roughly *wall-clock today + one year*, so the calendar's coverage
  window — and therefore which dates :meth:`is_session` accepts versus rejects as
  out-of-window — silently moves with the day the process runs. That is a wall-clock
  dependence at the calendar layer, and it makes the coverage check non-replayable. The
  resolver closes it by building every calendar bounded to an explicit **as-of date**
  (:meth:`CalendarResolver.__init__`'s ``as_of``): the window is then a pure function of the
  registry + the injected as-of, identical across processes and across days. 1C's
  byte-identical replay and 1G's idempotent ledger both break if the window depends on
  *when* the code ran, so the as-of is always injected by the caller (the same discipline
  the EOD-run tests pin); the resolver matches the :class:`Clock` abstraction the EOD runner
  threads by accepting either a plain ``date`` or that clock's current day.
* **Labeled failures, never a silent wrong answer.** An unknown index, a date outside the
  calendar's coverage window, or the close of a non-session date all raise a labeled
  :class:`CalendarResolutionError`, never a bare library traceback and never a defaulted
  instant.

The returned instant is a stdlib timezone-aware :class:`datetime.datetime` (UTC), converted
off the library's pandas ``Timestamp`` so the resolver's signature carries no pandas type.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Protocol, runtime_checkable

import exchange_calendars as xcals
from exchange_calendars.exchange_calendar import ExchangeCalendar

from .errors import CalendarResolutionError
from .index_registry import IndexRegistry

# How far back the deterministic calendar window starts, in years before the as-of date. The
# library otherwise picks an implicit ~20-year lookback that also drifts with the wall clock;
# pinning the start to a fixed span before the injected as-of makes the *whole* window — both
# ends — a pure function of the as-of, so the same as-of yields byte-identical session bounds
# on any day, in any process. A numerical invariant (a fixed span), not an economic tunable,
# so it stays a code constant per the config standard's invariant carve-out. 30 years comfortably
# covers any history the platform replays while keeping the index small.
_CALENDAR_LOOKBACK_YEARS = 30

# Bound on the number of distinct (code, as_of) calendars kept live. Each cached calendar is a
# full ~30-year session index, so an UNbounded cache (plain ``@cache``) would leak one calendar per
# distinct as_of forever — a long-running babysitter / multi-day backfill that resolves a new as_of
# each fire would accumulate them without limit. A small LRU bound caps the resident set: a single
# fire touches only a handful of (code, as_of) pairs and reuses them within the fire (LRU keeps the
# hot ones), while stale calendars from earlier as_ofs are evicted. Not an economic tunable — a
# pure memory-footprint bound — so it stays a code constant per the config standard's invariant
# carve-out.
_CALENDAR_CACHE_SIZE = 32

# Forward margin the next-session calendar is built with, so
# :meth:`CalendarResolver.next_session_open` can resolve the session AFTER the trade date — which
# lies past the as-of bound the backward queries (``is_session`` / ``session_close``) build to. It
# must exceed the longest run of consecutive non-session days across every calendar in the registry:
# a weekend plus the longest holiday cluster (year-end, Easter today; a Lunar-New-Year calendar, if
# one is ever added, needs ~9–10 days). A pure structural bound like ``_CALENDAR_LOOKBACK_YEARS`` —
# not an economic tunable — so it stays a code constant per the config standard's invariant
# carve-out. It never widens the look-ahead surface: ONLY ``next_session_open`` builds with this
# margin; ``is_session`` / ``session_close`` build with the default ``forward=0`` and keep rejecting
# any date past the as-of. ``test_next_session_within_margin`` pins it against each registry
# calendar's widest closure, so a calendar that needs a bigger margin fails the test loudly rather
# than silently mis-capturing in production.
_NEXT_SESSION_MARGIN = timedelta(days=16)


@runtime_checkable
class _DayClock(Protocol):
    """The one method the resolver needs off the EOD runner's :class:`Clock` — its ``now()``.

    Typed as a Protocol so the resolver depends on the *signature*, not on the concrete
    ``connectivity.Clock`` (which lives a layer away); any injected clock with a ``now()``
    returning a ``datetime`` supplies the as-of day.
    """

    def now(self) -> datetime: ...


@lru_cache(maxsize=_CALENDAR_CACHE_SIZE)
def _calendar(
    code: str, as_of: date, forward: timedelta = timedelta(0)
) -> ExchangeCalendar:
    """Return (and cache) the library calendar for a code, bounded to an explicit as-of date.

    Bounded — ``end = as_of + forward`` and ``start`` a fixed span before it — so the calendar's
    coverage window is a deterministic function of ``(code, as_of, forward)`` and never of
    wall-clock today (the library defaults ``end`` to *today + ~1y*, which silently moves the
    window day to day). ``forward`` defaults to zero — the backward queries (``is_session`` /
    ``session_close``) build with ``end = as_of`` exactly and so never see a future date — and is
    a small positive margin only for :meth:`CalendarResolver.next_session_open`, which must read
    one session past the as-of (see :data:`_NEXT_SESSION_MARGIN`). The same handful of keys are
    resolved repeatedly across one fire, so the bounded calendar is cached; the result is immutable
    for our read-only use, so sharing one instance is safe. Keyed by the as-of (and forward) too,
    so a backfill fire at a different as-of gets its own correctly-bounded calendar rather than a
    stale cached one.
    """
    start = as_of.replace(year=as_of.year - _CALENDAR_LOOKBACK_YEARS)
    return xcals.get_calendar(code, start=start, end=as_of + forward)


def _resolve_as_of(as_of: date | _DayClock | None) -> date | None:
    """Normalise the injected as-of into a plain date (or None for the escape hatch).

    Accepts a plain ``date`` verbatim, or a clock with ``now()`` (the EOD runner's
    :class:`Clock`) whose current day is taken — never a wall clock read here. ``None`` stays
    ``None`` (the deliberate no-as-of escape hatch). A ``datetime`` is narrowed to its date.
    """
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return as_of.now().date()


class CalendarResolver:
    """Answer ``is_session`` / ``session_close`` per registry index, off the calendar lib.

    Built over an :class:`IndexRegistry` and an explicit **as-of date**: it resolves an index
    symbol to its ``calendar`` code and asks the library for a calendar bounded to that as-of,
    so the session set and coverage window are deterministic and replayable (never a function
    of wall-clock today — see the module docstring). Because each index carries its own code,
    two indices resolve to genuinely different session sets and close instants (it is
    per-index, not one global calendar).

    ``as_of`` accepts either a plain :class:`datetime.date` or a clock with a ``now()`` (the
    EOD runner's injected :class:`Clock`), so the same instance the runner already threads
    supplies the as-of day — matching that abstraction rather than introducing a second one.
    It defaults to ``None`` only as a deliberate, labeled escape hatch for a caller that has no
    as-of (a one-off lookup), in which case the library's own implicit (wall-clock-dependent)
    window is used and a determinism guarantee is *not* made; the EOD path always injects one.
    """

    def __init__(
        self, registry: IndexRegistry, *, as_of: date | _DayClock | None = None
    ) -> None:
        self._registry = registry
        self._as_of = _resolve_as_of(as_of)

    def _entry_calendar(self, index: str) -> tuple[str, ExchangeCalendar]:
        # `registry.get` raises a labeled IndexRegistryError for an unknown index; the
        # calendar code is already validated at parse time, so `_calendar` will not miss.
        entry = self._registry.get(index)
        if self._as_of is None:
            return entry.calendar, xcals.get_calendar(entry.calendar)
        return entry.calendar, _calendar(entry.calendar, self._as_of)

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

    def next_session_open(self, index: str, on_date: date) -> datetime:
        """The timezone-aware UTC open of the session *after* ``on_date`` for ``index``.

        This is the upper bound of the close set the 1C capture admits: a snapshot row whose
        broker update stamp falls in ``[session_close(on_date), next_session_open(on_date))`` is
        still the close (post-close settlement marks included), while one stamped at or after this
        open belongs to a later session — a wrong-day catch-up snapshot — and is dropped. The
        half-open interval is why the bound is the next *open*, not the next *close*.

        Unlike :meth:`is_session` / :meth:`session_close`, this answer deliberately looks one
        session *past* the as-of, so it builds the calendar with :data:`_NEXT_SESSION_MARGIN`
        rather than the bare-as-of calendar the backward queries use. That widened calendar is
        never served to a backward query, so the look-ahead guard there is untouched; and an
        exchange's published open instant is schedule, not a quote, so returning it leaks no
        market data. ``on_date`` must be a trading session (a non-session raises, as for
        :meth:`session_close`); a closure longer than the margin — the next session falling
        outside the built window — raises a labeled :class:`CalendarResolutionError` rather than
        guessing, the loud signal that the margin must grow for a newly-added calendar.
        """
        code, _ = self._entry_calendar(index)
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
