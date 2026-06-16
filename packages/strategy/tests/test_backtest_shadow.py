from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from algotrading.infra.contracts import BasketLeg
from algotrading.strategy import SignalKind, SignalReading, SignalSnapshot
from algotrading.strategy.backtest import (
    BookedFill,
    HeldContract,
    reconcile_shadow,
)
from algotrading.strategy.backtest.data import BacktestData
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy

_INDEX = "SX5E"
_TENOR = "1M"
_BAND = "24dp"
_CONTRACT_KEY = "SX5E|OPT|P|3800.0000"


@dataclass
class StubData(BacktestData):

    rich_days: frozenset[date]
    read_dates: list[date] = field(default_factory=list)

    def signals(self, as_of: date) -> SignalSnapshot:
        self.read_dates.append(as_of)
        value = -0.02 if as_of in self.rich_days else 0.05
        return SignalSnapshot(
            as_of=as_of,
            readings=(
                SignalReading(kind=SignalKind.IV_VS_REALIZED, value=value, subject=_INDEX),
            ),
        )

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        self.read_dates.append(as_of)
        return HeldContract(
            contract_key=_CONTRACT_KEY,
            quantity=leg.quantity,
            expiry=as_of + timedelta(days=30),
            leg=leg,
        )

    def valuation(self, held: HeldContract, as_of: date):  # type: ignore[no-untyped-def]
        return None


def _strategy(capacity: int = 10) -> PutLineStrategy:
    return PutLineStrategy(
        PutLineConfig(
            index=_INDEX, put_tenor=_TENOR, put_delta_band=_BAND,
            line_capacity=capacity, contracts_per_day=1.0, max_rv_minus_iv=0.0,
        )
    )


def test_shadow_reconciles_when_booked_matches_intended() -> None:
    base = date(2026, 1, 5)
    dates = [base, base + timedelta(1)]
    data = StubData(rich_days=frozenset(dates))
    booked = [
        BookedFill(trade_date=d, contract_key=_CONTRACT_KEY, signed_qty=-1.0)
        for d in dates
    ]
    report = reconcile_shadow(
        _strategy(), data, booked, dates=dates, basket_id_prefix="shadow"
    )
    assert report.reconciled is True
    assert report.drift_days == ()


def test_shadow_flags_a_missing_booking_as_drift() -> None:
    base = date(2026, 1, 5)
    dates = [base, base + timedelta(1)]
    data = StubData(rich_days=frozenset(dates))
    booked = [BookedFill(trade_date=base, contract_key=_CONTRACT_KEY, signed_qty=-1.0)]
    report = reconcile_shadow(
        _strategy(), data, booked, dates=dates, basket_id_prefix="shadow"
    )
    assert report.reconciled is False
    drift = report.drift_days
    assert len(drift) == 1
    assert drift[0].as_of == base + timedelta(1)
    assert drift[0].drift  # the second day intended a sell that was never booked


def test_shadow_flags_a_quantity_mismatch_as_drift() -> None:
    base = date(2026, 1, 5)
    dates = [base]
    data = StubData(rich_days=frozenset(dates))
    booked = [BookedFill(trade_date=base, contract_key=_CONTRACT_KEY, signed_qty=-2.0)]
    report = reconcile_shadow(
        _strategy(), data, booked, dates=dates, basket_id_prefix="shadow"
    )
    assert report.reconciled is False
    assert "intended -1" in report.days[0].drift[0]


def test_shadow_honours_capacity_via_the_booked_line() -> None:
    base = date(2026, 1, 5)
    dates = [base + timedelta(i) for i in range(3)]
    data = StubData(rich_days=frozenset(dates))
    booked = [
        BookedFill(trade_date=dates[0], contract_key=_CONTRACT_KEY, signed_qty=-1.0),
        BookedFill(trade_date=dates[1], contract_key=_CONTRACT_KEY, signed_qty=-1.0),
    ]
    report = reconcile_shadow(
        _strategy(capacity=2), data, booked, dates=dates, basket_id_prefix="shadow"
    )
    assert report.reconciled is True
    assert report.days[2].intended == ()
    assert report.days[2].booked == ()


def test_shadow_reads_only_the_current_day_in_loop_order() -> None:
    base = date(2026, 1, 5)
    dates = [base + timedelta(i) for i in range(4)]
    data = StubData(rich_days=frozenset(dates))
    reconcile_shadow(_strategy(), data, [], dates=dates, basket_id_prefix="shadow")
    assert set(data.read_dates) <= set(dates)
    distinct: list[date] = []
    for d in data.read_dates:
        if not distinct or d != distinct[-1]:
            distinct.append(d)
    assert distinct == dates
