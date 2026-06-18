from __future__ import annotations

import uuid
from datetime import UTC, datetime

from algotrading.execution import (
    BookingCommitted,
    JsonlBookingAuditLog,
    JsonlFillsLedger,
    book,
)
from algotrading.execution.booking import LegResolver
from algotrading.infra.contracts import ContractValidationError
from algotrading.infra.orders import TicketError, build_ticket
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..booking_resolve import StoreLegResolver
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

_BOOKING_DIRNAME = "booking"
_FILLS_FILENAME = "fills.jsonl"
_AUDIT_FILENAME = "booking_audit.jsonl"

_PAPER_PORTFOLIO = "paper"


def _resolver_for(ctx: AppContext) -> LegResolver:
    # Concretize each grid-cell leg into a real contract + paper mark off the as-of chain
    # (ADR 0043). An unresolvable cell becomes a labelled paper block, not a 500.
    return StoreLegResolver(ctx)


def _booking_dir(ctx: AppContext) -> object:
    booking_dir = ctx.store_root / _BOOKING_DIRNAME
    booking_dir.mkdir(parents=True, exist_ok=True)
    return booking_dir


@router.post("/commit")
async def commit_booking(ctx: CtxDep, request: Request) -> JSONResponse:
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
