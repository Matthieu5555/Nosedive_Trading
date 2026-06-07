"""Basket router: compose a multi-leg basket and price/risk it off the stored analytics (WS 2A).

Accepts an operator-composed basket (its legs) and returns the basket priced and risked as the
**book-additive sum** of the per-position dollar Greeks WS-1F already produced — read back from
the ``projected_option_analytics`` table (option legs) plus the spot from ``daily_bar`` (stock
legs). This is summation, never a recompute (``infra.risk.multileg.basket_risk``); the store is
opened read-only (the EOD cron is the sole writer, ADR 0034 §1).

A leg that references an unpriced cell (or an ambiguous provider, or a missing spot) is a
**labelled gap** in the payload with HTTP 200 — never a 500. A malformed basket (bad side/sign,
a non-finite quantity, a bad trade date) is a labelled 400, mirroring the surfaces router's
``bad_trade_date``. The HTTP shape is the seam: it stays in lockstep with the web client.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg, ContractValidationError
from algotrading.infra.risk import basket_risk
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import basket_risk_to_dict

router = APIRouter(prefix="/api/basket", tags=["basket"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _build_basket(body: object) -> Basket:
    """Build the typed :class:`Basket` from the request body, raising on anything malformed.

    Every shape error (not a dict, missing/!str fields, a bad leg, a side/sign contradiction)
    surfaces as a ``ValueError``/``ContractValidationError``/``TypeError``/``KeyError`` the caller
    turns into a labelled 400 — never a silent default.
    """
    if not isinstance(body, dict):
        raise ValueError("basket body must be a JSON object")
    raw_legs = body.get("legs", [])
    if not isinstance(raw_legs, list):
        raise ValueError("legs must be a list")
    legs = tuple(
        BasketLeg(
            instrument_kind=leg["instrument_kind"],
            side=leg["side"],
            quantity=float(leg["quantity"]),
            underlying=leg["underlying"],
            tenor_label=leg.get("tenor_label"),
            delta_band=leg.get("delta_band"),
        )
        for leg in raw_legs
    )
    return Basket(
        basket_id=body["basket_id"],
        trade_date=date.fromisoformat(body["trade_date"]),
        underlying=body["underlying"],
        legs=legs,
        provider=body.get("provider"),
    )


@router.post("/risk")
async def price_basket(request: Request) -> JSONResponse:
    """Price and risk a composed basket off the stored Tab-1 analytics (read-only).

    Reads the WS-1F analytics grid for the basket's ``(trade_date, underlying[, provider])`` and
    the stock-leg spots from ``daily_bar`` on the same date, then sums the per-leg dollar Greeks.
    Returns the priced/risked basket with each dollar number carrying its unit string and the
    per-leg breakdown + labelled gaps (HTTP 200). A malformed basket is a labelled 400.
    """
    ctx = _context(request)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "bad_basket", "detail": "body is not valid JSON"}, status_code=400
        )
    try:
        basket = _build_basket(body)
    except (ContractValidationError, ValueError, TypeError, KeyError) as exc:
        return JSONResponse({"error": "bad_basket", "detail": str(exc)}, status_code=400)

    # Read-only: the WS-1F grid for this day/underlying. Narrow by underlying so a version-blind
    # read never bleeds another name's grid in (mirrors the surfaces/price-history routers).
    analytics_rows = [
        row
        for row in ctx.store.read(
            "projected_option_analytics",
            trade_date=basket.trade_date,
            underlying=basket.underlying,
            provider=basket.provider,
        )
        if row.underlying == basket.underlying
    ]

    # Stock legs need their underlying's spot: the close from ``daily_bar`` on the basket's
    # trade_date (the read-only source the price-history router uses). Only read it when a stock
    # leg is present, and only for the underlyings those legs name.
    stock_underlyings = {
        leg.underlying for leg in basket.legs if leg.instrument_kind == "stock"
    }
    spot_by_underlying: dict[str, float] = {}
    if stock_underlyings:
        bars = ctx.store.read(
            "daily_bar", trade_date=basket.trade_date, provider=basket.provider
        )
        for bar in bars:
            if bar.underlying in stock_underlyings:
                spot_by_underlying[bar.underlying] = bar.close

    result = basket_risk(
        basket, analytics_rows=analytics_rows, spot_by_underlying=spot_by_underlying
    )
    return JSONResponse(basket_risk_to_dict(result))
