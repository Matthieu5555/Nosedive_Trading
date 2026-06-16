from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

_HYPOTHETICAL = "hypothetical"


@dataclass(frozen=True)
class Position:

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
    return PositionSet(positions=tuple(positions), source=source, source_ts=source_ts)
