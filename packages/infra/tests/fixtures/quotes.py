"""The shapes the fixture chains are expressed in.

A fixture chain is a small, immutable bundle of option quotes at one instant for
one underlying, plus enough metadata for downstream workstreams to drive their
own builders. Quotes are kept at the raw-ish quote level (bid/ask/last and the
quote's own timestamp) rather than as finished snapshots, so the consuming code
(C's snapshot/forward/IV builders) still does its own work — the fixture is the
input, not the answer.

Conventions for the pathological cases, documented so a reader of one fixture
knows what "missing" looks like:

* a one-sided quote has ``bid`` or ``ask`` set to ``None``;
* a zero-bid quote has ``bid == 0.0``;
* a missing multiplier is ``multiplier == 0.0`` on the instrument key;
* a missing currency is ``currency == ""`` on the instrument key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from algotrading.infra.contracts import InstrumentKey

from .synthetic import SyntheticSurface


@dataclass(frozen=True, slots=True)
class OptionQuoteFixture:
    """One option's two-sided quote at a point in time."""

    instrument: InstrumentKey
    bid: float | None
    ask: float | None
    last: float | None
    quote_ts: datetime


@dataclass(frozen=True, slots=True)
class ChainFixture:
    """A named, immutable option chain used as shared test ground.

    ``known_answers`` is populated only for the synthetic case, where the true
    forward, vols, and SVI parameters are recoverable from the quotes.
    """

    name: str
    description: str
    as_of: datetime
    underlying: InstrumentKey
    underlying_spot: float
    quotes: tuple[OptionQuoteFixture, ...]
    known_answers: SyntheticSurface | None = None
