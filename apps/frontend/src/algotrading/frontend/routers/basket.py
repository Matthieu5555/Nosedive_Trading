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

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None


class BasketIn(BaseModel):

    basket_id: str
    underlying: str
    trade_date: str | None = ""
    legs: list[BasketLegIn] = []
    provider: str | None = None


def _resolve_trade_date(ctx: AppContext, parsed: BasketIn) -> date:
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

    basket: Basket
    analytics_rows: list[ProjectedOptionAnalytics]
    spot_by_underlying: dict[str, float]


async def _basket_inputs(ctx: AppContext, request: Request) -> _BasketInputs:
    body = await parse_json_body(request, error="bad_basket")
    try:
        basket = _build_basket(ctx, BasketIn.model_validate(body))
    except (ValidationError, ValueError) as exc:
        raise BadRequestError({"error": "bad_basket", "detail": str(exc)}) from exc
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
    inputs = await _basket_inputs(ctx, request)
    result = basket_risk(
        inputs.basket,
        analytics_rows=inputs.analytics_rows,
        spot_by_underlying=inputs.spot_by_underlying,
    )
    return JSONResponse(basket_risk_to_dict(result))


def _option_multiplier_currency(ctx: AppContext, basket: Basket) -> tuple[float | None, str | None]:
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
