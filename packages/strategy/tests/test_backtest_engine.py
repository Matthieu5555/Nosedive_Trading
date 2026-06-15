"""Integration tests for the research backtester engine — S2 over a hand-built banked stretch.

The engine reinvents no pricing or attribution: it drives the landed harness, prices into landed
:class:`PositionRisk` lines, and decomposes day-over-day P&L with the landed realized attribution.
So the oracle for every number is the *landed pricer applied independently in the test* — the day
P&L is ``(price(end) - price(start)) * scale`` computed here with
:func:`~algotrading.infra.pricing.price`, never read back from the engine under test (the
independent-oracle rule, ``tasks/TESTING.md``). This is the §7.8 first target: S2, the index
short-put line, replayed through a banked stretch and an adverse (spot-down) regime.
"""

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

# --- The S2 line under test: sells one ~25Δ 30d index put/day, capacity 3, RV-IV gate at 0. ----
_INDEX = "SX5E"
_TENOR = "1M"
_BAND = "24dp"  # a put-wing band (ends with 'p'); the steered ~25Δ strike distance.
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
            max_rv_minus_iv=0.0,  # implied at least as rich as realized -> sell.
        )
    )


def _rich_signal(as_of: date) -> SignalSnapshot:
    """A signal day where index IV is rich vs realized (RV-IV = -0.02 <= 0): S2 sells."""
    return SignalSnapshot(
        as_of=as_of,
        readings=(SignalReading(kind=SignalKind.IV_VS_REALIZED, value=-0.02, subject=_INDEX),),
    )


def _put_valuation(spot: float, maturity_years: float, vol: float) -> ContractValuationInput:
    """A fixed short-put contract's market state — strike 3800, the engine re-prices the move."""
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
        discount_factor=1.0,  # zero-rate so the carry/rate axis is clean for the hand check.
        multiplier=_MULTIPLIER,
        currency="EUR",
    )


def _oracle_pnl(
    start: ContractValuationInput, end: ContractValuationInput, quantity: float
) -> float:
    """The independent realized-P&L oracle: (price(end) - price(start)) * multiplier * quantity.

    Prices both states with the landed pricer *here in the test* (the same engine the book uses,
    applied independently), so the assertion is not the engine checked against itself — it is the
    engine checked against the pricer's own reprice, which is the legitimate oracle.
    """
    start_price = price(pricing_state_for(start)).price
    end_price = price(pricing_state_for(end)).price
    return (end_price - start_price) * end.multiplier * quantity


def test_s2_sells_one_put_when_signal_rich_and_under_capacity() -> None:
    """Day 1: rich signal + empty line -> S2 opens exactly one short put (turnover == 1)."""
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
    assert result.days[0].open_contracts == 1.0  # one put now on the line.
    # First day has no prior book -> no realized P&L to mark.
    assert result.days[0].realized_pnl is None


def test_s2_daily_pnl_matches_independent_reprice_oracle() -> None:
    """Two days, one held put, an adverse spot-down move: day-2 realized P&L == the pricer oracle."""
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    start = _put_valuation(3900.0, 30 / 365, 0.20)
    # Adverse regime: spot falls 200, one day rolls off, vol spikes (the short put loses).
    end = _put_valuation(3700.0, 29 / 365, 0.28)
    held_template = HeldContract(
        contract_key=_CONTRACT_KEY, quantity=-1.0, expiry=date(2026, 2, 5),
        leg=_put_line(3).construct(d1, basket_id="seed").legs[0],
    )
    data = InMemoryBacktestData(
        # Only day 1 has a rich signal -> the line adds once, then just carries.
        signals_by_day={d1: _rich_signal(d1)},
        concrete_by_cell={(_INDEX, _TENOR, _BAND, _SIDE): held_template},
        marks_by_contract={_CONTRACT_KEY: ContractMarks(by_day={d1: start, d2: end})},
    )
    result = run_backtest(
        _put_line(3), data, dates=[d1, d2],
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    # Day 2 marks the short put (quantity -1) held overnight from d1's price to d2's.
    expected = _oracle_pnl(start, end, quantity=-1.0)
    assert result.days[1].realized_pnl == pytest.approx(expected, rel=1e-9)
    assert result.days[1].cumulative_pnl == pytest.approx(expected, rel=1e-9)
    # A spot-down move on a short put is a loss -> negative P&L (the short left tail).
    assert result.days[1].realized_pnl is not None and result.days[1].realized_pnl < 0.0
    assert result.summary.total_pnl == pytest.approx(expected, rel=1e-9)


def test_s2_attribution_decomposes_the_move_into_named_greeks() -> None:
    """The day-over-day P&L is decomposed by the landed realized attribution; terms ~ full reprice."""
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
    # The decomposition's full-reprice oracle equals the independent pricer reprice.
    assert attribution.full_reprice_pnl == pytest.approx(_oracle_pnl(start, end, -1.0), rel=1e-9)
    # The cumulative-attribution headline view sums the per-day terms (one attributed day here).
    cumulative = result.cumulative_attribution()
    assert cumulative.delta_pnl == pytest.approx(attribution.terms.delta_pnl, rel=1e-9)
    assert cumulative.vega_pnl == pytest.approx(attribution.terms.vega_pnl, rel=1e-9)
    # A short put into a spot-down + vol-up move: vega term is a loss (short vega, vol rose).
    assert attribution.terms.vega_pnl < 0.0


def test_s2_capacity_cap_stops_the_line_adding() -> None:
    """With capacity 2, the line adds on at most 2 days even given 4 rich-signal days."""
    base = date(2026, 1, 5)
    dates = [base + timedelta(i) for i in range(4)]
    # Four distinct concrete puts so each add is a different contract (no netting masking the count).
    concrete: dict[tuple[str, str | None, str | None, str], HeldContract] = {}
    marks: dict[str, ContractMarks] = {}
    leg = _put_line(2).construct(base, basket_id="seed").legs[0]
    # The in-memory adapter keys concretize by cell, so all four days resolve to one template;
    # to count adds we rely on turnover (entered days), which the capacity gate caps.
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
    # Capacity 2: the line is full after two adds; the 3rd/4th rich days do not enter.
    assert result.summary.turnover == 2
    assert [day.entered for day in result.days] == [True, True, False, False]


def test_s2_holds_flat_when_signal_not_rich() -> None:
    """No rich signal (RV-IV above the ceiling) -> S2 never sells, the line stays empty."""
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
    """The stress column is the worst-case full reprice over the grid on the day's held book."""
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
    # An adverse spot-down + vol-up scenario: the short put loses, so the stress column is negative.
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
