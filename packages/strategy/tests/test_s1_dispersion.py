from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from algotrading.infra.contracts import SURFACE_SIDE_COMBINED
from algotrading.infra.risk import ContractValuationInput
from algotrading.infra.risk.greeks import position_risk
from algotrading.infra.universe import BasketMember
from algotrading.strategy import (
    DispersionConfig,
    DispersionConstructionError,
    DispersionStrategy,
    EntryAction,
    ExitAction,
    GreekSign,
    MarketState,
    SignalKind,
    SignalReading,
    SignalSnapshot,
    StrategyContext,
    run_strategy,
    signal_snapshot,
)

AS_OF = date(2026, 1, 5)
MEMBERS = (BasketMember("ASML", 12.0), BasketMember("SAP", 4.0))


@dataclass(frozen=True)
class FakeData:

    members: tuple[BasketMember, ...] = MEMBERS
    net_delta: float | None = 100.0
    unit_delta: float | None = -50.0

    def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
        return self.members

    def net_dollar_delta(self, legs: object, as_of: date) -> float | None:
        return self.net_delta

    def forward_unit_dollar_delta(self, as_of: date) -> float | None:
        return self.unit_delta


def _strategy(data: FakeData | None = None, **cfg: object) -> DispersionStrategy:
    params: dict[str, object] = {
        "index": "SX5E",
        "top_n": 3,
        "straddle_tenor": "3m",
        "entry_threshold": 0.55,
        "contracts_per_name": 2.0,
        "delta_band": 10.0,
    }
    params.update(cfg)
    config = DispersionConfig(**params)  # type: ignore[arg-type]
    return DispersionStrategy(config=config, data=data or FakeData())


def test_contract_names_the_correlation_premium_and_long_vol_flat_delta_profile() -> None:
    contract = _strategy().contract
    assert contract.strategy_id == "S1-dispersion"
    assert contract.signal is SignalKind.IMPLIED_CORRELATION
    assert contract.intended_greeks.delta is GreekSign.FLAT
    assert contract.intended_greeks.gamma is GreekSign.LONG
    assert contract.intended_greeks.vega is GreekSign.LONG
    assert contract.intended_greeks.theta is GreekSign.SHORT
    assert contract.premium_harvested
    assert contract.kill_condition


@pytest.mark.parametrize(
    ("rho_bar", "expected"),
    [
        (0.62, EntryAction.ENTER),
        (0.55, EntryAction.ENTER),
        (0.40, EntryAction.NOOP),
    ],
)
def test_entry_fires_on_rich_implied_correlation(rho_bar: float, expected: EntryAction) -> None:
    snapshot = signal_snapshot(AS_OF, {SignalKind.IMPLIED_CORRELATION: rho_bar})
    decision = _strategy().decide_entry(AS_OF, snapshot)
    assert decision.action is expected


def test_entry_holds_when_no_correlation_reading() -> None:
    decision = _strategy().decide_entry(AS_OF, SignalSnapshot(as_of=AS_OF))
    assert decision.action is EntryAction.NOOP
    assert "no implied-correlation reading" in decision.reason


def test_entry_reads_index_subject_scoped_reading() -> None:
    snapshot = SignalSnapshot(
        as_of=AS_OF,
        readings=(SignalReading(SignalKind.IMPLIED_CORRELATION, 0.70, subject="SX5E"),),
    )
    assert _strategy().decide_entry(AS_OF, snapshot).action is EntryAction.ENTER


def test_construct_builds_per_name_straddles_routed_to_their_wings() -> None:
    basket = _strategy().construct(AS_OF, basket_id="b1")
    assert len(basket.legs) == 6
    assert basket.strategy_id == "S1-dispersion"
    assert basket.underlying == "SX5E"
    assert basket.trade_date == AS_OF

    straddle_legs = basket.legs[:4]
    for name, call_leg, put_leg in [("ASML", straddle_legs[0], straddle_legs[1]),
                                    ("SAP", straddle_legs[2], straddle_legs[3])]:
        assert call_leg.underlying == name
        assert call_leg.delta_band == "atm"
        assert call_leg.surface_side == "call"
        assert call_leg.side == "long" and call_leg.quantity == pytest.approx(2.0)
        assert put_leg.underlying == name
        assert put_leg.delta_band == "atmp"
        assert put_leg.surface_side == "put"
        assert put_leg.side == "long" and put_leg.quantity == pytest.approx(2.0)
        assert call_leg.tenor_label == put_leg.tenor_label == "3m"


def test_construct_sizes_short_forward_to_flatten_positive_net_delta() -> None:
    basket = _strategy().construct(AS_OF, basket_id="b1")
    call_leg, put_leg = basket.legs[4], basket.legs[5]
    assert call_leg.underlying == "SX5E" and put_leg.underlying == "SX5E"
    assert call_leg.delta_band == "atm" and put_leg.delta_band == "atmp"
    assert call_leg.surface_side == SURFACE_SIDE_COMBINED
    assert put_leg.surface_side == SURFACE_SIDE_COMBINED
    assert call_leg.side == "short" and call_leg.quantity == pytest.approx(-2.0)
    assert put_leg.side == "long" and put_leg.quantity == pytest.approx(2.0)


def test_construct_flips_to_long_forward_for_negative_net_delta() -> None:
    basket = _strategy(FakeData(net_delta=-100.0, unit_delta=-50.0)).construct(
        AS_OF, basket_id="b1"
    )
    call_leg, put_leg = basket.legs[4], basket.legs[5]
    assert call_leg.side == "long" and call_leg.quantity == pytest.approx(2.0)
    assert put_leg.side == "short" and put_leg.quantity == pytest.approx(-2.0)


def test_construct_omits_forward_when_straddles_already_delta_flat() -> None:
    basket = _strategy(FakeData(net_delta=0.0, unit_delta=-50.0)).construct(
        AS_OF, basket_id="b1"
    )
    assert len(basket.legs) == 4
    assert all(leg.underlying in ("ASML", "SAP") for leg in basket.legs)


def test_construct_omits_negligible_forward_below_min_hedge_units() -> None:
    basket = _strategy(FakeData(net_delta=1e-5, unit_delta=-50.0)).construct(
        AS_OF, basket_id="b1"
    )
    assert len(basket.legs) == 4


@pytest.mark.parametrize(
    "data",
    [
        FakeData(members=()),
        FakeData(net_delta=None),
        FakeData(unit_delta=None),
        FakeData(unit_delta=0.0),
    ],
)
def test_construct_refuses_rather_than_emit_a_mis_sized_basket(data: FakeData) -> None:
    with pytest.raises(DispersionConstructionError):
        _strategy(data).construct(AS_OF, basket_id="b1")


def _line(right: str, quantity: float) -> object:
    valuation = ContractValuationInput(
        contract_key=f"SX5E|OPT|{right}|100",
        underlying="SX5E",
        option_right=right,
        exercise_style="european",
        strike=100.0,
        maturity_years=0.25,
        spot=100.0,
        carry=0.0,
        volatility=0.20,
        discount_factor=0.99,
        multiplier=1.0,
        currency="EUR",
        confidence="ok",
    )
    return position_risk(portfolio_id="s1", quantity=quantity, valuation=valuation)


def test_exit_holds_flat_book() -> None:
    decision = _strategy().decide_exit(MarketState(as_of=AS_OF, position_lines=()))
    assert decision.action is ExitAction.HOLD


def test_exit_holds_while_long_vol_thesis_intact() -> None:
    call, put = _line("C", 1.0), _line("P", 1.0)
    net_vega = call.position_vega + put.position_vega  # type: ignore[attr-defined]
    assert net_vega > 0
    decision = _strategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(call, put))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD


def test_exit_flattens_when_net_vega_collapses_to_non_positive() -> None:
    short_call, short_put, long_call = _line("C", -1.0), _line("P", -1.0), _line("C", 1.0)
    net_vega = (
        short_call.position_vega + short_put.position_vega + long_call.position_vega  # type: ignore[attr-defined]
    )
    assert net_vega < 0
    decision = _strategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(short_call, short_put, long_call))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.FLATTEN


def test_rebalance_no_trade_inside_band() -> None:
    long_call, short_call = _line("C", 1.0), _line("C", -1.0)
    net_delta = long_call.position_delta + short_call.position_delta  # type: ignore[attr-defined]
    assert net_delta == pytest.approx(0.0, abs=1e-12)
    rebal = _strategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=(long_call, short_call))  # type: ignore[arg-type]
    )
    assert rebal.hedge_quantity == pytest.approx(0.0)


def test_rebalance_emits_flattening_quantity_outside_band() -> None:
    long_call = _line("C", 1.0)
    net_delta = long_call.position_delta  # type: ignore[attr-defined]
    strat = _strategy(delta_band=0.25)
    assert abs(net_delta) > 0.25
    rebal = strat.rebalance(MarketState(as_of=AS_OF, position_lines=(long_call,)))  # type: ignore[arg-type]
    assert rebal.hedge_quantity == pytest.approx(-net_delta)


def test_same_object_same_inputs_yields_equal_steps_across_four_contexts() -> None:
    strat = _strategy()
    snapshot = signal_snapshot(AS_OF, {SignalKind.IMPLIED_CORRELATION: 0.62})
    market = MarketState(as_of=AS_OF, position_lines=())
    steps = [
        run_strategy(
            strat, context=ctx, as_of=AS_OF, signals=snapshot, market=market, basket_id="b1"
        )
        for ctx in StrategyContext
    ]
    first = steps[0]
    assert first.entry.action is EntryAction.ENTER
    assert first.basket is not None and first.basket.strategy_id == "S1-dispersion"
    for step in steps[1:]:
        assert step.entry == first.entry
        assert step.exit_ == first.exit_
        assert step.rebalance == first.rebalance
        assert step.basket == first.basket
