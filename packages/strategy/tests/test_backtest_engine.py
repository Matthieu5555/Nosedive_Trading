from __future__ import annotations

from datetime import date, timedelta

import pytest
from algotrading.infra.pricing import price
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.scenarios import Scenario
from algotrading.infra.risk.valuation import ContractValuationInput, pricing_state_for
from algotrading.strategy import SignalKind, SignalReading, SignalSnapshot
from algotrading.strategy.backtest import (
    BacktestConfig,
    ContractMarks,
    HeldContract,
    InMemoryBacktestData,
    run_backtest,
)
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy

_INDEX = "SX5E"
_TENOR = "1M"
_BAND = "24dp"
_SIDE = "put"
_MULTIPLIER = 10.0
_CONTRACT_KEY = "SX5E|OPT|put|3800.0000"


def _put_line(capacity: int) -> PutLineStrategy:
    return PutLineStrategy(
        PutLineConfig(
            index=_INDEX,
            put_tenor=_TENOR,
            put_delta_band=_BAND,
            line_capacity=capacity,
            contracts_per_day=1.0,
            max_rv_minus_iv=0.0,
        )
    )


def _rich_signal(as_of: date) -> SignalSnapshot:
    return SignalSnapshot(
        as_of=as_of,
        readings=(SignalReading(kind=SignalKind.IV_VS_REALIZED, value=-0.02, subject=_INDEX),),
    )


def _put_valuation(spot: float, maturity_years: float, vol: float) -> ContractValuationInput:
    return ContractValuationInput(
        contract_key=_CONTRACT_KEY,
        underlying=_INDEX,
        option_right="P",
        exercise_style="european",
        strike=3800.0,
        maturity_years=maturity_years,
        spot=spot,
        carry=0.0,
        volatility=vol,
        discount_factor=1.0,
        multiplier=_MULTIPLIER,
        currency="EUR",
    )


def _oracle_pnl(
    start: ContractValuationInput, end: ContractValuationInput, quantity: float
) -> float:
    start_price = price(pricing_state_for(start)).price
    end_price = price(pricing_state_for(end)).price
    return (end_price - start_price) * end.multiplier * quantity


def test_s2_sells_one_put_when_signal_rich_and_under_capacity() -> None:
    d1 = date(2026, 1, 5)
    held_template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 2, 5),
        leg=_put_line(3).construct(d1, basket_id="seed").legs[0],
    )
    data = InMemoryBacktestData(
        signals_by_day={d1: _rich_signal(d1)},
        concrete_by_cell={(_INDEX, _TENOR, _BAND, _SIDE): held_template},
        marks_by_contract={_CONTRACT_KEY: ContractMarks(by_day={d1: _put_valuation(3900.0, 30 / 365, 0.20)})},
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    assert result.summary.turnover == 1
    assert result.days[0].entered is True
    assert result.days[0].open_contracts == 1.0
    assert result.days[0].realized_pnl is None


def test_s2_daily_pnl_matches_independent_reprice_oracle() -> None:
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    start = _put_valuation(3900.0, 30 / 365, 0.20)
    end = _put_valuation(3700.0, 29 / 365, 0.28)
    held_template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 2, 5),
        leg=_put_line(3).construct(d1, basket_id="seed").legs[0],
    )
    data = InMemoryBacktestData(
        signals_by_day={d1: _rich_signal(d1)},
        concrete_by_cell={(_INDEX, _TENOR, _BAND, _SIDE): held_template},
        marks_by_contract={_CONTRACT_KEY: ContractMarks(by_day={d1: start, d2: end})},
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1, d2],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    expected = _oracle_pnl(start, end, quantity=-1.0)
    assert result.days[1].realized_pnl == pytest.approx(expected, rel=1e-9)
    assert result.days[1].cumulative_pnl == pytest.approx(expected, rel=1e-9)
    assert result.days[1].realized_pnl is not None and result.days[1].realized_pnl < 0.0
    assert result.summary.total_pnl == pytest.approx(expected, rel=1e-9)


def test_s2_attribution_decomposes_the_move_into_named_greeks() -> None:
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    start = _put_valuation(3900.0, 30 / 365, 0.20)
    end = _put_valuation(3700.0, 29 / 365, 0.28)
    held_template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 2, 5),
        leg=_put_line(3).construct(d1, basket_id="seed").legs[0],
    )
    data = InMemoryBacktestData(
        signals_by_day={d1: _rich_signal(d1)},
        concrete_by_cell={(_INDEX, _TENOR, _BAND, _SIDE): held_template},
        marks_by_contract={_CONTRACT_KEY: ContractMarks(by_day={d1: start, d2: end})},
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1, d2],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    attribution = result.days[1].attribution
    assert attribution is not None
    assert attribution.full_reprice_pnl == pytest.approx(_oracle_pnl(start, end, -1.0), rel=1e-9)
    cumulative = result.cumulative_attribution()
    assert cumulative.delta_pnl == pytest.approx(attribution.terms.delta_pnl, rel=1e-9)
    assert cumulative.vega_pnl == pytest.approx(attribution.terms.vega_pnl, rel=1e-9)
    assert attribution.terms.vega_pnl < 0.0


def test_s2_capacity_cap_stops_the_line_adding() -> None:
    base = date(2026, 1, 5)
    dates = [base + timedelta(i) for i in range(4)]
    concrete: dict[tuple[str, str | None, str | None, str], HeldContract] = {}
    marks: dict[str, ContractMarks] = {}
    leg = _put_line(2).construct(base, basket_id="seed").legs[0]
    template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 6, 5), leg=leg,
    )
    concrete[(_INDEX, _TENOR, _BAND, _SIDE)] = template
    marks[_CONTRACT_KEY] = ContractMarks(
        by_day={d: _put_valuation(3900.0, 30 / 365, 0.20) for d in dates}
    )
    data = InMemoryBacktestData(
        signals_by_day={d: _rich_signal(d) for d in dates},
        concrete_by_cell=concrete,
        marks_by_contract=marks,
    )
    result = run_backtest(
        _put_line(2), data, dates=dates,
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    assert result.summary.turnover == 2
    assert [day.entered for day in result.days] == [True, True, False, False]


def test_s2_holds_flat_when_signal_not_rich() -> None:
    d1 = date(2026, 1, 5)
    not_rich = SignalSnapshot(
        as_of=d1,
        readings=(SignalReading(kind=SignalKind.IV_VS_REALIZED, value=0.05, subject=_INDEX),),
    )
    data = InMemoryBacktestData(
        signals_by_day={d1: not_rich},
        concrete_by_cell={},
        marks_by_contract={},
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    assert result.summary.turnover == 0
    assert result.days[0].entered is False
    assert result.days[0].open_contracts == 0.0


def test_s2_stress_loss_is_worst_case_over_the_grid() -> None:
    d1 = date(2026, 1, 5)
    start = _put_valuation(3900.0, 30 / 365, 0.20)
    held_template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 2, 5),
        leg=_put_line(3).construct(d1, basket_id="seed").legs[0],
    )
    data = InMemoryBacktestData(
        signals_by_day={d1: _rich_signal(d1)},
        concrete_by_cell={(_INDEX, _TENOR, _BAND, _SIDE): held_template},
        marks_by_contract={_CONTRACT_KEY: ContractMarks(by_day={d1: start})},
    )
    crash = Scenario(
        scenario_id="crash", family="stress",
        spot_shock=-0.10, vol_shock=0.10, time_shock=0.0,
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=(crash,)),
    )
    assert result.days[0].stress_loss < 0.0
    assert result.summary.worst_stress_loss == result.days[0].stress_loss
