"""Basket router: compose a multi-leg basket and price/risk it off the stored analytics (WS 2A).

Accepts an operator-composed basket (its legs) and returns the basket priced and risked as the
**book-additive sum** of the per-position dollar Greeks WS-1F already produced — read back from
the ``projected_option_analytics`` table (option legs) plus the spot from ``daily_bar`` (stock
legs). This is summation, never a recompute (``infra.risk.multileg.basket_risk``); the store is
opened read-only (the EOD cron is the sole writer, ADR 0034 §1).

A leg that references an unpriced cell (or an ambiguous provider, or a missing spot) is a
**labelled gap** in the payload with HTTP 200 — never a 500. A malformed basket (bad side/sign,
a non-finite quantity, a bad trade date) is a labelled 400, mirroring the surfaces router's
``bad_trade_date``. An **empty/missing** ``trade_date`` is the operator default, not an error:
it resolves to the latest banked analytics day for the underlying (the latest-with-data default
the health/coverage routers apply). The HTTP shape is the seam: it stays in lockstep with the
web client.
"""

from __future__ import annotations

from datetime import date

from algotrading.core.config import load_platform_config
from algotrading.infra.contracts import Basket, BasketLeg, ContractValidationError
from algotrading.infra.risk import basket_risk
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..basket_scenarios import basket_stress
from ..context import AppContext
from ..serializers import basket_risk_to_dict, basket_scenarios_to_dict

router = APIRouter(prefix="/api/basket", tags=["basket"])


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _resolve_trade_date(ctx: AppContext, body: dict) -> date:
    """The basket's trade date: an explicit ISO date, or the latest banked analytics day.

    The web client sends ``trade_date: ""`` until the operator picks a date, meaning "the latest
    day with banked analytics for this underlying" — the same latest-with-data default the
    health/coverage routers apply. An empty date over an underlying with no banked grid is a
    ``ValueError`` the caller turns into a labelled 400 (there is no day to price against).
    """
    raw = body.get("trade_date")
    if raw:
        return date.fromisoformat(str(raw))
    underlying = body["underlying"]
    dates = [
        part_date
        for part_date, part_underlying in ctx.store.list_partitions("projected_option_analytics")
        if part_underlying == underlying
    ]
    if not dates:
        raise ValueError(
            f"trade_date is empty and no analytics are banked for underlying {underlying!r}"
        )
    return max(dates)


def _build_basket(ctx: AppContext, body: object) -> Basket:
    """Build the typed :class:`Basket` from the request body, raising on anything malformed.

    Every shape error (not a dict, missing/!str fields, a bad leg, a side/sign contradiction)
    surfaces as a ``ValueError``/``ContractValidationError``/``TypeError``/``KeyError`` the caller
    turns into a labelled 400 — never a silent default. An empty/missing ``trade_date`` is not an
    error: it resolves to the latest banked analytics day (``_resolve_trade_date``).
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
        trade_date=_resolve_trade_date(ctx, body),
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
        basket = _build_basket(ctx, body)
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


def _option_multiplier_currency(ctx: AppContext, basket: Basket) -> tuple[float | None, str | None]:
    """The underlying's option contract multiplier + currency from ``instrument_master``.

    The projected grid cells are synthetic (pinned tenor / delta band), so there is no per-cell
    instrument master; the multiplier and currency are properties of the underlying's option
    contract, uniform across its options. Prefer an option row; fall back to any master row for
    the underlying. ``(None, None)`` when the underlying has no master — every option leg then
    becomes a labelled ``no_instrument_master`` gap rather than a silently wrong monetization.
    """
    masters = ctx.store.read(
        "instrument_master", trade_date=basket.trade_date, underlying=basket.underlying
    )
    for master in masters:
        instrument = master.instrument
        if instrument.is_option and instrument.underlying_symbol == basket.underlying:
            return instrument.multiplier, instrument.currency
    for master in masters:
        if master.instrument.underlying_symbol == basket.underlying:
            return master.instrument.multiplier, master.instrument.currency
    return None, None


@router.post("/scenarios")
async def stress_basket(request: Request) -> JSONResponse:
    """Full-reprice a composed basket over the cartesian (spot x vol) stress grid (WS 2B).

    The interactive, no-cron counterpart to ``GET /api/risk/scenarios`` (which reads the cron's
    persisted surface): reconstructs a valuation per option leg from the stored WS-1F analytics
    grid (:mod:`..basket_scenarios`), reprices the basket over the config-driven surface, and
    returns the same surface payload shape the ``StressSurface`` web component renders plus the
    worst-case cell and labelled per-leg gaps. A malformed basket is a labelled 400; an
    unresolved leg is a labelled gap inside a 200, never a 500.
    """
    ctx = _context(request)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "bad_basket", "detail": "body is not valid JSON"}, status_code=400
        )
    try:
        basket = _build_basket(ctx, body)
    except (ContractValidationError, ValueError, TypeError, KeyError) as exc:
        return JSONResponse({"error": "bad_basket", "detail": str(exc)}, status_code=400)

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
    multiplier, currency = _option_multiplier_currency(ctx, basket)

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

    config = load_platform_config(ctx.configs_dir).scenario
    result = basket_stress(
        basket,
        analytics_rows=analytics_rows,
        multiplier=multiplier,
        currency=currency,
        spot_by_underlying=spot_by_underlying,
        config=config,
    )
    return JSONResponse(basket_scenarios_to_dict(result))
