from __future__ import annotations

from dataclasses import dataclass

from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)


@dataclass(frozen=True, slots=True)
class IndexBasket:

    instruments: tuple[InstrumentKey, ...]
    events: tuple[RawMarketEvent, ...]
    masters: tuple[InstrumentMaster, ...]
    positions: tuple[Position, ...] = ()
    # Count of option instruments carrying a genuine two-sided quote in this capture, as
    # measured by the collector (None when the source does not report it). The overwrite-
    # protection gate (T-restore-overwrite-last-wins C1.2) reads this: a capture with ZERO
    # valid two-sided quotes must never overwrite a slice already banked for the day. The
    # boundary is zero — a thin-but-real basket (count > 0) is admitted and flagged, never
    # dropped (flag-not-reject; the front clamps degenerate ultra-short slices).
    two_sided_count: int | None = None


DEFAULT_PROVIDER = "IBKR"
