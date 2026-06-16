from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg, ContractValidationError
from algotrading.infra.orders import (
    Limit,
    Market,
    PriceSpec,
    TargetBroker,
    TicketError,
    TimeInForce,
    build_ticket,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..context import AppContext
from ..deps import BadRequestError, CtxDep, parse_json_body
from ..serializers import ticket_to_dict
from ..store_reads import latest_partition_date

router = APIRouter(prefix="/api/ticket", tags=["ticket"])


class PriceSpecIn(BaseModel):

    kind: str = "market"
    price: float | None = None


class TicketLegIn(BaseModel):

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None


class TicketPreviewIn(BaseModel):

    basket_id: str
    underlying: str
    trade_date: str | None = ""
    legs: list[TicketLegIn] = []
    target_broker: str = TargetBroker.IBKR.value
    time_in_force: str = TimeInForce.DAY.value
    price_spec: PriceSpecIn = Field(default_factory=PriceSpecIn)


def _resolve_trade_date(ctx: AppContext, parsed: TicketPreviewIn) -> date:
    if parsed.trade_date:
        return date.fromisoformat(parsed.trade_date)
    latest = latest_partition_date(
        ctx.store.list_partitions("projected_option_analytics"), parsed.underlying
    )
    if latest is None:
        raise ValueError(
            "trade_date is empty and no analytics are banked for underlying "
            f"{parsed.underlying!r}"
        )
    return latest


def _build_basket(ctx: AppContext, parsed: TicketPreviewIn) -> Basket:
    legs = tuple(
        BasketLeg(
            instrument_kind=leg.instrument_kind,
            side=leg.side,
            quantity=leg.quantity,
            underlying=leg.underlying,
            tenor_label=leg.tenor_label,
            delta_band=leg.delta_band,
        )
        for leg in parsed.legs
    )
    return Basket(
        basket_id=parsed.basket_id,
        trade_date=_resolve_trade_date(ctx, parsed),
        underlying=parsed.underlying,
        legs=legs,
    )


def _target_broker(value: str) -> TargetBroker:
    try:
        return TargetBroker(value)
    except ValueError as exc:
        raise TicketError("unknown target broker", field="target_broker", value=value) from exc


def _time_in_force(value: str) -> TimeInForce:
    try:
        return TimeInForce(value)
    except ValueError as exc:
        raise TicketError("unknown time-in-force", field="time_in_force", value=value) from exc


def _price_spec(spec: PriceSpecIn) -> PriceSpec:
    if spec.kind == "market":
        return Market()
    if spec.kind == "limit":
        if spec.price is None:
            raise TicketError("a limit price spec needs a price", field="price", value=None)
        return Limit(spec.price)
    raise TicketError("price spec kind must be 'market' or 'limit'", field="kind", value=spec.kind)


@router.get("/options")
async def ticket_options() -> JSONResponse:
    return JSONResponse(
        {
            "brokers": [broker.value for broker in TargetBroker],
            "time_in_force": [tif.value for tif in TimeInForce],
        }
    )


@router.post("/preview")
async def preview_ticket(ctx: CtxDep, request: Request) -> JSONResponse:
    body = await parse_json_body(request, error="bad_ticket")
    try:
        parsed = TicketPreviewIn.model_validate(body)
        ticket = build_ticket(
            _build_basket(ctx, parsed),
            broker=_target_broker(parsed.target_broker),
            tif=_time_in_force(parsed.time_in_force),
            price_spec=_price_spec(parsed.price_spec),
        )
    except (ValidationError, ValueError, ContractValidationError, TicketError) as exc:
        raise BadRequestError({"error": "bad_ticket", "detail": str(exc)}) from exc
    return JSONResponse(ticket_to_dict(ticket))
