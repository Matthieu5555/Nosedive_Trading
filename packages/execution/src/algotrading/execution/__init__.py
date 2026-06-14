"""algotrading.execution — the booking chain above infra (paper, read-only).

This package owns the *position store fed by fills* and its readers (TARGET §5.1/§5.5/§7.1):
the book is accounted from fills, never from intentions (§6). It also owns the
**fill-concretization** seam (ADR 0043): the pure, as-of transform from an abstract grid-cell
order-ticket leg into a concrete, priced paper fill — the step WS 3A deferred and
booking-commit (TARGET §7 #1) consumes. See ``concretization.py``. It imports infra/core only
and is never imported by them.

**Two gates, neither one a broker send.** The *booking commit* — the password-gated write barrier
that mints the fills this store ingests — lives in :mod:`.booking` (TARGET §7 #1). The live broker
*send* gate (3B) is a separate, later barrier and is **not** here. Nothing in this package
transmits an order, reads a broker credential, or connects to a broker — fills are paper by
construction (the booking gate guards only the *paper* write to the book).

Note: :class:`booking.ConcretizationError` (the booking seam's labelled error) is kept under the
``.booking`` namespace; the canonical top-level ``ConcretizationError`` re-exported here is the
one from :mod:`.concretization`. The booking commit's :class:`~booking.LegResolver` is wired to
the real :func:`concretize` via a thin adapter as a follow-up (booking is fail-closed until then).
"""

from __future__ import annotations

from .book import BOOKED, booked_position_set, position_set_from_fills
from .booking import (
    BookingAudit,
    BookingAuditError,
    BookingAuditLog,
    BookingBlocked,
    BookingCommitted,
    BookingResult,
    InMemoryBookingAuditLog,
    JsonlBookingAuditLog,
    LegResolver,
    ResolvedLeg,
    book,
    hash_password,
    signed_quantity_for,
    verify_password,
    verify_password_from_environment,
)
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
    "BookingAudit",
    "BookingAuditError",
    "BookingAuditLog",
    "BookingBlocked",
    "BookingCommitted",
    "BookingResult",
    "ConcreteChain",
    "ConcreteFill",
    "ConcretizationError",
    "Fill",
    "FillError",
    "FillsLedger",
    "FillsLedgerError",
    "InMemoryBookingAuditLog",
    "InMemoryFillsLedger",
    "JsonlBookingAuditLog",
    "JsonlFillsLedger",
    "LegResolver",
    "ResolvedLeg",
    "book",
    "booked_position_set",
    "concretize",
    "hash_password",
    "option_right_for_band",
    "position_set_from_fills",
    "signed_quantity_for",
    "verify_password",
    "verify_password_from_environment",
]
