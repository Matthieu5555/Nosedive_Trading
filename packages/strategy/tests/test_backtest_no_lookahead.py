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

    assert set(data.read_dates) <= set(dates)
    distinct_in_order: list[date] = []
    for d in data.read_dates:
        if not distinct_in_order or d != distinct_in_order[-1]:
            distinct_in_order.append(d)
    assert distinct_in_order == sorted(set(data.read_dates))
    assert distinct_in_order == dates


def test_recording_audit_would_catch_a_forward_read() -> None:
    base = date(2026, 1, 5)
    dates = [base, base + timedelta(1), base + timedelta(2)]
    forward_jumped = [base, base + timedelta(2), base + timedelta(1)]
    distinct_in_order: list[date] = []
    for d in forward_jumped:
        if not distinct_in_order or d != distinct_in_order[-1]:
            distinct_in_order.append(d)
    assert distinct_in_order != dates
