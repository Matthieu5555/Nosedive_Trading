"""algotrading.execution — the booking chain above infra (paper, read-only).

This package owns the *position store fed by fills* and its readers (TARGET §5.1/§5.5/§7.1):
the book is accounted from fills, never from intentions (§6). It also owns the
**fill-concretization** seam (ADR 0043): the pure, as-of transform from an abstract grid-cell
order-ticket leg into a concrete, priced paper fill — the step WS 3A deferred and
booking-commit (TARGET §7 #1) consumes. See ``concretization.py``. It imports infra/core only
and is never imported by them.

**Two gates, neither here.** The password-gated *booking commit* (which mints the fills this
store ingests) and the live broker *send* gate (3B) are separate, later barriers. Nothing in
this package transmits an order, reads a credential, or connects to a broker — fills are
paper by construction.
"""

from __future__ import annotations

from .book import BOOKED, booked_position_set, position_set_from_fills
from .concretization import (
    MARK_SOURCE_ANALYTICS_PRICE,
    MARK_SOURCE_SNAPSHOT_MID,
    ConcreteChain,
    ConcreteFill,
    ConcretizationError,
    concretize,
    option_right_for_band,
)
from .fills import Fill, FillError
from .ledger import (
    FillsLedger,
    FillsLedgerError,
    InMemoryFillsLedger,
    JsonlFillsLedger,
)

__all__ = [
    "BOOKED",
    "MARK_SOURCE_ANALYTICS_PRICE",
    "MARK_SOURCE_SNAPSHOT_MID",
    "ConcreteChain",
    "ConcreteFill",
    "ConcretizationError",
    "Fill",
    "FillError",
    "FillsLedger",
    "FillsLedgerError",
    "InMemoryFillsLedger",
    "JsonlFillsLedger",
    "booked_position_set",
    "concretize",
    "option_right_for_band",
    "position_set_from_fills",
]
