from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from algotrading.infra.actor import ActorOutputs

RECONSTRUCTED = "reconstructed"
MISSING = "missing"
EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class DayReconstruction:

    trade_date: date
    status: str
    outputs: ActorOutputs | None = None
    version: str | None = None
    record_count: int = 0
    reason: str = ""

    @property
    def is_missing(self) -> bool:
        return self.status == MISSING

    @property
    def is_reconstructed(self) -> bool:
        return self.status == RECONSTRUCTED


@dataclass(frozen=True, slots=True)
class ReconstructionReport:

    start: date
    end: date
    version: str | None
    days: tuple[DayReconstruction, ...] = field(default_factory=tuple)

    @property
    def missing_dates(self) -> tuple[date, ...]:
        return tuple(day.trade_date for day in self.days if day.is_missing)

    @property
    def reconstructed_dates(self) -> tuple[date, ...]:
        return tuple(day.trade_date for day in self.days if day.is_reconstructed)

    def day(self, trade_date: date) -> DayReconstruction:
        for day in self.days:
            if day.trade_date == trade_date:
                return day
        raise KeyError(trade_date)


@dataclass(frozen=True, slots=True)
class TableAgreement:

    table: str
    agrees: bool
    replay_count: int
    live_count: int
    divergent_keys: tuple[tuple[object, ...], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReplayComparison:

    trade_date: date
    tables: tuple[TableAgreement, ...]

    @property
    def agrees(self) -> bool:
        return all(table.agrees for table in self.tables)

    @property
    def divergent_tables(self) -> tuple[str, ...]:
        return tuple(table.table for table in self.tables if not table.agrees)
