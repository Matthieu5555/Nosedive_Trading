"""Booking — the password-gated write barrier that commits a previewed ticket into fills.

The public surface of the booking chain's commit step (TARGET §7 #1):

* :func:`book` — the one verb that mutates the book, behind the password gate, paper-only.
* :class:`BookingResult` (:class:`BookingCommitted` | :class:`BookingBlocked`) — its outcome.
* :func:`verify_password` / :func:`hash_password` — the scrypt gate and its provisioning helper.
* :class:`BookingAudit` + :class:`BookingAuditLog` (:class:`InMemoryBookingAuditLog` /
  :class:`JsonlBookingAuditLog`) — the append-only decision log every commit/block is recorded in.
* :class:`ResolvedLeg` / :class:`LegResolver` / :class:`ConcretizationError` — the seam this
  consumes from ``execution-fill-concretization`` (ADR 0043), defined as the interface here so
  the commit depends on the shape, not the parallel module's code.

There is **no broker and no order-submit symbol** in this package — the live-send gate is 3B,
separate and off this week (asserted by ``test_two_gates``).
"""

from __future__ import annotations

from .audit import (
    BLOCK,
    COMMIT,
    BookingAudit,
    BookingAuditError,
    BookingAuditLog,
    InMemoryBookingAuditLog,
    JsonlBookingAuditLog,
)
from .commit import (
    UNRESOLVABLE_LEG,
    BookingBlocked,
    BookingCommitted,
    BookingResult,
    book,
)
from .concretization_seam import (
    ConcretizationError,
    LegResolver,
    ResolvedLeg,
    signed_quantity_for,
)
from .password_gate import (
    ABSENT_PASSWORD,
    MALFORMED_GATE_CONFIG,
    UNCONFIGURED_GATE,
    WRONG_PASSWORD,
    GateBlock,
    GateDecision,
    GateOpen,
    hash_password,
    verify_password,
    verify_password_from_environment,
)

__all__ = [
    "ABSENT_PASSWORD",
    "BLOCK",
    "COMMIT",
    "MALFORMED_GATE_CONFIG",
    "UNCONFIGURED_GATE",
    "UNRESOLVABLE_LEG",
    "WRONG_PASSWORD",
    "BookingAudit",
    "BookingAuditError",
    "BookingAuditLog",
    "BookingBlocked",
    "BookingCommitted",
    "BookingResult",
    "ConcretizationError",
    "GateBlock",
    "GateDecision",
    "GateOpen",
    "InMemoryBookingAuditLog",
    "JsonlBookingAuditLog",
    "LegResolver",
    "ResolvedLeg",
    "book",
    "hash_password",
    "signed_quantity_for",
    "verify_password",
    "verify_password_from_environment",
]
