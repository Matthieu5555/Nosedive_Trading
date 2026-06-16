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

EOD_JOB_NAME = "eod_capture"


class EodRunError(Exception):
    pass


@runtime_checkable
class SessionResolver(Protocol):

    def is_session(self, index: str, on_date: date) -> bool: ...

    def session_close(self, index: str, on_date: date) -> datetime: ...

    def next_session_open(self, index: str, on_date: date) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FiredIndex:

    entry: IndexEntry
    as_of: datetime
    next_open: datetime


@dataclass(frozen=True, slots=True)
class EodRunPlan:

    trade_date: date
    correlation_id: str
    fired: tuple[FiredIndex, ...]

    @property
    def is_noop(self) -> bool:
        return not self.fired


def _market_day(clock: Clock) -> date:
    return clock.now().date()


def _filter_scope(
    entries: Sequence[IndexEntry], *, calendar: str | None, index: str | None
) -> tuple[IndexEntry, ...]:
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
