"""Structured results of a batch reconstruction — what ran, what was skipped, why.

These are the typed report shapes the batch driver returns. They exist so a
reconstruction's outcome is data a caller can assert on, never prose in a log line.
Every per-day outcome is one of a small, named set (``RECONSTRUCTED``, ``MISSING``,
``EMPTY``); a missing raw partition is its own explicit status, so "no data for this
day" is a hard, inspectable fact and is never masked by a fabricated empty result.

All of these are frozen dataclasses so a report is a value: two equal reports are
equal by ``==`` and a report carries nothing mutable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from actor import ActorOutputs

# The three terminal outcomes for one trade date in a reconstruction range. A day is
# RECONSTRUCTED when its raw partition existed and the actor produced at least one
# derived record; MISSING when no raw partition is stored for it (the flagged, never
# masked case); EMPTY when raw data existed but the actor produced nothing (e.g. only
# gaps, no usable quotes) — a real, distinct outcome from MISSING, not interpolated.
RECONSTRUCTED = "reconstructed"
MISSING = "missing"
EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class DayReconstruction:
    """The outcome of reconstructing one trade date.

    ``status`` is one of :data:`RECONSTRUCTED`, :data:`MISSING`, :data:`EMPTY`.
    ``outputs`` is the actor's :class:`ActorOutputs` for a day that ran; for a
    :data:`MISSING` day it is ``None`` — there is deliberately no fabricated empty
    ``ActorOutputs`` for a missing partition, so a caller cannot mistake "absent" for
    "present but empty". ``version`` records the restatement version the outputs were
    written under (``None`` for the live, replace-in-place layout). ``record_count``
    is the total derived records produced, ``0`` for a missing or empty day.
    ``reason`` is a short, human-readable explanation of a skip.
    """

    trade_date: date
    status: str
    outputs: ActorOutputs | None = None
    version: str | None = None
    record_count: int = 0
    reason: str = ""

    @property
    def is_missing(self) -> bool:
        """True when no raw partition was stored for this day."""
        return self.status == MISSING

    @property
    def is_reconstructed(self) -> bool:
        """True when the day ran and produced at least one derived record."""
        return self.status == RECONSTRUCTED


@dataclass(frozen=True, slots=True)
class ReconstructionReport:
    """The structured result of reconstructing a date range, in date order.

    ``days`` holds one :class:`DayReconstruction` per trade date the driver
    considered, in ascending date order. The convenience views below let a caller
    assert on the named outcomes directly — "these dates were flagged missing", "these
    reconstructed" — rather than re-deriving them from the list.
    """

    start: date
    end: date
    version: str | None
    days: tuple[DayReconstruction, ...] = field(default_factory=tuple)

    @property
    def missing_dates(self) -> tuple[date, ...]:
        """The trade dates flagged as having no stored raw partition."""
        return tuple(day.trade_date for day in self.days if day.is_missing)

    @property
    def reconstructed_dates(self) -> tuple[date, ...]:
        """The trade dates that reconstructed to at least one derived record."""
        return tuple(day.trade_date for day in self.days if day.is_reconstructed)

    def day(self, trade_date: date) -> DayReconstruction:
        """The per-day outcome for ``trade_date``.

        Raises :class:`KeyError` if the date was not in the reconstructed range, so a
        caller asking about a date it never requested gets a loud error, not a
        silently fabricated "missing".
        """
        for day in self.days:
            if day.trade_date == trade_date:
                return day
        raise KeyError(trade_date)


@dataclass(frozen=True, slots=True)
class TableAgreement:
    """Whether a reconstruction's rows for one table match the live rows on disk.

    ``agrees`` is the headline: do replay and live carry the same records for this
    table on this date. ``replay_count``/``live_count`` are the row counts on each
    side; ``divergent_keys`` names the primary keys that differ (present on one side
    only, or present on both with differing field values) so a divergence points at
    the specific rows, never a bare "they differ".
    """

    table: str
    agrees: bool
    replay_count: int
    live_count: int
    divergent_keys: tuple[tuple[object, ...], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    """Per-table agreement between a day's reconstruction and its live outputs.

    ``agrees`` is True only when every compared table agrees. Under the same code
    version a reconstruction must agree with the previously-persisted live outputs on
    every table — that is the determinism guarantee — so this object exists to catch a
    future drift, by naming the first table and keys that diverge.
    """

    trade_date: date
    tables: tuple[TableAgreement, ...]

    @property
    def agrees(self) -> bool:
        """True when every compared table agrees."""
        return all(table.agrees for table in self.tables)

    @property
    def divergent_tables(self) -> tuple[str, ...]:
        """The tables whose replay rows diverge from live."""
        return tuple(table.table for table in self.tables if not table.agrees)
