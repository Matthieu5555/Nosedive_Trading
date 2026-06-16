from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..harness import StrategyContext, run_strategy
from ..strategy import MarketState, Strategy
from .data import BacktestData
from .engine import daily_entry_fires

_QTY_ABS_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class BookedFill:

    trade_date: date
    contract_key: str
    signed_qty: float


@dataclass(frozen=True, slots=True)
class ShadowLeg:

    contract_key: str
    signed_qty: float


@dataclass(frozen=True, slots=True)
class ShadowDay:

    as_of: date
    intended: tuple[ShadowLeg, ...]
    booked: tuple[ShadowLeg, ...]
    matched: bool
    drift: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShadowReport:

    strategy_id: str
    days: tuple[ShadowDay, ...]

    @property
    def reconciled(self) -> bool:
        return all(day.matched for day in self.days)

    @property
    def drift_days(self) -> tuple[ShadowDay, ...]:
        return tuple(day for day in self.days if not day.matched)


def _qty_match(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=_QTY_ABS_TOL)


def _net_by_contract(legs: Sequence[ShadowLeg]) -> dict[str, float]:
    net: dict[str, float] = {}
    for leg in legs:
        net[leg.contract_key] = net.get(leg.contract_key, 0.0) + leg.signed_qty
    return net


def _diff(intended: Sequence[ShadowLeg], booked: Sequence[ShadowLeg]) -> tuple[str, ...]:
    intended_net = _net_by_contract(intended)
    booked_net = _net_by_contract(booked)
    keys = sorted(set(intended_net) | set(booked_net))
    drift: list[str] = []
    for key in keys:
        want = intended_net.get(key, 0.0)
        got = booked_net.get(key, 0.0)
        if not _qty_match(want, got):
            drift.append(f"{key}: intended {want:g}, booked {got:g}")
    return tuple(drift)


def reconcile_shadow(
    strategy: Strategy,
    data: BacktestData,
    booked_fills: Sequence[BookedFill],
    *,
    dates: Sequence[date],
    basket_id_prefix: str,
) -> ShadowReport:
    booked_by_day: dict[date, list[ShadowLeg]] = {}
    for fill in booked_fills:
        booked_by_day.setdefault(fill.trade_date, []).append(
            ShadowLeg(contract_key=fill.contract_key, signed_qty=fill.signed_qty)
        )

    days: list[ShadowDay] = []
    open_contracts = 0.0
    for as_of in dates:
        signals = data.signals(as_of)
        market = MarketState(as_of=as_of, position_lines=())
        step = run_strategy(
            strategy,
            context=StrategyContext.BACKTEST,
            as_of=as_of,
            signals=signals,
            market=market,
            basket_id=f"{basket_id_prefix}-{as_of.isoformat()}",
        )

        intended: list[ShadowLeg] = []
        if step.basket is not None and daily_entry_fires(
            strategy, open_contracts, signals, as_of
        ):
            for leg in step.basket.legs:
                held = data.concretize_leg(leg, as_of)
                if held is not None:
                    intended.append(
                        ShadowLeg(
                            contract_key=held.contract_key, signed_qty=leg.quantity
                        )
                    )

        booked = booked_by_day.get(as_of, [])
        drift = _diff(intended, booked)
        days.append(
            ShadowDay(
                as_of=as_of,
                intended=tuple(intended),
                booked=tuple(booked),
                matched=not drift,
                drift=drift,
            )
        )
        open_contracts += float(len(booked))

    return ShadowReport(strategy_id=strategy.contract.strategy_id, days=tuple(days))
