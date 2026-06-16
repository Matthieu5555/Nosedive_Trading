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


DEFAULT_PROVIDER = "IBKR"
