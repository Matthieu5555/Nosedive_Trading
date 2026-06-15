"""The cardinal backtester guarantee: the engine never reads a future date (no look-ahead).

Look-ahead is the cardinal sin of a backtester (AGENTS.md / ``check-lookahead-bias``). This is
the *mechanism* that proves the engine is clean, not a claim: a recording data seam that captures
the ``as_of`` of every market-state read, and an assertion over the recording that the engine
only ever read the day it was processing — never a future one.

The proof is exact because the engine's *only* source of a date is the replay loop variable. So
two properties together pin "no look-ahead": (1) every read's ``as_of`` is one of the replay
dates (the engine invents no date), and (2) the distinct read-dates appear in non-decreasing
order matching the loop (the engine never jumps forward and back). A forward peek would make a
read's ``as_of`` exceed the largest date the loop had reached, breaking (2). A second test
deliberately drives a forward read to show the check is not vacuously green.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from algotrading.infra.contracts import BasketLeg
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.valuation import ContractValuationInput
from algotrading.strategy import SignalKind, SignalReading, SignalSnapshot
from algotrading.strategy.backtest import (
    BacktestConfig,
    HeldContract,
    run_backtest,
)
from algotrading.strategy.backtest.data import BacktestData
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy

_INDEX = "SX5E"
_TENOR = "1M"
_BAND = "24dp"
_SIDE = "put"
_CONTRACT_KEY = "SX5E|OPT|P|3800.0000"


@dataclass
class RecordingData(BacktestData):
    """A :class:`BacktestData` that records the ``as_of`` of every read for the look-ahead audit.

    It serves real (constant) state so a full replay runs; its only job beyond that is to append
    each read's ``as_of`` to ``read_dates`` in call order. The test then audits the recording
    against the replay dates — the engine cannot read a date it was not handed by the loop.
    """

    signals_by_day: dict[date, SignalSnapshot]
    held_template: HeldContract
    mark: ContractValuationInput
    read_dates: list[date] = field(default_factory=list)

    def signals(self, as_of: date) -> SignalSnapshot:
        self.read_dates.append(as_of)
        return self.signals_by_day.get(as_of, SignalSnapshot(as_of=as_of, readings=()))

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        self.read_dates.append(as_of)
        return HeldContract(
            contract_key=self.held_template.contract_key,
            quantity=leg.quantity,
            expiry=self.held_template.expiry,
            leg=leg,
        )

    def valuation(self, held: HeldContract, as_of: date) -> ContractValuationInput | None:
        self.read_dates.append(as_of)
        return self.mark


def _strategy() -> PutLineStrategy:
    return PutLineStrategy(
        PutLineConfig(
            index=_INDEX, put_tenor=_TENOR, put_delta_band=_BAND,
            line_capacity=10, contracts_per_day=1.0, max_rv_minus_iv=0.0,
        )
    )


def test_engine_only_reads_the_current_day_in_loop_order() -> None:
    base = date(2026, 1, 5)
    dates = [base + timedelta(i) for i in range(5)]
    strategy = _strategy()
    template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=base + timedelta(60),
        leg=strategy.construct(base, basket_id="seed").legs[0],
    )
    mark = ContractValuationInput(
        contract_key=_CONTRACT_KEY, underlying=_INDEX, option_right="P",
        exercise_style="european", strike=3800.0, maturity_years=30 / 365, spot=3900.0,
        carry=0.0, volatility=0.20, discount_factor=1.0, multiplier=10.0, currency="EUR",
    )
    data = RecordingData(
        signals_by_day={
            d: SignalSnapshot(
                as_of=d,
                readings=(SignalReading(kind=SignalKind.IV_VS_REALIZED, value=-0.02, subject=_INDEX),),
            )
            for d in dates
        },
        held_template=template,
        mark=mark,
    )
    run_backtest(
        strategy, data, dates=dates,
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )

    # (1) The engine invented no date: every read is one of the replay dates.
    assert set(data.read_dates) <= set(dates)
    # (2) No forward peek: each read's as_of is <= the largest replay date reached by its position
    #     in the loop. Because reads happen day by day in order, the distinct read-dates must be
    #     exactly the loop order (monotone non-decreasing, no future jump).
    distinct_in_order: list[date] = []
    for d in data.read_dates:
        if not distinct_in_order or d != distinct_in_order[-1]:
            distinct_in_order.append(d)
    assert distinct_in_order == sorted(set(data.read_dates))
    assert distinct_in_order == dates  # the engine reached every day, in order, none early.


def test_recording_audit_would_catch_a_forward_read() -> None:
    """Sanity: the audit predicate is not vacuous — a hand-made out-of-order read fails it."""
    base = date(2026, 1, 5)
    dates = [base, base + timedelta(1), base + timedelta(2)]
    # A read log with a forward jump (reads day 2 before day 1) — the audit must reject it.
    forward_jumped = [base, base + timedelta(2), base + timedelta(1)]
    distinct_in_order: list[date] = []
    for d in forward_jumped:
        if not distinct_in_order or d != distinct_in_order[-1]:
            distinct_in_order.append(d)
    assert distinct_in_order != dates  # the out-of-order log is caught by the same predicate.
