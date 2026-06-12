"""Basket router: compose a multi-leg basket and price/risk it off the stored analytics (WS 2A).

Accepts an operator-composed basket (its legs) and returns the basket priced and risked as the
**book-additive sum** of the per-position dollar Greeks WS-1F already produced — read back from
the ``projected_option_analytics`` table (option legs) plus the spot from ``daily_bar`` (stock
legs). This is summation, never a recompute (``infra.risk.multileg.basket_risk``); the store is
opened read-only (the EOD cron is the sole writer, ADR 0034 §1).

A leg that references an unpriced cell (or an ambiguous provider, or a missing spot) is a
**labelled gap** in the payload with HTTP 200 — never a 500. A malformed basket (bad side/sign,
a non-finite quantity, a bad trade date) is a labelled 400, mirroring the surfaces router's
``bad_trade_date``: the pydantic :class:`BasketIn` shape errors and the contract's own
:class:`ContractValidationError` both surface as ``{"error": "bad_basket", "detail": …}`` (the
latter via the app-level handler in ``create_app``). An **empty/missing** ``trade_date`` is the
operator default, not an error: it resolves to the latest banked analytics day for the
underlying (the latest-with-data default the health/coverage routers apply). The HTTP shape is
the seam: it stays in lockstep with the web client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrading.core.config import load_platform_config
from algotrading.infra.contracts import Basket, BasketLeg, ProjectedOptionAnalytics
from algotrading.infra.risk import basket_risk
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..basket_scenarios import basket_stress
from ..context import AppContext
from ..deps import BadRequestError, CtxDep, parse_json_body
from ..serializers import basket_risk_to_dict, basket_scenarios_to_dict
from ..store_reads import latest_partition_date, read_for_underlying

router = APIRouter(prefix="/api/basket", tags=["basket"])


class BasketLegIn(BaseModel):
    """One leg of the composed-basket request body (the WS 2A wire shape)."""

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None


class BasketIn(BaseModel):
    """The composed-basket request body. Field semantics live on :class:`Basket`.

    Only the *shape* is validated here (missing/mistyped fields become a labelled 400
    naming the field, instead of an opaque ``KeyError`` detail); the domain rules —
    side/sign agreement, finite quantities, option legs naming their grid cell — stay
    on the :class:`BasketLeg`/:class:`Basket` contracts, the single home for them.
    ``trade_date`` stays a raw string: empty/missing means "the latest banked day".
    """

    basket_id: str
    underlying: str
    trade_date: str | None = ""
    legs: list[BasketLegIn] = []
    provider: str | None = None


def _resolve_trade_date(ctx: AppContext, parsed: BasketIn) -> date:
    """The basket's trade date: an explicit ISO date, or the latest banked analytics day.

    The web client sends ``trade_date: ""`` until the operator picks a date, meaning "the latest
    day with banked analytics for this underlying" — the same latest-with-data default the
    health/coverage routers apply. An empty date over an underlying with no banked grid is a
    ``ValueError`` the caller turns into a labelled 400 (there is no day to price against).
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


def _build_basket(ctx: AppContext, parsed: BasketIn) -> Basket:
    """Build the typed :class:`Basket` from the validated body, raising on anything malformed.

    A bad trade date is a ``ValueError``; a leg violating its contract (a side/sign
    contradiction, a non-finite quantity) is a ``ContractValidationError`` — both become a
    labelled 400, never a silent default.
    """
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
        provider=parsed.provider,
    )


def _stock_spots(ctx: AppContext, basket: Basket) -> dict[str, float]:
    """Stock-leg spots: each named underlying's ``daily_bar`` close on the trade date.

    Only read when a stock leg is present, and only for the underlyings those legs name
    (the read-only source the price-history router uses).
    """
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
    return spot_by_underlying


@dataclass(frozen=True, slots=True)
class _BasketInputs:
    """Everything both POST handlers need: the typed basket plus its store reads."""

    basket: Basket
    analytics_rows: list[ProjectedOptionAnalytics]
    spot_by_underlying: dict[str, float]


async def _basket_inputs(ctx: AppContext, request: Request) -> _BasketInputs:
    """Parse, validate, and read back the inputs both basket endpoints share.

    Malformed JSON / shape / dates raise :class:`BadRequestError` with the labelled
    ``bad_basket`` payload; a leg violating its contract raises
    ``ContractValidationError``, which the app-level handler emits with the same shape.
    """
    body = await parse_json_body(request, error="bad_basket")
    try:
        basket = _build_basket(ctx, BasketIn.model_validate(body))
    except (ValidationError, ValueError) as exc:
        raise BadRequestError({"error": "bad_basket", "detail": str(exc)}) from exc
    # Read-only: the WS-1F grid for this day/underlying, never bleeding in another name's
    # grid (the version-blind belt lives in read_for_underlying).
    analytics_rows = read_for_underlying(
        ctx.store,
        "projected_option_analytics",
        basket.underlying,
        trade_date=basket.trade_date,
        provider=basket.provider,
    )
    return _BasketInputs(
        basket=basket,
        analytics_rows=analytics_rows,
        spot_by_underlying=_stock_spots(ctx, basket),
    )


@router.post("/risk")
async def price_basket(ctx: CtxDep, request: Request) -> JSONResponse:
    """Price and risk a composed basket off the stored Tab-1 analytics (read-only).

    Reads the WS-1F analytics grid for the basket's ``(trade_date, underlying[, provider])`` and
    the stock-leg spots from ``daily_bar`` on the same date, then sums the per-leg dollar Greeks.
    Returns the priced/risked basket with each dollar number carrying its unit string and the
    per-leg breakdown + labelled gaps (HTTP 200). A malformed basket is a labelled 400.
    """
    inputs = await _basket_inputs(ctx, request)
    result = basket_risk(
        inputs.basket,
        analytics_rows=inputs.analytics_rows,
        spot_by_underlying=inputs.spot_by_underlying,
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
async def stress_basket(ctx: CtxDep, request: Request) -> JSONResponse:
    """Full-reprice a composed basket over the cartesian (spot x vol) stress grid (WS 2B).

    The interactive, no-cron counterpart to ``GET /api/risk/scenarios`` (which reads the cron's
    persisted surface): reconstructs a valuation per option leg from the stored WS-1F analytics
    grid (:mod:`..basket_scenarios`), reprices the basket over the config-driven surface, and
    returns the same surface payload shape the ``StressSurface`` web component renders plus the
    worst-case cell and labelled per-leg gaps. A malformed basket is a labelled 400; an
    unresolved leg is a labelled gap inside a 200, never a 500.
    """
    inputs = await _basket_inputs(ctx, request)
    multiplier, currency = _option_multiplier_currency(ctx, inputs.basket)
    config = load_platform_config(ctx.configs_dir).scenario
    result = basket_stress(
        inputs.basket,
        analytics_rows=inputs.analytics_rows,
        multiplier=multiplier,
        currency=currency,
        spot_by_underlying=inputs.spot_by_underlying,
        config=config,
    )
    return JSONResponse(basket_scenarios_to_dict(result))
