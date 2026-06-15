"""The password-gated booking commit — the book's single write barrier (TARGET §7 #1).

This is the **one** verb that mutates the book. :func:`book` takes a previewed 3A
:class:`~algotrading.infra.orders.OrderTicket`, a password, the as-of option chain, a leg
resolver, the append-only fills ledger, and the append-only booking audit log, and:

1. **Verifies the password gate** (:mod:`~.password_gate`) — fail-closed. A wrong/absent password
   or an unconfigured/malformed gate is a labelled block: **no fill is synthesized and the fills
   ledger's ``append`` is never called.** The block is still recorded in the audit log.
2. On a verified gate, **synthesizes the concrete paper fill(s)** by resolving each ticket leg
   through the :class:`~.concretization_seam.LegResolver` (ADR 0043: a booked fill is a concrete
   contract resolved as-of the booking date) and **appends them once** to the fills ledger with
   ticket/basket lineage.
3. **Appends one provenance-stamped record** of the decision — commit *or* block — to the
   append-only audit log. Every attempt to mutate the book leaves an immutable, ordered trace
   (TARGET §6).

It is **paper / read-only against the broker**: no bytes leave the process, and this module
imports **no broker and no order-submit symbol** (asserted by ``test_two_gates``). Live
transmission is the *other* gate (``execution-order-sign-and-send``, 3B) and stays off — the two
gates are never conflated.

Purity / DI: the ledger, the audit log, the resolver, the chain, the wall-clock ``now``, the
``config_hashes``, the ``booking_id``, and the id-minting function are all injected, so the verb is
deterministic and testable. The only ambient input is the password gate's environment, isolated
behind :mod:`~.password_gate`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp, code_version, source_ref, stamp
from algotrading.infra.orders import OrderTicket

from ..fills import Fill
from ..ledger import FillsLedger
from .audit import BLOCK, COMMIT, BookingAudit, BookingAuditLog
from .concretization_seam import ConcretizationError, LegResolver
from .password_gate import GateBlock, GateDecision, GateOpen, verify_password_from_environment

# The distribution whose version brands a fill's / audit record's provenance stamp.
_DISTRIBUTION = "algotrading-execution"

# The table a fill's provenance stamp references as its source — the previewed ticket lineage.
_TICKET_TABLE = "order_tickets"

# A booking is always paper here (no broker bytes leave the process); 3B owns the live path.
_PAPER = "paper"

# A resolver/concretization failure on a gate-open commit is itself a labelled block kind, so a
# leg that cannot be priced fails closed exactly like a bad password — never a half-written book.
UNRESOLVABLE_LEG = "unresolvable_leg"


@dataclass(frozen=True, slots=True)
class BookingCommitted:
    """A verified commit: the fills appended to the ledger, plus the audit record of the commit."""

    fills: tuple[Fill, ...]
    audit: BookingAudit


@dataclass(frozen=True, slots=True)
class BookingBlocked:
    """A fail-closed block: the labelled reason, and the audit record of the refusal.

    No fill was synthesized and the fills ledger was never appended to — the audit record carries
    an empty ``fill_ids``. ``reason``/``detail`` mirror the gate's :class:`GateBlock` (or a
    concretization failure), so the caller can branch on the *kind* of block.
    """

    reason: str
    detail: str
    audit: BookingAudit


# The verb's answer is exactly one of these — a committed booking or a labelled block.
BookingResult = BookingCommitted | BookingBlocked


def _fill_stamp(
    *,
    now: datetime,
    config_hashes: Mapping[str, str],
    source_basket_id: str,
    contract_key: str,
) -> ProvenanceStamp:
    """A provenance stamp for one fill, pointing back at the previewed ticket/basket lineage."""
    return stamp(
        calc_ts=now,
        code_version=code_version(_DISTRIBUTION),
        config_hashes=config_hashes,
        source_records=(source_ref(_TICKET_TABLE, source_basket_id, contract_key),),
        source_timestamps=(now,),
    )


def _make_fills(
    ticket: OrderTicket,
    *,
    resolver: LegResolver,
    chain: object,
    now: datetime,
    booking_id: str,
    config_hashes: Mapping[str, str],
    mint_fill_id: Callable[[int], str],
) -> tuple[Fill, ...]:
    """Resolve every ticket leg to a concrete, priced paper :class:`Fill` (ADR 0043).

    Each leg is concretized as-of the ticket's ``trade_date`` (the booking date) against the
    passed-in ``chain`` — no wall clock, no broker. The fill carries lineage to the booking
    decision (``booking_id``) and the originating basket (``source_basket_id``), and its own
    provenance stamp. A leg the resolver cannot price raises :class:`ConcretizationError`, which
    the caller turns into a fail-closed block (no fill appended).
    """
    fills: list[Fill] = []
    for index, leg in enumerate(ticket.legs):
        resolved = resolver(leg, as_of=ticket.trade_date, chain=chain)
        fills.append(
            Fill(
                fill_id=mint_fill_id(index),
                booking_id=booking_id,
                source_basket_id=ticket.source_basket_id,
                trade_date=ticket.trade_date,
                underlying=ticket.underlying,
                contract_key=resolved.contract_key,
                signed_qty=resolved.signed_qty,
                price=resolved.price,
                fill_ts=now,
                provenance=_fill_stamp(
                    now=now,
                    config_hashes=config_hashes,
                    source_basket_id=ticket.source_basket_id,
                    contract_key=resolved.contract_key,
                ),
                mode=_PAPER,
                broker_contract_id=resolved.broker_contract_id,
            )
        )
    return tuple(fills)


def _audit_record(
    ticket: OrderTicket,
    *,
    decision: str,
    now: datetime,
    booking_id: str,
    fills: tuple[Fill, ...],
    config_hashes: Mapping[str, str],
    block_reason: str | None,
) -> BookingAudit:
    """Build the provenance-stamped audit record for one commit/block decision.

    The stamp's source references point at the fills the decision produced (none on a block), so
    lineage resolves a booked position back to the gated decision. The stamp is order-independent
    by construction (:func:`stamp` sorts its sources), which is what makes a replay of the
    decision sequence reorder-stable. The ``audit_id`` is the ``booking_id`` (one decision per
    booking), so a commit's fills and its audit record share one identity.
    """
    source_records = tuple(
        source_ref("fills", fill.fill_id, fill.contract_key) for fill in fills
    )
    source_timestamps = tuple(fill.fill_ts for fill in fills)
    if not source_records:
        # A block produced no fill; stamp the decision against the ticket lineage so it still
        # carries a non-empty, well-formed provenance (the audit door validates it).
        source_records = (source_ref(_TICKET_TABLE, ticket.source_basket_id, booking_id),)
        source_timestamps = (now,)
    provenance = stamp(
        calc_ts=now,
        code_version=code_version(_DISTRIBUTION),
        config_hashes=config_hashes,
        source_records=source_records,
        source_timestamps=source_timestamps,
    )
    return BookingAudit(
        audit_id=booking_id,
        booking_id=booking_id,
        source_basket_id=ticket.source_basket_id,
        trade_date=ticket.trade_date,
        underlying=ticket.underlying,
        decision=decision,
        fill_ids=tuple(fill.fill_id for fill in fills),
        decision_ts=now,
        provenance=provenance,
        block_reason=block_reason,
    )


def _blocked(
    ticket: OrderTicket,
    *,
    reason: str,
    detail: str,
    now: datetime,
    booking_id: str,
    config_hashes: Mapping[str, str],
    audit_log: BookingAuditLog,
) -> BookingBlocked:
    """Record a fail-closed block in the audit log and return it. No ledger write."""
    audit = _audit_record(
        ticket,
        decision=BLOCK,
        now=now,
        booking_id=booking_id,
        fills=(),
        config_hashes=config_hashes,
        block_reason=reason,
    )
    audit_log.append(audit)
    return BookingBlocked(reason=reason, detail=detail, audit=audit)


def book(
    ticket: OrderTicket,
    password: str,
    *,
    ledger: FillsLedger,
    audit_log: BookingAuditLog,
    resolver: LegResolver,
    chain: object,
    now: datetime,
    booking_id: str,
    config_hashes: Mapping[str, str],
    mint_fill_id: Callable[[int], str],
    verify_gate: Callable[[str], GateDecision] = verify_password_from_environment,
) -> BookingResult:
    """Commit a previewed ticket into paper fill(s) — only when the password gate verifies.

    Returns :class:`BookingCommitted` on success (fills appended once to the ledger, one audit
    record appended) or :class:`BookingBlocked` on a fail-closed refusal (no fill, the ledger's
    ``append`` never invoked, one block audit record appended). Block kinds: the gate's
    ``wrong_password``/``absent_password``/``unconfigured_gate``/``malformed_gate_config``, or
    :data:`UNRESOLVABLE_LEG` when a verified commit hits a leg the resolver cannot price.

    Every path writes exactly one audit record, so the audit log is the complete decision
    history. ``verify_gate`` is injected (defaults to the real environment check) so tests drive
    the gate deterministically without touching ``os.environ``. No broker, no transmission —
    paper only.
    """
    decision = verify_gate(password)
    if isinstance(decision, GateBlock):
        return _blocked(
            ticket,
            reason=decision.reason,
            detail=decision.detail,
            now=now,
            booking_id=booking_id,
            config_hashes=config_hashes,
            audit_log=audit_log,
        )

    # The gate is open. Synthesize the concrete fills *before* any write; an unresolvable leg
    # fails closed (no fill appended) exactly like a bad password.
    if not isinstance(decision, GateOpen):  # pragma: no cover - exhaustiveness guard
        raise TypeError(f"unexpected gate decision: {decision!r}")
    try:
        fills = _make_fills(
            ticket,
            resolver=resolver,
            chain=chain,
            now=now,
            booking_id=booking_id,
            config_hashes=config_hashes,
            mint_fill_id=mint_fill_id,
        )
    except ConcretizationError as exc:
        return _blocked(
            ticket,
            reason=UNRESOLVABLE_LEG,
            detail=str(exc),
            now=now,
            booking_id=booking_id,
            config_hashes=config_hashes,
            audit_log=audit_log,
        )

    # Ordering is deliberate: fills FIRST, then the audit decision. "Accounting from fills"
    # (§6) means the book IS the fills — so the book must never claim a position the ledger does
    # not hold. Writing the audit first would risk a COMMITTED decision whose fills never landed
    # (a phantom position); writing it last means the only crash window leaves *durable fills with
    # no decision yet* — the safe direction, and fully recoverable: every fill carries its
    # `booking_id`, so a reconciliation reconstructs the missing decision from the ledger. Both
    # appends are within-process loud-on-failure (a dup id raises, never silently skips); the
    # residual gap is a hard process crash between the two, tracked for hardening in
    # T-audit-2026-06-14-findings.
    ledger.append_many(fills)
    audit = _audit_record(
        ticket,
        decision=COMMIT,
        now=now,
        booking_id=booking_id,
        fills=fills,
        config_hashes=config_hashes,
        block_reason=None,
    )
    audit_log.append(audit)
    return BookingCommitted(fills=fills, audit=audit)
