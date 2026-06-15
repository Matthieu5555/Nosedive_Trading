"""S2 index short-put line strategy object — the pure rule layer (TARGET §3 S2, course p.128–130).

These tests drive :class:`PutLineStrategy` over injected signals and a hand-built market state,
so every expected value is derived independently of the strategy code: the RV−IV gate, the
capacity cap, the steered put-leg shape, the rolling-line cycle, and the drawdown-proxy kill are
computed by hand in each test, never read back from the object under test. S2 is config-only (no
store-backed data adapter), so there is no separate data-path test module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from algotrading.strategy import (
    EntryAction,
    ExitAction,
    GreekSign,
    MarketState,
    PutLineConfig,
    PutLineStrategy,
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
    """A minimal held risk line: only the Greek S2's drawdown-proxy exit reads."""

    position_delta: float = 0.0


def _strategy(**cfg: object) -> PutLineStrategy:
    params: dict[str, object] = {
        "index": "SX5E",
        "put_tenor": "1m",
        "put_delta_band": "24dp",
        "line_capacity": 30,
        "contracts_per_day": 1.0,
        "max_rv_minus_iv": 0.0,
    }
    params.update(cfg)
    return PutLineStrategy(config=PutLineConfig(**params))  # type: ignore[arg-type]


def _rv_iv(value: float) -> SignalSnapshot:
    """An index-level RV−IV snapshot (subjectless; decide_entry's index fallback finds it)."""
    return signal_snapshot(AS_OF, {SignalKind.IV_VS_REALIZED: value})


# --- the contract -------------------------------------------------------------------------


def test_contract_names_the_left_tail_premium_and_short_vol_profile() -> None:
    contract = _strategy().contract
    assert contract.strategy_id == "S2-index-put-line"
    assert contract.signal is SignalKind.IV_VS_REALIZED
    # The §3 profile: short downside vega + gamma, positive (earned) theta, carried long delta
    # (the deliberate short left tail — the opposite of S1's flat-delta book).
    assert contract.intended_greeks.delta is GreekSign.LONG
    assert contract.intended_greeks.gamma is GreekSign.SHORT
    assert contract.intended_greeks.vega is GreekSign.SHORT
    assert contract.intended_greeks.theta is GreekSign.LONG
    assert contract.premium_harvested  # non-empty (validated by StrategyContract)
    assert contract.kill_condition


# --- config validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"put_delta_band": "24dc"},  # a call band — must be a put-wing band
        {"put_delta_band": "atm"},  # the ATM call pillar — not a put band
        {"line_capacity": 0},  # capacity must be positive
        {"contracts_per_day": 0.0},  # size must be positive
        {"exit_delta_ceiling": -1.0},  # a ceiling, when set, must be positive
    ],
)
def test_config_rejects_malformed_parameters(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _strategy(**bad)


# --- entry: the signal gate (implied rich vs realized) ------------------------------------


@pytest.mark.parametrize(
    ("rv_minus_iv", "expected"),
    [
        (-0.03, EntryAction.ENTER),  # implied richer than realized by 3 vol pts → premium → sell
        (0.0, EntryAction.ENTER),  # exactly at the ceiling → enter (<=)
        (0.05, EntryAction.NOOP),  # realized richer than implied → no premium → hold
    ],
)
def test_entry_fires_when_implied_is_rich_vs_realized(
    rv_minus_iv: float, expected: EntryAction
) -> None:
    decision = _strategy().decide_entry(AS_OF, _rv_iv(rv_minus_iv))
    assert decision.action is expected


def test_entry_holds_when_no_rv_iv_reading() -> None:
    decision = _strategy().decide_entry(AS_OF, SignalSnapshot(as_of=AS_OF))
    assert decision.action is EntryAction.NOOP
    assert "no IV-vs-realized reading" in decision.reason


def test_entry_reads_index_subject_scoped_reading() -> None:
    snapshot = SignalSnapshot(
        as_of=AS_OF,
        readings=(SignalReading(SignalKind.IV_VS_REALIZED, -0.02, subject="SX5E"),),
    )
    assert _strategy().decide_entry(AS_OF, snapshot).action is EntryAction.ENTER


# --- capacity + the daily sell decision ---------------------------------------------------


@pytest.mark.parametrize(
    ("open_contracts", "at_capacity"),
    [(0, False), (29, False), (30, True), (31, True)],  # capacity 30: full at 30 (>=)
)
def test_line_at_capacity_is_the_open_count_vs_cap(
    open_contracts: int, at_capacity: bool
) -> None:
    assert _strategy().line_at_capacity(open_contracts) is at_capacity


def test_decide_sell_enters_when_premium_present_and_under_capacity() -> None:
    decision = _strategy().decide_sell(AS_OF, _rv_iv(-0.03), open_contracts=10)
    assert decision.action is EntryAction.ENTER


def test_decide_sell_holds_at_capacity_even_with_premium() -> None:
    decision = _strategy().decide_sell(AS_OF, _rv_iv(-0.03), open_contracts=30)
    assert decision.action is EntryAction.NOOP
    assert "capacity" in decision.reason  # the capacity gate, not the signal, held it


def test_decide_sell_holds_on_no_premium_even_under_capacity() -> None:
    decision = _strategy().decide_sell(AS_OF, _rv_iv(0.05), open_contracts=0)
    assert decision.action is EntryAction.NOOP
    assert "RV-IV" in decision.reason  # the signal gate held it, not capacity


def test_rolling_line_cycle_sells_to_capacity_then_resumes_as_one_expires() -> None:
    # The course rolling line: sell one put/day while under the cap, stop at the cap, resume the
    # next day as the oldest expires and frees a slot. Capacity 3, premium always on offer.
    strat = _strategy(line_capacity=3)
    premium = _rv_iv(-0.03)
    # Days 1-3 fill the line (open 0 → 1 → 2): each sells.
    for open_contracts in (0, 1, 2):
        assert strat.decide_sell(AS_OF, premium, open_contracts=open_contracts).action is (
            EntryAction.ENTER
        )
    # Day 4 the line is full (open 3): hold, do not over-sell.
    assert strat.decide_sell(AS_OF, premium, open_contracts=3).action is EntryAction.NOOP
    # Day 5 one put has expired (open back to 2): the slot frees, the line resumes selling.
    assert strat.decide_sell(AS_OF, premium, open_contracts=2).action is EntryAction.ENTER


# --- construct: one short OTM index put at the steered band --------------------------------


def test_construct_emits_one_short_put_at_the_steered_band_on_the_put_wing() -> None:
    basket = _strategy(contracts_per_day=2.0).construct(AS_OF, basket_id="b1")
    assert basket.strategy_id == "S2-index-put-line"
    assert basket.underlying == "SX5E"
    assert basket.trade_date == AS_OF
    assert len(basket.legs) == 1  # the line adds one day's put per construct
    put_leg = basket.legs[0]
    assert put_leg.instrument_kind == "option"
    assert put_leg.underlying == "SX5E"
    assert put_leg.delta_band == "24dp"  # the steered ≈25Δ ≈3% OTM band
    assert put_leg.surface_side == "put"  # routed to the put wing (ADR 0048)
    assert put_leg.tenor_label == "1m"
    assert put_leg.side == "short" and put_leg.quantity == pytest.approx(-2.0)


# --- exit: the drawdown-proxy kill --------------------------------------------------------


def test_exit_defers_to_the_execution_kill_switch_without_a_ceiling() -> None:
    # No position-side proxy configured → hold and defer the flatten to execution (§5.9/§6),
    # even with a fat held position.
    decision = _strategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(FakeLine(position_delta=9_999.0),))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD
    assert "kill switch" in decision.reason


def test_exit_holds_flat_line_with_ceiling_set() -> None:
    decision = _strategy(exit_delta_ceiling=1_000.0).decide_exit(
        MarketState(as_of=AS_OF, position_lines=())
    )
    assert decision.action is ExitAction.HOLD


def test_exit_flattens_when_net_delta_breaches_the_ceiling() -> None:
    # Short puts going ITM in a drawdown drive net delta up: two lines summing to 1500 >= 1000.
    lines = (FakeLine(position_delta=900.0), FakeLine(position_delta=600.0))
    net_delta = sum(line.position_delta for line in lines)  # independent: 1500
    assert net_delta == pytest.approx(1500.0)
    decision = _strategy(exit_delta_ceiling=1_000.0).decide_exit(
        MarketState(as_of=AS_OF, position_lines=lines)  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.FLATTEN


def test_exit_holds_when_net_delta_below_ceiling() -> None:
    decision = _strategy(exit_delta_ceiling=1_000.0).decide_exit(
        MarketState(as_of=AS_OF, position_lines=(FakeLine(position_delta=500.0),))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD


# --- rebalance: S2 carries its delta intentionally ----------------------------------------


def test_rebalance_is_always_a_no_op() -> None:
    # S2 is not delta-neutral by rule (the carried long delta is the strategy), so it never
    # re-hedges — even with a fat held delta.
    rebal = _strategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=(FakeLine(position_delta=5_000.0),))  # type: ignore[arg-type]
    )
    assert rebal.hedge_quantity == pytest.approx(0.0)


# --- §6 four-context invariance -----------------------------------------------------------


def test_same_object_same_inputs_yields_equal_steps_across_four_contexts() -> None:
    strat = _strategy()
    snapshot = _rv_iv(-0.03)
    market = MarketState(as_of=AS_OF, position_lines=())
    steps = [
        run_strategy(
            strat, context=ctx, as_of=AS_OF, signals=snapshot, market=market, basket_id="b1"
        )
        for ctx in StrategyContext
    ]
    first = steps[0]
    assert first.entry.action is EntryAction.ENTER
    assert first.basket is not None and first.basket.strategy_id == "S2-index-put-line"
    for step in steps[1:]:
        assert step.entry == first.entry
        assert step.exit_ == first.exit_
        assert step.rebalance == first.rebalance
        assert step.basket == first.basket
