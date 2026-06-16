from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import Basket
from algotrading.infra.risk.attribution import (
    RealizedBookAttribution,
    attribute_realized_book,
)
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.scenarios import Scenario, scenario_line_pnls, worst_case

from ..harness import StrategyContext, run_strategy
from ..s2_put_line import PutLineStrategy
from ..signals import SignalSnapshot
from ..strategy import EntryAction, MarketState, Strategy
from .book import BacktestBook, PricedBook
from .data import BacktestData, HeldContract
from .results import (
    BacktestResult,
    DayGreeks,
    DayResult,
    summarise,
)


@dataclass(frozen=True, slots=True)
class BacktestConfig:

    basket_id_prefix: str
    attribution: AttributionConfig
    stress_grid: tuple[Scenario, ...]


def _book_greeks(priced: PricedBook) -> DayGreeks:
    return DayGreeks(
        delta=sum(line.position_delta for line in priced.lines),
        gamma=sum(line.position_gamma for line in priced.lines),
        vega=sum(line.position_vega for line in priced.lines),
        theta=sum(line.position_theta for line in priced.lines),
    )


def _stress_loss(priced: PricedBook, grid: tuple[Scenario, ...]) -> float:
    if not priced.lines or not grid:
        return 0.0
    cells = scenario_line_pnls(priced.lines, grid)
    return min(0.0, worst_case(cells).total_pnl)


def _attribute_day(
    prior: PricedBook | None,
    today: PricedBook,
    config: AttributionConfig,
) -> RealizedBookAttribution | None:
    if prior is None or not prior.lines:
        return None
    starts = [line for line in prior.lines if line.contract_key in today.valuations]
    if not starts:
        return None
    ends = {key: today.valuations[key] for key in (line.contract_key for line in starts)}
    return attribute_realized_book(starts, ends, config)


def run_backtest(
    strategy: Strategy,
    data: BacktestData,
    *,
    dates: Sequence[date],
    config: BacktestConfig,
) -> BacktestResult:
    book = BacktestBook()
    prior_priced: PricedBook | None = None
    cumulative_pnl = 0.0
    day_results: list[DayResult] = []

    for as_of in dates:
        book.expire(as_of)

        priced = book.price(data, as_of)
        attribution = _attribute_day(prior_priced, priced, config.attribution)
        realized_pnl = attribution.full_reprice_pnl if attribution is not None else None
        if realized_pnl is not None:
            cumulative_pnl += realized_pnl

        entered = _run_day(strategy, data, book, as_of, config)

        closed = book.price(data, as_of)

        day_results.append(
            DayResult(
                as_of=as_of,
                open_contracts=book.open_contract_count,
                entered=entered,
                realized_pnl=realized_pnl,
                cumulative_pnl=cumulative_pnl,
                greeks=_book_greeks(closed),
                attribution=attribution,
                stress_loss=_stress_loss(closed, config.stress_grid),
            )
        )
        prior_priced = closed

    return BacktestResult(
        strategy_id=strategy.contract.strategy_id,
        days=tuple(day_results),
        summary=summarise(day_results),
    )


def _run_day(
    strategy: Strategy,
    data: BacktestData,
    book: BacktestBook,
    as_of: date,
    config: BacktestConfig,
) -> bool:
    signals = data.signals(as_of)
    market = MarketState(as_of=as_of, position_lines=book.price(data, as_of).lines)

    run_strategy(
        strategy,
        context=StrategyContext.BACKTEST,
        as_of=as_of,
        signals=signals,
        market=market,
        basket_id=f"{config.basket_id_prefix}-{as_of.isoformat()}",
    )

    if not _daily_entry_fires(strategy, book.open_contract_count, signals, as_of):
        return False

    basket = strategy.construct(as_of, basket_id=f"{config.basket_id_prefix}-{as_of.isoformat()}")
    opened = _concretize_basket(data, basket, as_of)
    book.add(opened)
    return bool(opened)


def _daily_entry_fires(
    strategy: Strategy,
    open_contracts: float,
    signals: SignalSnapshot,
    as_of: date,
) -> bool:
    if isinstance(strategy, PutLineStrategy):
        decision = strategy.decide_sell(as_of, signals, open_contracts=open_contracts)
    else:
        decision = strategy.decide_entry(as_of, signals)
    return decision.action is EntryAction.ENTER


def _concretize_basket(
    data: BacktestData, basket: Basket, as_of: date
) -> list[HeldContract]:
    opened: list[HeldContract] = []
    for leg in basket.legs:
        held = data.concretize_leg(leg, as_of)
        if held is not None:
            opened.append(held)
    return opened
