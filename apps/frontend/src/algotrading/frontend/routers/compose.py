"""BFF compose / book router (2D step 5).

The operator composes a named *book* from an ordered set of sub-strategies (each a
2A basket). This router is a thin serializer over the landed, tested infra:
it resolves each layer's legs to ``PositionRisk`` lines with the *same*
``reconstruct_valuation`` path the basket-stress seam already uses, then calls the
pure ``build_book_greeks`` / ``book_stress_surface`` functions and serializes their
output. No risk is recomputed here, no aggregation is forked, and placing a
sub-strategy in a book never re-solves it (the book is a view, not a mutation).

The diversification ratio is surfaced as a *read-only* diagnostic over the per-layer
net vegas (the operator's "decorrelated" intent shown back to them); it never feeds
the Greeks or the PnL surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from algotrading.core.config import MonetizationConfig, ScenarioConfig
from algotrading.core.config.loader import load_platform_config
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import (
    Basket,
    BasketLeg,
    BookGreeks,
    ContractValidationError,
    ProjectedOptionAnalytics,
)
from algotrading.infra.risk import BookLayerInput, PositionRisk, position_risk
from algotrading.infra.risk.basket import basket_variance
from algotrading.infra.risk.book import (
    COMPOSITION_VERSION,
    book_stress_surface,
    build_book_greeks,
)
from algotrading.infra.risk.grid_versioning import short_construction_hash
from algotrading.infra.risk.multileg import (
    analytics_cell_key,
    index_rows_by_cell_and_side,
    resolve_cell_side,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..basket_scenarios import reconstruct_valuation
from ..context import AppContext
from ..deps import BadRequestError, CtxDep, parse_json_body
from ..store_reads import latest_partition_date, read_for_underlying

router = APIRouter(prefix="/api/compose", tags=["compose"])

_PORTFOLIO_ID = "book-compose"
_BOOK_LEVEL = "book"
_LAYER_LEVEL = "layer"
_CODE_VERSION = "compose-router-1.0.0"


class _LegIn(BaseModel):

    instrument_kind: str
    side: str
    quantity: float
    underlying: str
    tenor_label: str | None = None
    delta_band: str | None = None


class _LayerIn(BaseModel):

    label: str
    basket_id: str
    underlying: str
    legs: list[_LegIn] = []
    provider: str | None = None


class _ComposeIn(BaseModel):

    book_id: str
    trade_date: str | None = ""
    layers: list[_LayerIn] = []


@dataclass(frozen=True, slots=True)
class _ResolvedLayer:

    label: str
    basket: Basket
    lines: tuple[PositionRisk, ...]
    n_legs: int
    n_resolved: int


def _resolve_trade_date(ctx: AppContext, parsed: _ComposeIn) -> date:
    if parsed.trade_date:
        return date.fromisoformat(parsed.trade_date)
    partitions = ctx.store.list_partitions("projected_option_analytics")
    latest = latest_partition_date(partitions)
    if latest is None:
        raise ValueError("trade_date is empty and no analytics are banked")
    return latest


def _basket_of(layer: _LayerIn, trade_date: date) -> Basket:
    legs = tuple(
        BasketLeg(
            instrument_kind=leg.instrument_kind,
            side=leg.side,
            quantity=leg.quantity,
            underlying=leg.underlying,
            tenor_label=leg.tenor_label,
            delta_band=leg.delta_band,
        )
        for leg in layer.legs
    )
    return Basket(
        basket_id=layer.basket_id,
        trade_date=trade_date,
        underlying=layer.underlying,
        legs=legs,
        provider=layer.provider,
    )


def _option_multiplier_currency(
    ctx: AppContext, basket: Basket
) -> tuple[float | None, str | None]:
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


def _resolve_lines(
    ctx: AppContext, basket: Basket
) -> tuple[tuple[PositionRisk, ...], int]:
    analytics_rows: list[ProjectedOptionAnalytics] = read_for_underlying(
        ctx.store,
        "projected_option_analytics",
        basket.underlying,
        trade_date=basket.trade_date,
        provider=basket.provider,
    )
    by_cell_side, ambiguous = index_rows_by_cell_and_side(analytics_rows)
    multiplier, currency = _option_multiplier_currency(ctx, basket)
    lines: list[PositionRisk] = []
    for leg in basket.legs:
        if leg.instrument_kind != "option" or multiplier is None or currency is None:
            continue
        key = analytics_cell_key(leg.underlying, leg.tenor_label, leg.delta_band)
        row, _reason = resolve_cell_side(
            by_cell_side, ambiguous, key=key, surface_side=leg.surface_side
        )
        if row is None:
            continue
        valuation = reconstruct_valuation(row, multiplier=multiplier, currency=currency)
        lines.append(
            position_risk(
                portfolio_id=_PORTFOLIO_ID, quantity=leg.quantity, valuation=valuation
            )
        )
    return tuple(lines), len(lines)


def _resolved_layers(ctx: AppContext, parsed: _ComposeIn) -> list[_ResolvedLayer]:
    trade_date = _resolve_trade_date(ctx, parsed)
    resolved: list[_ResolvedLayer] = []
    for layer in parsed.layers:
        basket = _basket_of(layer, trade_date)
        lines, n_resolved = _resolve_lines(ctx, basket)
        resolved.append(
            _ResolvedLayer(
                label=layer.label,
                basket=basket,
                lines=lines,
                n_legs=len(basket.legs),
                n_resolved=n_resolved,
            )
        )
    return resolved


def _composition_hashes(
    parsed: _ComposeIn, *, config: ScenarioConfig, monetization: MonetizationConfig
) -> dict[str, str]:
    # Economic selection only: layer labels + ordered leg identities. A comment-only or
    # display-only edit that does not change the selection leaves these byte-identical;
    # changing the layer set or the grid moves exactly its own bundle's hash. Built with
    # ``short_construction_hash`` (sorted-key canonical JSON, sha256) so it is stable across
    # separate processes without relying on PYTHONHASHSEED.
    layer_payload = [
        {
            "label": layer.label,
            "legs": [
                {
                    "instrument_kind": leg.instrument_kind,
                    "side": leg.side,
                    "quantity": leg.quantity,
                    "underlying": leg.underlying,
                    "tenor_label": leg.tenor_label,
                    "delta_band": leg.delta_band,
                }
                for leg in layer.legs
            ],
        }
        for layer in parsed.layers
    ]
    grid_payload = {
        "version": config.stress_surface.version,
        "spot_shock_abs": config.stress_surface.spot_shock_abs,
        "vol_shock_abs": config.stress_surface.vol_shock_abs,
        "spot_steps": config.stress_surface.spot_steps,
        "vol_steps": config.stress_surface.vol_steps,
    }
    monetization_payload = {
        "gamma_normalisation": monetization.gamma_normalisation,
        "theta_day_count": monetization.theta_day_count,
    }
    return {
        "layer_set": short_construction_hash({"layers": layer_payload}),
        "grid": short_construction_hash(grid_payload),
        "monetization": short_construction_hash(monetization_payload),
    }


def _stamp(config_hashes: dict[str, str], *, valuation_ts: datetime) -> ProvenanceStamp:
    return stamp(
        calc_ts=valuation_ts,
        code_version=_CODE_VERSION,
        config_hashes=config_hashes,
        source_records=(source_ref("projected_option_analytics", _PORTFOLIO_ID),),
        source_timestamps=(valuation_ts,),
    )


def _greeks_to_dict(row: BookGreeks) -> dict[str, object]:
    return {
        "level": row.level,
        "layer_label": row.layer_label,
        "layer_index": row.layer_index,
        "net_delta": row.net_delta,
        "net_gamma": row.net_gamma,
        "net_vega": row.net_vega,
        "net_theta": row.net_theta,
        "dollar_delta": {"value": row.dollar_delta, "unit": row.dollar_delta_unit},
        "dollar_gamma": {"value": row.dollar_gamma, "unit": row.dollar_gamma_unit},
        "dollar_vega": {"value": row.dollar_vega, "unit": row.dollar_vega_unit},
        "dollar_theta": {"value": row.dollar_theta, "unit": row.dollar_theta_unit},
        "dollar_rho": {"value": row.dollar_rho, "unit": row.dollar_rho_unit},
    }


def _diversification_ratio(layer_rows: list[BookGreeks]) -> float | None:
    # Read-only diagnostic: the realised diversification of the operator's selection, shown
    # over the per-layer net vegas under their decorrelated *intent* (avg_correlation=0). It
    # is reported, never fed back into the Greeks or the PnL surface — removing it changes no
    # aggregate (see test_diversification_diagnostic_is_read_only).
    vegas = [row.net_vega for row in layer_rows]
    if len(vegas) < 2 or all(v == 0.0 for v in vegas):
        return None
    weights = [1.0] * len(vegas)
    return basket_variance(weights, vegas, avg_correlation=0.0).diversification_ratio


@router.get("/sub-strategies")
def list_sub_strategies(ctx: CtxDep) -> JSONResponse:
    partitions = ctx.store.list_partitions("projected_option_analytics")
    underlyings = sorted({underlying for _date, underlying in partitions})
    return JSONResponse(
        {"n_sub_strategies": len(underlyings), "sub_strategies": underlyings}
    )


@router.post("")
async def compose_book(ctx: CtxDep, request: Request) -> JSONResponse:
    body = await parse_json_body(request, error="bad_composition")
    try:
        parsed = _ComposeIn.model_validate(body)
    except ValidationError as exc:
        raise BadRequestError({"error": "bad_composition", "detail": str(exc)}) from exc
    try:
        layers = _resolved_layers(ctx, parsed)
    except (ValueError, ContractValidationError) as exc:
        raise BadRequestError({"error": "bad_composition", "detail": str(exc)}) from exc

    platform = load_platform_config(ctx.configs_dir)
    config = platform.scenario
    monetization = platform.monetization
    valuation_ts = datetime.now(UTC)

    config_hashes = _composition_hashes(parsed, config=config, monetization=monetization)
    provenance = _stamp(config_hashes, valuation_ts=valuation_ts)

    book_inputs = [
        BookLayerInput(label=layer.label, lines=layer.lines) for layer in layers
    ]
    rows = build_book_greeks(
        book_id=parsed.book_id,
        layers=book_inputs,
        monetization=monetization,
        valuation_ts=valuation_ts,
        source_snapshot_ts=valuation_ts,
        provenance=provenance,
    )
    surface = book_stress_surface(book_inputs, config=config)

    combined = next(row for row in rows if row.level == _BOOK_LEVEL)
    layer_rows = [row for row in rows if row.level == _LAYER_LEVEL]

    return JSONResponse(
        {
            "book_id": parsed.book_id,
            "valuation_ts": valuation_ts.isoformat(),
            "composition_version": COMPOSITION_VERSION,
            "config_hashes": config_hashes,
            "combined": _greeks_to_dict(combined),
            "layers": [
                {
                    **_greeks_to_dict(row),
                    "n_legs": layer.n_legs,
                    "n_resolved": layer.n_resolved,
                }
                for row, layer in zip(layer_rows, layers, strict=True)
            ],
            "diversification_ratio": _diversification_ratio(layer_rows),
            "surface": {
                "scenario_version": surface.scenario_version,
                "spot_axis": list(surface.spot_axis),
                "vol_axis": list(surface.vol_axis),
                "pnl_grid": [list(row) for row in surface.pnl_grid],
            },
        }
    )
