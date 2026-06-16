from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from algotrading.infra.contracts import InstrumentKey

from .synthetic import SyntheticSurface


@dataclass(frozen=True, slots=True)
class OptionQuoteFixture:

    instrument: InstrumentKey
    bid: float | None
    ask: float | None
    last: float | None
    quote_ts: datetime


@dataclass(frozen=True, slots=True)
class ChainFixture:

    name: str
    description: str
    as_of: datetime
    underlying: InstrumentKey
    underlying_spot: float
    quotes: tuple[OptionQuoteFixture, ...]
    known_answers: SyntheticSurface | None = None
