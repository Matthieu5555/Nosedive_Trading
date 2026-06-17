from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from algotrading.infra.surfaces import calendar_violations
from algotrading.strategy import (
    CalendarCarryConfig,
    CalendarCarryStrategy,
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


@dataclass(frozen=True)
class FakeLine:

    position_theta: float = 0.0


def _strategy(**cfg: object) -> CalendarCarryStrategy:
    params: dict[str, object] = {
        "index": "SX5E",
        "front_tenor": "1m",
        "back_tenor": "3m",
        "strike_band": "atm",
        "entry_slope_threshold": 0.0,
        "contracts": 1.0,
    }
    params.update(cfg)
    return CalendarCarryStrategy(config=CalendarCarryConfig(**params))  # type: ignore[arg-type]


def _slope(value: float, *, subject: str | None = None) -> SignalSnapshot:
    if subject is None:
        return signal_snapshot(AS_OF, {SignalKind.TERM_STRUCTURE_SLOPE: value})
    return SignalSnapshot(
        as_of=AS_OF,
        readings=(
            SignalReading(SignalKind.TERM_STRUCTURE_SLOPE, value, subject=subject),
        ),
    )


def test_contract_names_the_carry_premium_and_short_front_long_back_profile() -> None:
    contract = _strategy().contract
    assert contract.strategy_id == "S5-calendar-carry"
    assert contract.signal is SignalKind.TERM_STRUCTURE_SLOPE
    assert "carry" in contract.premium_harvested
    assert contract.intended_greeks.vega is GreekSign.LONG
    assert contract.intended_greeks.theta is GreekSign.LONG
    assert contract.intended_greeks.gamma is GreekSign.SHORT
    assert contract.intended_greeks.delta is GreekSign.FLAT
    assert "invert" in contract.kill_condition


@pytest.mark.parametrize(
    ("slope", "threshold", "expected"),
    [
        (0.5, 0.0, EntryAction.ENTER),
        (0.0, 0.0, EntryAction.ENTER),
        (-0.1, 0.0, EntryAction.NOOP),
        (0.2, 0.3, EntryAction.NOOP),
        (0.3, 0.3, EntryAction.ENTER),
    ],
)
def test_entry_fires_only_in_contango_at_or_above_threshold(
    slope: float, threshold: float, expected: EntryAction
) -> None:
    strategy = _strategy(entry_slope_threshold=threshold)
    decision = strategy.decide_entry(AS_OF, _slope(slope))
    assert decision.action is expected


def test_entry_noops_without_a_term_slope_reading() -> None:
    decision = _strategy().decide_entry(AS_OF, SignalSnapshot(as_of=AS_OF))
    assert decision.action is EntryAction.NOOP
    assert "no term-structure slope" in decision.reason


def test_entry_prefers_the_index_subject_reading_over_a_global_one() -> None:
    strategy = _strategy(index="SX5E", entry_slope_threshold=0.0)
    snapshot = SignalSnapshot(
        as_of=AS_OF,
        readings=(
            SignalReading(SignalKind.TERM_STRUCTURE_SLOPE, -1.0, subject="SX5E"),
            SignalReading(SignalKind.TERM_STRUCTURE_SLOPE, 5.0, subject=None),
        ),
    )
    assert strategy.decide_entry(AS_OF, snapshot).action is EntryAction.NOOP


def test_construct_builds_a_same_strike_short_front_long_back_calendar() -> None:
    basket = _strategy(contracts=2.0).construct(AS_OF, basket_id="b1")
    assert basket.strategy_id == "S5-calendar-carry"
    assert basket.underlying == "SX5E"
    assert len(basket.legs) == 2

    front, back = basket.legs
    assert front.side == "short"
    assert front.quantity == -2.0
    assert front.tenor_label == "1m"
    assert back.side == "long"
    assert back.quantity == 2.0
    assert back.tenor_label == "3m"

    assert front.delta_band == back.delta_band == "atm"
    assert front.surface_side == back.surface_side
    assert front.underlying == back.underlying == "SX5E"
    assert front.tenor_label != back.tenor_label


def test_exit_defers_to_the_kill_switch_when_no_proxy_configured() -> None:
    decision = _strategy().decide_exit(MarketState(as_of=AS_OF))
    assert decision.action is ExitAction.HOLD
    assert "kill switch" in decision.reason


@pytest.mark.parametrize(
    ("net_theta", "floor", "expected"),
    [
        (5.0, 0.0, ExitAction.HOLD),
        (0.0, 0.0, ExitAction.FLATTEN),
        (-3.0, 0.0, ExitAction.FLATTEN),
        (1.0, 2.0, ExitAction.FLATTEN),
        (3.0, 2.0, ExitAction.HOLD),
    ],
)
def test_exit_kills_when_net_theta_falls_to_the_floor(
    net_theta: float, floor: float, expected: ExitAction
) -> None:
    strategy = _strategy(exit_theta_floor=floor)
    lines = (FakeLine(position_theta=net_theta),)
    decision = strategy.decide_exit(MarketState(as_of=AS_OF, position_lines=lines))  # type: ignore[arg-type]
    assert decision.action is expected


def test_exit_holds_when_flat_even_with_a_floor() -> None:
    strategy = _strategy(exit_theta_floor=0.0)
    decision = strategy.decide_exit(MarketState(as_of=AS_OF))
    assert decision.action is ExitAction.HOLD
    assert "flat" in decision.reason


def test_rebalance_holds_the_calendar_with_no_band_hedge() -> None:
    lines = (FakeLine(position_theta=10.0),)
    instruction = _strategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=lines)  # type: ignore[arg-type]
    )
    assert instruction.hedge_quantity == 0.0


@pytest.mark.parametrize(
    "front_tenor",
    ["1m", "1m"],
)
def test_config_rejects_equal_front_and_back_tenors(front_tenor: str) -> None:
    with pytest.raises(ValueError, match="must differ"):
        _strategy(front_tenor=front_tenor, back_tenor=front_tenor)


def _atm_strike_k() -> float:
    return 0.0


def test_calendar_parity_identity_holds_in_the_entry_regime() -> None:
    strategy = _strategy(front_tenor="1m", back_tenor="3m")
    basket = strategy.construct(AS_OF, basket_id="b1")
    front, back = basket.legs

    t_front = 1.0 / 12.0
    t_back = 3.0 / 12.0

    sigma_front = 0.20
    sigma_back = 0.22

    w_front = sigma_front * sigma_front * t_front
    w_back = sigma_back * sigma_back * t_back

    assert w_back == pytest.approx(0.0121, abs=1e-9)
    assert w_front == pytest.approx(1.0 / 300.0, abs=1e-9)

    k = _atm_strike_k()
    slices = (
        (t_front, lambda _k: w_front),
        (t_back, lambda _k: w_back),
    )
    violations = calendar_violations(slices, (k,))

    assert violations == ()
    assert front.side == "short"
    assert back.side == "long"
    assert w_back >= w_front


def test_calendar_parity_identity_breaks_when_the_term_structure_inverts() -> None:
    t_front = 1.0 / 12.0
    t_back = 3.0 / 12.0

    sigma_front = 0.40
    sigma_back = 0.18

    w_front = sigma_front * sigma_front * t_front
    w_back = sigma_back * sigma_back * t_back

    assert w_front > w_back

    k = _atm_strike_k()
    slices = (
        (t_front, lambda _k: w_front),
        (t_back, lambda _k: w_back),
    )
    violations = calendar_violations(slices, (k,))

    assert len(violations) == 1
    violation = violations[0]
    assert violation.maturity_short == pytest.approx(t_front)
    assert violation.maturity_long == pytest.approx(t_back)
    assert violation.w_short == pytest.approx(w_front)
    assert violation.w_long == pytest.approx(w_back)


@pytest.mark.parametrize("context", list(StrategyContext))
def test_one_logic_four_contexts_emit_an_equal_calendar_step(
    context: StrategyContext,
) -> None:
    strategy = _strategy(entry_slope_threshold=0.0)
    step = run_strategy(
        strategy,
        context=context,
        as_of=AS_OF,
        signals=_slope(0.6),
        market=MarketState(as_of=AS_OF),
        basket_id="b1",
    )
    assert step.entry.action is EntryAction.ENTER
    assert step.basket is not None
    assert step.basket.strategy_id == "S5-calendar-carry"
    assert len(step.basket.legs) == 2
    assert step.strategy_id == "S5-calendar-carry"


def test_four_contexts_agree_on_the_emitted_step() -> None:
    strategy = _strategy(entry_slope_threshold=0.0)
    steps = [
        run_strategy(
            strategy,
            context=context,
            as_of=AS_OF,
            signals=_slope(0.6),
            market=MarketState(as_of=AS_OF),
            basket_id="b1",
        )
        for context in StrategyContext
    ]
    baskets = {step.basket for step in steps}
    entries = {step.entry for step in steps}
    assert len(baskets) == 1
    assert len(entries) == 1
