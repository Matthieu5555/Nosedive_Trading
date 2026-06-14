"""Booking router: the password-gated commit of a previewed ticket into paper fill(s) (WS 3A/§7 #1).

``POST /api/booking/commit`` takes the same composed-basket body the ticket router previews, plus a
**password**, builds the previewed ticket with the pure 3A builder, and runs the
:func:`~algotrading.execution.book` write barrier. The password is verified against the booking
gate configured in the process environment (``$HOME/.env``); a wrong/absent/unconfigured/malformed
gate is a **labelled block** — fail-closed, **no fill written** — surfaced as
``{"decision": "block", "reason": …}`` with HTTP 200 (a block is a normal, expected answer, not a
client error). A malformed *request* (bad body/leg/date) stays a labelled **400** exactly like the
ticket router.

This is **paper / read-only against the broker**: the commit writes fills to a durable JSONL ledger
and a provenance-stamped audit record to a durable JSONL log under the data root — **no broker
bytes leave the process**, and no order-submit path exists here. The live-send affordance is 3B,
behind a *separate* gate, and is off this week.

**Concretization seam (ADR 0043, wire-on-merge).** Synthesizing a concrete, priced fill needs the
as-of grid-cell→contract resolver owned by ``execution-fill-concretization`` (built in parallel,
not yet merged). Until it lands, this router injects a *pending* resolver that raises a labelled
:class:`ConcretizationError`, so a *verified* booking returns a labelled ``unresolvable_leg`` block
— honest and fail-closed. The password write barrier is fully live regardless; the endpoint flips
to committing real fills the moment the resolver is wired in :func:`_resolver_for`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from algotrading.execution import (
    BookingCommitted,
    JsonlBookingAuditLog,
    JsonlFillsLedger,
    ResolvedLeg,
    book,
)
# The booking seam's labelled ConcretizationError (carries field/value) — distinct from the
# concretization engine's ConcretizationError re-exported at the package top level (ADR 0043).
from algotrading.execution.booking import ConcretizationError, LegResolver
from algotrading.infra.contracts import ContractValidationError
from algotrading.infra.orders import TicketError, build_ticket
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..context import AppContext
from ..deps import BadRequestError, CtxDep, parse_json_body
from .ticket import (
    TicketPreviewIn,
    _build_basket,
    _price_spec,
    _target_broker,
    _time_in_force,
)

router = APIRouter(prefix="/api/booking", tags=["booking"])

# The durable booking stores live beside the data store, under a dedicated booking dir.
_BOOKING_DIRNAME = "booking"
_FILLS_FILENAME = "fills.jsonl"
_AUDIT_FILENAME = "booking_audit.jsonl"

# The portfolio the paper book accrues into. One paper book this week (ADR 0042 scope); kept a
# named constant rather than a literal sprinkled through the code.
_PAPER_PORTFOLIO = "paper"


class _PendingConcretizationResolver:
    """The wire-on-merge placeholder for ``execution-fill-concretization``'s resolver.

    Raises a labelled :class:`ConcretizationError` for every leg, so a *verified* booking
    fails closed with ``unresolvable_leg`` until the real as-of resolver (ADR 0043) is merged
    and wired in :func:`_resolver_for`. It reads nothing — no chain, no broker, no clock.
    """

    def __call__(self, leg: object, *, as_of: date, chain: object) -> ResolvedLeg:
        raise ConcretizationError(
            "concretization (execution-fill-concretization, ADR 0043) is not yet wired; "
            "the password gate is live but no concrete fill can be synthesized yet",
            field="resolver",
            value="pending",
        )


def _resolver_for(ctx: AppContext) -> LegResolver:
    """The leg resolver the commit uses. Returns the pending placeholder until ADR 0043 merges.

    Wire-on-merge: replace the body with the real ``execution-fill-concretization`` resolver,
    reading the captured chain off ``ctx.store`` as-of the ticket's trade date. The commit verb
    and this router need no other change — the seam is :class:`LegResolver`.
    """
    return _PendingConcretizationResolver()


def _booking_dir(ctx: AppContext) -> object:
    booking_dir = ctx.store_root / _BOOKING_DIRNAME
    booking_dir.mkdir(parents=True, exist_ok=True)
    return booking_dir


@router.post("/commit")
async def commit_booking(ctx: CtxDep, request: Request) -> JSONResponse:
    """Commit a previewed ticket into paper fill(s) behind the password gate (read-only, paper).

    Builds the previewed ticket from the body, then runs the write barrier. A malformed request
    is a labelled 400; a gate refusal or an unresolvable leg is a labelled ``block`` (HTTP 200,
    no fill written); a verified, resolvable booking writes the fills + audit record and returns
    ``decision: "commit"``. **Nothing is transmitted to a broker.**
    """
    body = await parse_json_body(request, error="bad_booking")
    password = ""
    if isinstance(body, dict):
        password = str(body.get("password") or "")
    try:
        parsed = TicketPreviewIn.model_validate(body)
        ticket = build_ticket(
            _build_basket(ctx, parsed),
            broker=_target_broker(parsed.target_broker),
            tif=_time_in_force(parsed.time_in_force),
            price_spec=_price_spec(parsed.price_spec),
        )
    except (ValidationError, ValueError, ContractValidationError, TicketError) as exc:
        raise BadRequestError({"error": "bad_booking", "detail": str(exc)}) from exc

    booking_dir = _booking_dir(ctx)
    ledger = JsonlFillsLedger(booking_dir / _FILLS_FILENAME)  # type: ignore[operator]
    audit_log = JsonlBookingAuditLog(booking_dir / _AUDIT_FILENAME)  # type: ignore[operator]
    booking_id = f"bkg-{uuid.uuid4().hex}"
    now = datetime.now(UTC)

    result = book(
        ticket,
        password,
        ledger=ledger,
        audit_log=audit_log,
        resolver=_resolver_for(ctx),
        chain=ctx.store,
        now=now,
        booking_id=booking_id,
        config_hashes={"execution": "bff"},
        mint_fill_id=lambda index: f"{booking_id}-fill-{index}",
    )

    if isinstance(result, BookingCommitted):
        return JSONResponse(
            {
                "decision": "commit",
                "booking_id": booking_id,
                "fill_ids": list(result.audit.fill_ids),
                "fill_count": len(result.fills),
            }
        )
    return JSONResponse(
        {
            "decision": "block",
            "booking_id": booking_id,
            "reason": result.reason,
            "detail": result.detail,
        }
    )
