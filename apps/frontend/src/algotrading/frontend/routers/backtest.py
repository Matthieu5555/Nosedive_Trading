from __future__ import annotations

from datetime import date

from algotrading.infra.risk.attribution import RealizedBookAttribution
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.scenarios import Scenario, TaylorTerms
from algotrading.strategy.backtest import (
    BacktestConfig,
    BacktestResult,
    StoreBackedBacktestData,
    TransactionCostModel,
    run_backtest,
)
from algotrading.strategy.backtest.results import DayGreeks
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy
from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from ..context import AppContext
from ..deps import BadRequestError, CtxDep

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_ANALYTICS_TABLE = "projected_option_analytics"
_INSTRUMENT_TABLE = "instrument_master"
_DEFAULT_PROVIDER = "ibkr"
_ATTRIBUTION_VERSION = "backtest-bff-v1"


class PutLineParamsIn(BaseModel):

    put_tenor: str
    put_delta_band: str
    line_capacity: int
    contracts_per_day: float = 1.0
    max_rv_minus_iv: float = 0.0
    exit_delta_ceiling: float | None = None


class CostsIn(BaseModel):

    commission_per_contract: float = 0.0
    slippage_rate: float = 0.0


class StressScenarioIn(BaseModel):

    scenario_id: str
    spot_shock: float = 0.0
    vol_shock: float = 0.0
    time_shock: float = 0.0


class BacktestRunIn(BaseModel):

    index: str
    reference_tenor: str
    start_date: str
    end_date: str
    provider: str = _DEFAULT_PROVIDER
    put_line: PutLineParamsIn
    costs: CostsIn = CostsIn()
    stress_grid: list[StressScenarioIn] = []


def _resolve_dates(ctx: AppContext, parsed: BacktestRunIn) -> list[date]:
    try:
        start = date.fromisoformat(parsed.start_date)
        end = date.fromisoformat(parsed.end_date)
    except ValueError as exc:
        raise BadRequestError(
            {"error": "bad_date", "detail": str(exc)}
        ) from exc
    if end < start:
        raise BadRequestError(
            {"error": "bad_window", "detail": "end_date precedes start_date"}
        )
    banked = sorted(
        part_date
        for part_date, part_underlying in ctx.store.list_partitions(_ANALYTICS_TABLE)
        if part_underlying == parsed.index and start <= part_date <= end
    )
    if not banked:
        raise BadRequestError(
            {
                "error": "no_banked_days",
                "detail": (
                    f"no {_ANALYTICS_TABLE} partitions for {parsed.index!r} in "
                    f"[{parsed.start_date}, {parsed.end_date}]"
                ),
            }
        )
    return banked


def _multiplier_currency(ctx: AppContext, index: str) -> tuple[float, str]:
    masters = ctx.store.read(_INSTRUMENT_TABLE, underlying=index)
    for master in masters:
        instrument = master.instrument
        if instrument.is_option() and instrument.underlying_symbol == index:
            return float(instrument.multiplier), instrument.currency
    for master in masters:
        instrument = master.instrument
        if instrument.underlying_symbol == index:
            return float(instrument.multiplier), instrument.currency
    raise BadRequestError(
        {"error": "no_instrument_master", "detail": f"no instrument master for {index!r}"}
    )


def _strategy(parsed: BacktestRunIn) -> PutLineStrategy:
    try:
        return PutLineStrategy(
            PutLineConfig(
                index=parsed.index,
                put_tenor=parsed.put_line.put_tenor,
                put_delta_band=parsed.put_line.put_delta_band,
                line_capacity=parsed.put_line.line_capacity,
                contracts_per_day=parsed.put_line.contracts_per_day,
                max_rv_minus_iv=parsed.put_line.max_rv_minus_iv,
                exit_delta_ceiling=parsed.put_line.exit_delta_ceiling,
            )
        )
    except ValueError as exc:
        raise BadRequestError(
            {"error": "bad_put_line_config", "detail": str(exc)}
        ) from exc


def _stress_grid(parsed: BacktestRunIn) -> tuple[Scenario, ...]:
    return tuple(
        Scenario(
            scenario_id=item.scenario_id,
            family="backtest_stress",
            spot_shock=item.spot_shock,
            vol_shock=item.vol_shock,
            time_shock=item.time_shock,
        )
        for item in parsed.stress_grid
    )


def _greeks_to_dict(greeks: DayGreeks) -> dict[str, float]:
    return {
        "delta": greeks.delta,
        "gamma": greeks.gamma,
        "vega": greeks.vega,
        "theta": greeks.theta,
    }


def _terms_to_dict(terms: TaylorTerms) -> dict[str, float]:
    return {
        "delta": terms.delta_pnl,
        "gamma": terms.gamma_pnl,
        "vega": terms.vega_pnl,
        "theta": terms.theta_pnl,
        "rho": terms.rho_pnl,
        "vanna": terms.vanna_pnl,
        "volga": terms.volga_pnl,
    }


def _attribution_to_dict(result: BacktestResult) -> dict[str, float]:
    return _terms_to_dict(result.cumulative_attribution())


def _day_attribution_to_dict(
    attribution: RealizedBookAttribution | None,
) -> dict[str, object] | None:
    """Per-day realized decomposition: the seven terms, the full reprice, the residual, verdict.

    The engine produces a full ``RealizedBookAttribution`` per day (terms + full_reprice +
    residual + tolerance verdict); the old serializer dropped all of it and surfaced only the
    cumulative terms, so the day-level reconciliation (does Taylor explain the realized move?)
    was invisible. ``None`` on days with no attributed book (e.g. before the first position).
    """
    if attribution is None:
        return None
    return {
        "terms": _terms_to_dict(attribution.terms),
        "approx_pnl": attribution.terms.total,
        "full_reprice_pnl": attribution.full_reprice_pnl,
        "residual": attribution.residual,
        "within_tolerance": attribution.within_tolerance,
        "diagnostic": attribution.diagnostic,
    }


def _result_to_dict(result: BacktestResult) -> dict[str, object]:
    summary = result.summary
    return {
        "strategy_id": result.strategy_id,
        "summary": {
            "total_pnl": summary.total_pnl,
            "total_net_pnl": summary.total_net_pnl,
            "total_transaction_cost": summary.total_transaction_cost,
            "max_drawdown": summary.max_drawdown,
            "sharpe": summary.sharpe,
            "turnover": summary.turnover,
            "worst_stress_loss": summary.worst_stress_loss,
        },
        "cumulative_attribution": _attribution_to_dict(result),
        "days": [
            {
                "as_of": day.as_of.isoformat(),
                "open_contracts": day.open_contracts,
                "entered": day.entered,
                "realized_pnl": day.realized_pnl,
                "cumulative_pnl": day.cumulative_pnl,
                "cumulative_net_pnl": day.cumulative_net_pnl,
                "transaction_cost": day.transaction_cost,
                "stress_loss": day.stress_loss,
                "greeks": _greeks_to_dict(day.greeks),
                "attribution": _day_attribution_to_dict(day.attribution),
            }
            for day in result.days
        ],
    }


@router.post("/run")
def run(ctx: CtxDep, body: BacktestRunIn) -> dict[str, object]:
    try:
        parsed = BacktestRunIn.model_validate(body)
    except ValidationError as exc:
        raise BadRequestError({"error": "bad_backtest_request", "detail": str(exc)}) from exc

    dates = _resolve_dates(ctx, parsed)
    multiplier, currency = _multiplier_currency(ctx, parsed.index)
    strategy = _strategy(parsed)
    data = StoreBackedBacktestData(
        store=ctx.store,
        index=parsed.index,
        reference_tenor=parsed.reference_tenor,
        multiplier=multiplier,
        currency=currency,
        provider=parsed.provider,
    )
    config = BacktestConfig(
        basket_id_prefix=f"bt-{parsed.index}",
        attribution=AttributionConfig(version=_ATTRIBUTION_VERSION),
        stress_grid=_stress_grid(parsed),
        costs=TransactionCostModel(
            commission_per_contract=parsed.costs.commission_per_contract,
            slippage_rate=parsed.costs.slippage_rate,
        ),
    )
    result = run_backtest(strategy, data, dates=dates, config=config)
    return _result_to_dict(result)
