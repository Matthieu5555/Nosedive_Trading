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

_DISTRIBUTION = "algotrading-execution"

_TICKET_TABLE = "order_tickets"

_PAPER = "paper"

UNRESOLVABLE_LEG = "unresolvable_leg"


@dataclass(frozen=True, slots=True)
class BookingCommitted:

    fills: tuple[Fill, ...]
    audit: BookingAudit


@dataclass(frozen=True, slots=True)
class BookingBlocked:

    reason: str
    detail: str
    audit: BookingAudit


BookingResult = BookingCommitted | BookingBlocked


def _fill_stamp(
    *,
    now: datetime,
    config_hashes: Mapping[str, str],
    source_basket_id: str,
    contract_key: str,
) -> ProvenanceStamp:
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
    source_records = tuple(
        source_ref("fills", fill.fill_id, fill.contract_key) for fill in fills
    )
    source_timestamps = tuple(fill.fill_ts for fill in fills)
    if not source_records:
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
