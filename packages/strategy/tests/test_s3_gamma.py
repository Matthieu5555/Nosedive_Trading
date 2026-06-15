"""S3 gamma-trading strategy object — the pure rule layer (TARGET §3 S3, course p.107–108).

These tests drive :class:`GammaStrategy` over a **hand-built** ``GammaMarketData`` fake, so
every expected value is derived independently of the strategy code: the cheapest name, the call
delta, and the share (spot) unit are chosen here, and the call-leg shape, the wing routing, the
stock-hedge sizing arithmetic, and the p.108 scalp-band cycle are computed by hand in each test,
never read back from the object under test. The store-backed data path is exercised separately
in ``test_gamma_data.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from algotrading.infra.risk import ContractValuationInput
from algotrading.infra.risk.greeks import position_risk
from algotrading.strategy import (
    EntryAction,
    ExitAction,
    GammaConfig,
    GammaConstructionError,
    GammaStrategy,
    GreekSign,
    MarketState,
    SignalKind,
    SignalReading,
    SignalSnapshot,
    StrategyContext,
    run_strategy,
)

AS_OF = date(2026, 1, 5)


@dataclass(frozen=True)
class FakeData:
    """A hand-built ``GammaMarketData``: a fixed cheap name, call delta, and share (spot) unit."""

    name: str | None = "ASML"
    call_delta: float | None = 60.0
    share_unit: float | None = 100.0

    def cheapest_name(self, as_of: date) -> str | None:
        return self.name

    def net_dollar_delta(self, legs: object, as_of: date) -> float | None:
        return self.call_delta

    def share_unit_dollar_delta(self, name: str, as_of: date) -> float | None:
        return self.share_unit


@dataclass(frozen=True)
class FakeLine:
    """A minimal held risk line: only the two Greeks S3's exit/rebalance read."""

    position_delta: float = 0.0
    position_gamma: float = 0.0


def _strategy(data: FakeData | None = None, **cfg: object) -> GammaStrategy:
    params: dict[str, object] = {
        "index": "SX5E",
        "option_tenor": "3m",
        "entry_iv_rank_max": 0.30,
        "contracts": 2.0,
        "delta_band": 10.0,
    }
    params.update(cfg)
    config = GammaConfig(**params)  # type: ignore[arg-type]
    return GammaStrategy(config=config, data=data or FakeData())


def _iv_rank(*pairs: tuple[str, float]) -> SignalSnapshot:
    """A per-name IV-rank snapshot — one reading per (subject, value)."""
    return SignalSnapshot(
        as_of=AS_OF,
        readings=tuple(SignalReading(SignalKind.IV_RANK, value, subject=name) for name, value in pairs),
    )


# --- the contract -------------------------------------------------------------------------


def test_contract_names_the_gamma_premium_and_long_gamma_flat_delta_profile() -> None:
    contract = _strategy().contract
    assert contract.strategy_id == "S3-gamma"
    assert contract.signal is SignalKind.IV_RANK
    # The §3 profile: delta-neutral by rule, long gamma/vega, short theta.
    assert contract.intended_greeks.delta is GreekSign.FLAT
    assert contract.intended_greeks.gamma is GreekSign.LONG
    assert contract.intended_greeks.vega is GreekSign.LONG
    assert contract.intended_greeks.theta is GreekSign.SHORT
    assert contract.premium_harvested  # non-empty (validated by StrategyContract)
    assert contract.kill_condition


# --- entry: enter when the cheapest name's vol is cheap -----------------------------------


@pytest.mark.parametrize(
    ("iv_rank", "expected"),
    [
        (0.20, EntryAction.ENTER),  # below the 0.30 threshold → cheap → enter
        (0.30, EntryAction.ENTER),  # exactly at threshold → enter (<=)
        (0.45, EntryAction.NOOP),  # above → not cheap → hold flat
    ],
)
def test_entry_fires_on_cheap_iv_rank(iv_rank: float, expected: EntryAction) -> None:
    decision = _strategy().decide_entry(AS_OF, _iv_rank(("ASML", iv_rank)))
    assert decision.action is expected


def test_entry_picks_the_cheapest_name_across_subjects() -> None:
    # Two names: SAP (0.18) is cheaper than ASML (0.62). The minimum gates entry and is named.
    decision = _strategy().decide_entry(AS_OF, _iv_rank(("ASML", 0.62), ("SAP", 0.18)))
    assert decision.action is EntryAction.ENTER
    assert "SAP" in decision.reason  # the cheapest name drives the decision


def test_entry_holds_when_cheapest_name_not_cheap_enough() -> None:
    # The minimum (0.55) is still above the 0.30 entry → no cheap vol anywhere → hold flat.
    decision = _strategy().decide_entry(AS_OF, _iv_rank(("ASML", 0.70), ("SAP", 0.55)))
    assert decision.action is EntryAction.NOOP


def test_entry_holds_when_no_iv_rank_reading() -> None:
    # An empty snapshot is a labelled absence — the strategy holds flat, never trades a zero.
    decision = _strategy().decide_entry(AS_OF, SignalSnapshot(as_of=AS_OF))
    assert decision.action is EntryAction.NOOP
    assert "no IV-rank reading" in decision.reason


# --- construct: long ATM call + a delta-flattening short stock leg -------------------------


def test_construct_builds_long_call_on_the_cheapest_name_routed_to_the_call_wing() -> None:
    basket = _strategy().construct(AS_OF, basket_id="b1")
    assert basket.strategy_id == "S3-gamma"
    assert basket.underlying == "ASML"  # the single cheap name, not the index
    assert basket.trade_date == AS_OF

    call_leg = basket.legs[0]
    assert call_leg.instrument_kind == "option"
    assert call_leg.underlying == "ASML"
    assert call_leg.delta_band == "atm"
    assert call_leg.surface_side == "call"
    assert call_leg.tenor_label == "3m"
    assert call_leg.side == "long" and call_leg.quantity == pytest.approx(2.0)


def test_construct_sizes_short_stock_to_flatten_the_call_delta() -> None:
    # call dollar delta = +60, one share's dollar delta (spot) = 100.
    # shares = -call_delta / share_unit = -60/100 = -0.6  → a SHORT stock leg of 0.6.
    basket = _strategy().construct(AS_OF, basket_id="b1")
    assert len(basket.legs) == 2
    stock_leg = basket.legs[1]
    assert stock_leg.instrument_kind == "stock"
    assert stock_leg.underlying == "ASML"
    assert stock_leg.tenor_label is None and stock_leg.delta_band is None  # a stock leg
    assert stock_leg.side == "short" and stock_leg.quantity == pytest.approx(-0.6)


def test_construct_flips_to_long_stock_for_negative_call_delta() -> None:
    # A long put structure (negative option delta): call_delta = -60, share_unit = 100
    #   → shares = -(-60)/100 = +0.6 → a LONG stock leg of 0.6.
    basket = _strategy(FakeData(call_delta=-60.0)).construct(AS_OF, basket_id="b1")
    stock_leg = basket.legs[1]
    assert stock_leg.side == "long" and stock_leg.quantity == pytest.approx(0.6)


def test_construct_omits_stock_leg_when_call_already_delta_flat() -> None:
    # call delta ~ 0 → shares ~ 0, below default min_hedge_units → no stock leg.
    basket = _strategy(FakeData(call_delta=1e-5, share_unit=100.0)).construct(AS_OF, basket_id="b1")
    assert len(basket.legs) == 1  # only the call leg
    assert basket.legs[0].instrument_kind == "option"


@pytest.mark.parametrize(
    "data",
    [
        FakeData(name=None),  # no cheap name resolves
        FakeData(call_delta=None),  # grid cannot price the call's delta
        FakeData(share_unit=None),  # no spot resolves for the name
        FakeData(share_unit=0.0),  # degenerate, un-invertible spot
    ],
)
def test_construct_refuses_rather_than_emit_a_mis_sized_structure(data: FakeData) -> None:
    with pytest.raises(GammaConstructionError):
        _strategy(data).construct(AS_OF, basket_id="b1")


# --- exit / rebalance over real risk lines ------------------------------------------------


def _line(right: str, quantity: float) -> object:
    """One priced ATM-ish European option line (spot=K=100, T=0.25, vol=0.2, multiplier=1)."""
    valuation = ContractValuationInput(
        contract_key=f"ASML|OPT|{right}|100",
        underlying="ASML",
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
    return position_risk(portfolio_id="s3", quantity=quantity, valuation=valuation)


def test_exit_holds_flat_book() -> None:
    decision = _strategy().decide_exit(MarketState(as_of=AS_OF, position_lines=()))
    assert decision.action is ExitAction.HOLD


def test_exit_holds_while_long_gamma_thesis_intact() -> None:
    # A long call is long gamma → net gamma > 0 (first principles), above the 0.0 floor → hold.
    long_call = _line("C", 1.0)
    assert long_call.position_gamma > 0  # type: ignore[attr-defined]  independent: long option carries positive gamma
    decision = _strategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(long_call,))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.HOLD


def test_exit_flattens_when_net_gamma_collapses_to_non_positive() -> None:
    # A net-short-gamma book (a dominant short option) drives net gamma <= 0 → the long-gamma
    # thesis is gone → flatten. Two short lots vs one long lot ⇒ net short gamma.
    short_call, short_put, long_call = _line("C", -1.0), _line("P", -1.0), _line("C", 1.0)
    net_gamma = (
        short_call.position_gamma + short_put.position_gamma + long_call.position_gamma  # type: ignore[attr-defined]
    )
    assert net_gamma < 0  # independent: two short options outweigh one long ⇒ short gamma
    decision = _strategy().decide_exit(
        MarketState(as_of=AS_OF, position_lines=(short_call, short_put, long_call))  # type: ignore[arg-type]
    )
    assert decision.action is ExitAction.FLATTEN


# --- the p.108 scalp cycle: rebalance in delta bands --------------------------------------


@pytest.mark.parametrize(
    ("net_delta", "expected_hedge"),
    [
        (5.0, 0.0),  # inside ±10 band → hold (don't pin delta, it bleeds spread)
        (10.0, 0.0),  # exactly at the band edge → still hold (<=)
        (15.0, -15.0),  # delta rose past the band → SELL 15 (sell strength)
        (-15.0, 15.0),  # delta fell past the band → BUY 15 (buy it back lower)
    ],
)
def test_rebalance_implements_the_p108_delta_band_scalp(net_delta: float, expected_hedge: float) -> None:
    # The course p.108 cycle: hold inside the band, and on band exit re-hedge the stock leg to
    # return net delta to zero — sell as delta rises, buy back as it falls. Each round trip
    # (sell high at +15, buy back low at -15) banks the rectangle. Hand-derived from the band
    # rule: 0 inside, hedge_ratio(-1) × (net_delta - 0) outside.
    rebal = _strategy().rebalance(
        MarketState(as_of=AS_OF, position_lines=(FakeLine(position_delta=net_delta),))  # type: ignore[arg-type]
    )
    assert rebal.hedge_quantity == pytest.approx(expected_hedge)


def test_rebalance_no_trade_on_flat_book() -> None:
    rebal = _strategy().rebalance(MarketState(as_of=AS_OF, position_lines=()))
    assert rebal.hedge_quantity == pytest.approx(0.0)


# --- §6 four-context invariance -----------------------------------------------------------


def test_same_object_same_inputs_yields_equal_steps_across_four_contexts() -> None:
    # The production-shadow property: research == backtest == paper == live for equal inputs.
    strat = _strategy()
    snapshot = _iv_rank(("ASML", 0.20))
    market = MarketState(as_of=AS_OF, position_lines=())
    steps = [
        run_strategy(
            strat, context=ctx, as_of=AS_OF, signals=snapshot, market=market, basket_id="b1"
        )
        for ctx in StrategyContext
    ]
    first = steps[0]
    assert first.entry.action is EntryAction.ENTER
    assert first.basket is not None and first.basket.strategy_id == "S3-gamma"
    for step in steps[1:]:
        assert step.entry == first.entry
        assert step.exit_ == first.exit_
        assert step.rebalance == first.rebalance
        assert step.basket == first.basket
