"""EOD fire planning — the dependency-free leaf of the runner: resolve which day/indices.

This is the leaf the rest of the runner builds on: the labeled error, the calendar-resolver
seam, the per-index fired record, the resolved plan, and :func:`plan_fire` that produces the
plan from injected deps. It imports nothing from the other runner modules so it can be imported
by all of them without a cycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog
from algotrading.infra.connectivity import Clock
from algotrading.infra.universe import IndexEntry, enabled_indices

if TYPE_CHECKING:
    from .eod_dependencies import RunnerDeps

_LOGGER = structlog.get_logger("orchestration.eod_run")

# The job name the manifest/run-registry records this fire under.
EOD_JOB_NAME = "eod_capture"


class EodRunError(Exception):
    """A labeled runner error — a future trade date, an unknown calendar, a bad scope.

    Carries a plain-language reason so a misfire fails loudly with an operator-readable
    message rather than a bare traceback or a silent wrong-day capture.
    """


@runtime_checkable
class SessionResolver(Protocol):
    """The calendar answers the runner needs — the 1J :class:`CalendarResolver` seam.

    Typed as a Protocol (not the concrete class) so the runner depends on the *signature*,
    not on ``exchange_calendars``: a test injects a fake resolver with controlled
    holiday/close behaviour, and 1C/1G consume the same methods the real resolver exposes —
    ``is_session`` (fire/skip), ``session_close`` (the capture instant), and
    ``next_session_open`` (the upper bound of the close set, see :class:`FiredIndex`).
    """

    def is_session(self, index: str, on_date: date) -> bool: ...

    def session_close(self, index: str, on_date: date) -> datetime: ...

    def next_session_open(self, index: str, on_date: date) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FiredIndex:
    """One enabled index this fire captures, paired with its own session-close instant.

    ``as_of`` is :meth:`CalendarResolver.session_close` for the index on the trade date — its
    own timezone-correct close (Eurex close for SX5E, NYSE close for SPX), the exact
    look-ahead-sensitive instant 1C captures at and the value the stage wiring injects.

    ``next_open`` is :meth:`CalendarResolver.next_session_open` for the same index/date — the
    open of the following session. It is the upper bound of the close set: 1C keeps a snapshot
    row whose broker update stamp lands in ``[as_of, next_open)`` (post-close settlement marks)
    and drops one stamped at/after ``next_open`` (a later session, i.e. a wrong-day catch-up
    snapshot). Resolved here, upstream, alongside ``as_of`` so the live capture layer is handed
    a deterministic instant rather than recomputing it — the same determinism rail ``as_of`` rides.
    """

    entry: IndexEntry
    as_of: datetime
    next_open: datetime


@dataclass(frozen=True, slots=True)
class EodRunPlan:
    """What one fire resolved before running: the date, the trace id, and the fired set.

    ``fired`` is the enabled indices in the calendar group that are *in session* on the trade
    date, each with its close instant. An empty ``fired`` (every index a holiday, or an empty
    enabled set for the group) is a clean no-op — :attr:`is_noop` is then ``True`` and the
    pipeline is not run.
    """

    trade_date: date
    correlation_id: str
    fired: tuple[FiredIndex, ...]

    @property
    def is_noop(self) -> bool:
        """True when no index is in session for the fire — a clean no-op, not a failure."""
        return not self.fired


def _market_day(clock: Clock) -> date:
    """The clock's current calendar day in UTC — the default fire's trade date.

    Reads the injected clock, never a wall clock, so a deterministic caller (a test, a replay)
    pins the day. The resolver then decides per index whether that day is a session.
    """
    return clock.now().date()


def _filter_scope(
    entries: Sequence[IndexEntry], *, calendar: str | None, index: str | None
) -> tuple[IndexEntry, ...]:
    """Filter the enabled entries to the fired calendar group / single index.

    ``calendar`` scopes to one exchange-calendar code (the templated timer's group — every
    enabled index on that calendar); ``index`` scopes to a single symbol. Both ``None`` = the
    whole enabled set. A ``--calendar``/``--index`` that matches nothing yields an empty set
    (a clean no-op for that fire), not an error — an exchange with no enabled index yet is a
    legitimate, harmless fire.
    """
    selected = entries
    if calendar is not None:
        selected = tuple(e for e in selected if e.calendar == calendar)
    if index is not None:
        selected = tuple(e for e in selected if e.symbol == index)
    return tuple(selected)


def plan_fire(
    deps: RunnerDeps,
    *,
    trade_date: date | None,
    calendar: str | None,
    index: str | None,
    correlation_id: str | None = None,
) -> EodRunPlan:
    """Resolve the trade date, the trace id, and the in-session fired index set for one fire.

    The trade date defaults to the clock's current market day; an explicit ``trade_date`` in
    the *future* (after the clock's market day) is rejected with a labeled :class:`EodRunError`
    — never capture a session that has not closed. The enabled indices are read from the
    registry, filtered to the calendar group / single index, and reduced to those in session on
    the date (per the 1J resolver). Each surviving index is paired with its own
    ``session_close`` instant. A bound ``correlation_id`` (a fresh UUID unless one is supplied)
    is returned for the whole fire.
    """
    today = _market_day(deps.clock)
    resolved_date = trade_date if trade_date is not None else today
    if resolved_date > today:
        raise EodRunError(
            f"trade-date {resolved_date.isoformat()} is in the future "
            f"(clock day {today.isoformat()}); a session that has not closed is never captured"
        )

    corr = correlation_id or uuid.uuid4().hex
    scoped = _filter_scope(
        enabled_indices(deps.registry), calendar=calendar, index=index
    )
    fired: list[FiredIndex] = []
    for entry in scoped:
        if not deps.resolver.is_session(entry.symbol, resolved_date):
            _LOGGER.info(
                "orchestration.eod_run.skip_non_session",
                correlation_id=corr,
                index=entry.symbol,
                calendar=entry.calendar,
                trade_date=resolved_date.isoformat(),
            )
            continue
        fired.append(
            FiredIndex(
                entry=entry,
                as_of=deps.resolver.session_close(entry.symbol, resolved_date),
                next_open=deps.resolver.next_session_open(entry.symbol, resolved_date),
            )
        )
    return EodRunPlan(
        trade_date=resolved_date,
        correlation_id=corr,
        fired=tuple(fired),
    )
