"""Ticket router: build and **preview** an order ticket from a composed basket (WS 3A).

``POST /api/ticket/preview`` takes the same composed-basket body the basket router accepts plus
the ticket build params (target broker, time-in-force, price spec), calls the **pure**
:func:`~algotrading.infra.orders.build_ticket`, and returns the serialized ticket. It is
**read-only / paper**: no store mutation, no broker call, no credential, **no transmission**, and
no ``/api/orders``. Sending is WS 3B behind an explicit owner gate.

A malformed request is a **labelled 400** (mirroring the basket router's ``bad_basket``): the
pydantic shape, the :class:`~algotrading.infra.contracts.ContractValidationError` from a bad leg,
a bad ``trade_date``, and the :class:`~algotrading.infra.orders.TicketError` from the builder all
surface as ``{"error": "bad_ticket", "detail": …}``. Never a 500, never a silent coercion. The
HTTP shape is the seam — it stays in lockstep with the web client.
"""

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
    """A price spec on the wire: ``{"kind": "market"}`` or ``{"kind": "limit", "price": …}``."""

    kind: str = "market"
    price: float | None = None


class TicketLegIn(BaseModel):
    """One leg of the composed basket the ticket is built from (the 2A wire shape)."""

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None


class TicketPreviewIn(BaseModel):
    """The preview request: a composed basket plus the ticket build params.

    Only the *shape* is validated here (a missing/mistyped field becomes a labelled 400 naming
    the field); the domain rules — leg side/sign agreement, a coherent price spec, a real broker —
    live on the contracts and the pure builder, the single home for them. ``trade_date`` stays a
    raw string: empty/missing means "the latest banked day", as in the basket router.
    """

    basket_id: str
    underlying: str
    trade_date: str | None = ""
    legs: list[TicketLegIn] = []
    # Defaults derive from the enums (the single source of truth), never bare string literals,
    # so they cannot silently drift from `TargetBroker` / `TimeInForce`.
    target_broker: str = TargetBroker.IBKR.value
    time_in_force: str = TimeInForce.DAY.value
    price_spec: PriceSpecIn = Field(default_factory=PriceSpecIn)


def _resolve_trade_date(ctx: AppContext, parsed: TicketPreviewIn) -> date:
    """An explicit ISO date, or the latest banked analytics day for the underlying.

    The web client sends ``trade_date: ""`` until a date is picked, meaning "the latest day with
    banked analytics" — the latest-with-data default the basket/coverage routers apply. An empty
    date over an underlying with no banked grid is a ``ValueError`` the caller turns into a 400.
    """
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
    """Build the typed :class:`Basket` from the validated body, raising on anything malformed."""
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
    """Resolve the named broker to the enum — a labelled :class:`TicketError` if unknown."""
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
    """Map the wire price spec to the closed :data:`PriceSpec` set (Market | Limit)."""
    if spec.kind == "market":
        return Market()
    if spec.kind == "limit":
        if spec.price is None:
            raise TicketError("a limit price spec needs a price", field="price", value=None)
        return Limit(spec.price)
    raise TicketError("price spec kind must be 'market' or 'limit'", field="kind", value=spec.kind)


@router.get("/options")
async def ticket_options() -> JSONResponse:
    """The selectable broker / time-in-force values, derived from the enums.

    The single source for the web Ticket panel's selectors, so the front never hardcodes a
    parallel list that could drift from `TargetBroker` / `TimeInForce`.
    """
    return JSONResponse(
        {
            "brokers": [broker.value for broker in TargetBroker],
            "time_in_force": [tif.value for tif in TimeInForce],
        }
    )


@router.post("/preview")
async def preview_ticket(ctx: CtxDep, request: Request) -> JSONResponse:
    """Build and preview an order ticket from a composed basket (read-only, paper).

    Calls the pure builder and serializes the result. **Nothing is transmitted**; the broker is
    named and validated, never connected to. A malformed request is a labelled 400.
    """
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
