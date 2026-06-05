"""Positions and the source of record that supplies them.

The working in-memory book model risk consumes. A position is the signed quantity of one
instrument, identified by its canonical contract key so it joins cleanly to the analytics
produced upstream — it carries no pricing or economic detail of its own. Quantities are
``Decimal`` (exact contract counts, no float drift when summed); they convert to float only
where they multiply per-contract Greeks.

A :class:`PositionSet` bundles the latest positions with the source identity and the
timestamp that stamps any risk snapshot built from them, so a risk report is reproducible
from a named, dated book (the blueprint's "Version the risk snapshot with analytics version
and position source timestamp").

This is the working model; the persisted/seam shape is
:class:`algotrading.infra.contracts.Position`. ``tags`` carry desk-defined grouping keys for
the ``desk`` aggregation dimension; ``broker_contract_id`` is a foreign key used only for
broker reconciliation, never part of identity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

_HYPOTHETICAL = "hypothetical"


@dataclass(frozen=True)
class Position:
    """A signed holding of one instrument.

    ``quantity`` is signed: positive long, negative short. ``tags`` carry desk-defined
    grouping keys (e.g. ``{"desk": "vol-arb"}``) for aggregation. ``broker_contract_id`` is a
    foreign key for reconciliation only — never part of identity.
    """

    contract_key: str
    quantity: Decimal
    tags: Mapping[str, str] = field(default_factory=dict, compare=False)
    broker_contract_id: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.contract_key.strip():
            raise ValueError("contract_key must be non-empty")
        if not self.quantity.is_finite():
            raise ValueError(f"quantity must be finite, got {self.quantity}")
        if self.quantity == 0:
            raise ValueError("quantity must be non-zero")


@dataclass(frozen=True)
class PositionSet:
    """The latest positions from a source of record.

    ``source`` names the origin (e.g. ``hypothetical``, or a broker account) and ``source_ts``
    is the as-of time of the book; together they version any risk snapshot derived from it.
    An empty book is valid (a flat portfolio).
    """

    positions: tuple[Position, ...]
    source: str
    source_ts: datetime

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must be non-empty")


def hypothetical_positions(
    positions: Iterable[Position],
    *,
    source_ts: datetime,
    source: str = _HYPOTHETICAL,
) -> PositionSet:
    """Wrap hand-built positions as a hypothetical book (paper mode, no broker account).

    Order is preserved. This is the seam a live broker-positions source will later mirror.
    """
    return PositionSet(positions=tuple(positions), source=source, source_ts=source_ts)
