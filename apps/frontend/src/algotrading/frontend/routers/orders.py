"""Paper order routes for the operator frontend."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..data import (
    OrderTicket,
    get_orders_dashboard,
    json_payload,
    preview_order,
    submit_paper_order,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class OrderTicketRequest(BaseModel):
    """Request body for paper order preview and submission."""

    model_config = ConfigDict(extra="forbid")

    side: Literal["buy", "sell"]
    symbol: str = "SPX"
    quantity: int = Field(gt=0, le=10_000)
    limit_price: float = Field(gt=0)
    instrument_type: Literal["index_option", "equity"] = "index_option"
    expiry: date | None = None
    strike: float | None = Field(default=None, gt=0)
    option_type: Literal["call", "put"] | None = None
    time_in_force: Literal["day", "gtc"] = "day"


@router.get("")
async def orders_dashboard() -> JSONResponse:
    """Return paper order state and history."""

    return JSONResponse(json_payload(get_orders_dashboard()))


@router.post("/preview")
async def preview(body: OrderTicketRequest) -> JSONResponse:
    """Preview paper order notional and greek impact."""

    ticket = _ticket_from_body(body)
    return JSONResponse(json_payload(preview_order(ticket)))


@router.post("")
async def submit(body: OrderTicketRequest) -> JSONResponse:
    """Submit a paper order."""

    ticket = _ticket_from_body(body)
    try:
        order = submit_paper_order(ticket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(json_payload(order), status_code=202)


def _ticket_from_body(body: OrderTicketRequest) -> OrderTicket:
    if body.instrument_type == "index_option" and (
        body.expiry is None or body.strike is None or body.option_type is None
    ):
        raise HTTPException(
            status_code=422,
            detail="index_option orders require expiry, strike, and option_type",
        )
    return OrderTicket(
        side=body.side,
        symbol=body.symbol.upper(),
        quantity=body.quantity,
        limit_price=body.limit_price,
        instrument_type=body.instrument_type,
        expiry=body.expiry,
        strike=body.strike,
        option_type=body.option_type,
        time_in_force=body.time_in_force,
    )
