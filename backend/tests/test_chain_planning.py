"""Tests for the broker-agnostic chain-planning policy (`universe.chain_planning`).

This is the selection policy that used to live inside the IBKR adapter, now broker-
neutral: which listing to expand, which expiries to keep, which strikes to window, and
the composed :class:`ChainPlan`. No broker is involved — the policy reads only the plain
:class:`AvailableChain` shape. Every expected value is hand-derived from the inputs and
cited inline, never read back from the function under test.
"""

from __future__ import annotations

from datetime import date

import pytest

from contracts import InstrumentKey
from universe import (
    AvailableChain,
    ChainPlan,
    ChainSelection,
    plan_chain,
    select_capture_keys,
    select_chain,
    select_expiries,
    select_strikes,
)


def _chain(
    *,
    exchange: str,
    trading_class: str,
    multiplier: str = "100",
    expirations: tuple[str, ...] = (),
    strikes: tuple[float, ...] = (),
) -> AvailableChain:
    return AvailableChain(
        exchange=exchange,
        trading_class=trading_class,
        multiplier=multiplier,
        expirations=expirations,
        strikes=strikes,
    )


# -- ChainSelection validation ----------------------------------------------


def test_chain_selection_rejects_nonsense_config() -> None:
    with pytest.raises(ValueError):
        ChainSelection(max_expiries=0)
    with pytest.raises(ValueError):
        ChainSelection(strike_window_pct=0.0)
    with pytest.raises(ValueError):
        ChainSelection(strike_window_pct=1.5)
    with pytest.raises(ValueError):
        ChainSelection(min_strikes_per_side=0)
    with pytest.raises(ValueError):
        ChainSelection(max_strikes_per_session=0)


# -- expiry selection -------------------------------------------------------


def test_select_expiries_keeps_the_nearest_n_chronologically() -> None:
    # Deliberately unsorted with a duplicate; YYYYMMDD sorts chronologically as text.
    expirations = ["20260918", "20260619", "20260717", "20260619", "20261218"]
    # Nearest two after dedup+sort: 20260619, 20260717.
    assert select_expiries(expirations, max_expiries=2) == ("20260619", "20260717")


# -- strike selection -------------------------------------------------------


def test_select_strikes_windows_around_spot() -> None:
    strikes = [50.0, 80.0, 90.0, 100.0, 110.0, 120.0, 150.0]
    selection = ChainSelection(strike_window_pct=0.15, min_strikes_per_side=1)
    # spot 100, ±15% -> [85, 115]; keep 90,100 below and 110 above.
    assert select_strikes(strikes, 100.0, selection) == (90.0, 100.0, 110.0)


def test_select_strikes_guarantees_min_per_side_outside_a_tight_window() -> None:
    strikes = [80.0, 90.0, 100.0, 110.0, 120.0]
    selection = ChainSelection(strike_window_pct=0.01, min_strikes_per_side=2)
    # Window is empty (spot ±1%), so fall back to 2 nearest each side of spot=100.
    assert select_strikes(strikes, 100.0, selection) == (90.0, 100.0, 110.0, 120.0)


def test_select_strikes_falls_back_to_a_median_block_without_spot() -> None:
    strikes = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    selection = ChainSelection(min_strikes_per_side=2)
    # No spot -> block of min_per_side either side of the median (index 3 -> 40).
    assert select_strikes(strikes, None, selection) == (20.0, 30.0, 40.0, 50.0)


def test_select_strikes_drops_nonpositive_and_is_empty_when_none_remain() -> None:
    selection = ChainSelection(min_strikes_per_side=2)
    assert select_strikes([0.0, -5.0], 100.0, selection) == ()


# -- listing selection ------------------------------------------------------


def test_select_chain_prefers_the_requested_exchange_then_falls_back() -> None:
    nyse = _chain(exchange="NYSE", trading_class="AAPL")
    smart = _chain(exchange="SMART", trading_class="AAPL")
    assert select_chain([nyse, smart], "AAPL", "SMART") is smart
    assert select_chain([nyse], "AAPL", "SMART") is nyse  # fall back to the first listed
    assert select_chain([], "AAPL", "SMART") is None


def test_select_chain_prefers_the_primary_trading_class_over_a_secondary_listing() -> None:
    # SPY lists several trading classes; the primary one is `trading_class == symbol`.
    # A secondary class (e.g. "2SPY") on the requested exchange must not win over the
    # primary class, because its strike/expiry grid does not combine into the same
    # listed contracts — expanding it yields phantom options that fail to qualify.
    secondary = _chain(exchange="SMART", trading_class="2SPY")
    primary_other_exchange = _chain(exchange="CBOE", trading_class="SPY")
    primary_smart = _chain(exchange="SMART", trading_class="SPY")

    # Primary class on the requested exchange beats a secondary class on that exchange.
    assert select_chain([secondary, primary_smart], "SPY", "SMART") is primary_smart
    # Primary class on any exchange beats a secondary class, even on the requested one.
    assert (
        select_chain([secondary, primary_other_exchange], "SPY", "SMART")
        is primary_other_exchange
    )


# -- the composed plan ------------------------------------------------------


def test_plan_chain_composes_listing_expiries_and_strikes_with_diagnostics() -> None:
    secondary = _chain(
        exchange="SMART", trading_class="2SPY",
        expirations=("20260619",), strikes=(1.0, 2.0),
    )
    primary = _chain(
        exchange="SMART", trading_class="SPY", multiplier="100",
        expirations=("20260918", "20260619", "20260717"),
        strikes=(50.0, 90.0, 100.0, 110.0, 150.0),
    )
    selection = ChainSelection(
        max_expiries=2, strike_window_pct=0.15, min_strikes_per_side=1
    )

    plan = plan_chain("SPY", [secondary, primary], spot=100.0, selection=selection)

    assert plan is not None
    # Primary SPY listing chosen over the secondary 2SPY listing.
    assert plan.trading_class == "SPY"
    assert plan.exchange == "SMART"
    assert plan.multiplier == "100"
    # Nearest 2 expiries of the primary listing (chronological).
    assert plan.expiries == ("20260619", "20260717")
    # spot 100 ±15% -> [85,115]; with min_per_side=1 keep 90,100 below and 110 above.
    assert plan.strikes == (90.0, 100.0, 110.0)
    assert plan.rights == ("C", "P")
    # Diagnostics reflect the *offered* primary listing, not the bounded plan.
    assert plan.available_expiry_count == 3
    assert plan.available_strike_count == 5
    assert plan.spot == 100.0
    # 2 expiries × 3 strikes × 2 rights.
    assert plan.contract_count == 12
    assert isinstance(plan, ChainPlan)


def test_plan_chain_returns_none_when_no_listing_is_offered() -> None:
    selection = ChainSelection()
    assert plan_chain("SPY", [], spot=100.0, selection=selection) is None


def test_plan_chain_falls_back_to_requested_exchange_when_listing_exchange_blank() -> None:
    # A listing with a blank exchange must still yield a concrete exchange to qualify.
    blank = _chain(exchange="", trading_class="SPY", expirations=("20260619",), strikes=(100.0,))
    plan = plan_chain("SPY", [blank], spot=None, selection=ChainSelection())
    assert plan is not None
    assert plan.exchange == "SMART"  # the selection's default option_exchange


# -- capture selection (the second, subscription stage of the one policy) ---

_E1 = date(2026, 6, 19)
_E2 = date(2026, 7, 17)
_E3 = date(2026, 9, 18)


def _opt(
    symbol: str, expiry: date, strike: float, right: str, *, exchange: str = "SMART"
) -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol=symbol,
        security_type="OPT",
        exchange=exchange,
        currency="USD",
        multiplier=100.0,
        broker_contract_id=f"{symbol}-{expiry:%Y%m%d}-{strike:g}-{right}-{exchange}",
        expiry=expiry,
        strike=strike,
        option_right=right,
    )


def _stk(symbol: str, *, exchange: str = "SMART") -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol=symbol,
        security_type="STK",
        exchange=exchange,
        currency="USD",
        multiplier=1.0,
        broker_contract_id=f"{symbol}-STK",
    )


def _both_rights(symbol: str, expiry: date, strike: float, **kw: str) -> list[InstrumentKey]:
    return [_opt(symbol, expiry, strike, right, **kw) for right in ("C", "P")]


def _grid(symbol: str, expiry: date, strikes: tuple[float, ...], **kw: str) -> list[InstrumentKey]:
    return [opt for strike in strikes for opt in _both_rights(symbol, expiry, strike, **kw)]


def _expected(*keys: InstrumentKey) -> tuple[str, ...]:
    # The documented output order: underlyings (sorted) first, then options (sorted),
    # each group independently. The *selection* (which keys) is hand-derived in each test;
    # `canonical()` is only the output format here, not the function under test.
    unders = sorted(k.canonical() for k in keys if not k.is_option())
    opts = sorted(k.canonical() for k in keys if k.is_option())
    return (*unders, *opts)


def test_capture_keeps_the_nearest_the_money_strikes_within_budget() -> None:
    stk = _stk("AAPL")
    options = _grid("AAPL", _E1, (80.0, 90.0, 100.0, 110.0, 120.0))
    # spot 100, one expiry, budget 2 strikes. Distance-to-spot order: 100(0), then 90 and
    # 110 tie at 10 — the lower strike wins the deterministic tie-break — so {100, 90}.
    selection = ChainSelection(max_strikes_per_session=2)

    result = select_capture_keys([stk, *options], spots={"AAPL": 100.0}, selection=selection)

    assert result == _expected(
        stk, *_both_rights("AAPL", _E1, 100.0), *_both_rights("AAPL", _E1, 90.0)
    )


def test_capture_splits_the_strike_budget_across_the_kept_expiries() -> None:
    stk = _stk("AAPL")
    options = [
        opt
        for expiry in (_E1, _E2)
        for strike in (90.0, 100.0, 110.0)
        for opt in _both_rights("AAPL", expiry, strike)
    ]
    # Budget 2 across 2 kept expiries -> 1 strike each; nearest to spot 100 is 100 in both.
    selection = ChainSelection(max_strikes_per_session=2)

    result = select_capture_keys([stk, *options], spots={"AAPL": 100.0}, selection=selection)

    assert result == _expected(
        stk, *_both_rights("AAPL", _E1, 100.0), *_both_rights("AAPL", _E2, 100.0)
    )


def test_capture_bounds_the_maturities_to_max_expiries() -> None:
    stk = _stk("AAPL")
    options = [opt for expiry in (_E1, _E2, _E3) for opt in _both_rights("AAPL", expiry, 100.0)]
    # Nearest 2 expiries kept (E1, E2); E3 dropped. Budget None -> every kept contract streams.
    selection = ChainSelection(max_expiries=2, max_strikes_per_session=None)

    result = select_capture_keys([stk, *options], spots={"AAPL": 100.0}, selection=selection)

    assert result == _expected(
        stk, *_both_rights("AAPL", _E1, 100.0), *_both_rights("AAPL", _E2, 100.0)
    )


def test_capture_falls_back_to_the_median_strike_without_a_spot() -> None:
    stk = _stk("AAPL")
    options = _grid("AAPL", _E1, (10.0, 20.0, 30.0, 40.0, 50.0))
    # No spot for AAPL -> centre on the median listed strike (30). Budget 1 keeps {30}.
    selection = ChainSelection(max_strikes_per_session=1)

    result = select_capture_keys([stk, *options], spots={}, selection=selection)

    assert result == _expected(stk, *_both_rights("AAPL", _E1, 30.0))


def test_capture_restricts_options_to_the_requested_exchange() -> None:
    stk = _stk("AAPL")
    smart = _both_rights("AAPL", _E1, 100.0, exchange="SMART")
    cboe = _both_rights("AAPL", _E1, 100.0, exchange="CBOE")
    selection = ChainSelection(max_strikes_per_session=None)

    result = select_capture_keys(
        [stk, *smart, *cboe], spots={"AAPL": 100.0}, selection=selection, exchange="SMART"
    )

    # Only the SMART options survive the filter; the CBOE listing is excluded.
    assert result == _expected(stk, *smart)


def test_capture_always_keeps_underlyings_even_off_the_filtered_exchange() -> None:
    # The underlying lists on NASDAQ, options on SMART, and we filter options to SMART.
    stk = _stk("AAPL", exchange="NASDAQ")
    smart = _both_rights("AAPL", _E1, 100.0, exchange="SMART")
    selection = ChainSelection(max_strikes_per_session=None)

    result = select_capture_keys(
        [stk, *smart], spots={"AAPL": 100.0}, selection=selection, exchange="SMART"
    )

    assert stk.canonical() in result  # the underlying is needed for the spot, never filtered


def test_capture_budget_above_the_listed_strikes_keeps_them_all() -> None:
    stk = _stk("AAPL")
    options = [opt for strike in (90.0, 100.0, 110.0) for opt in _both_rights("AAPL", _E1, strike)]
    # Budget 10 exceeds the 3 listed strikes -> the cap drops nothing.
    selection = ChainSelection(max_strikes_per_session=10)

    result = select_capture_keys([stk, *options], spots={"AAPL": 100.0}, selection=selection)

    assert result == _expected(stk, *options)


def test_capture_is_invariant_to_input_order() -> None:
    stk = _stk("AAPL")
    options = [*_grid("AAPL", _E1, (90.0, 100.0, 110.0)), *_grid("AAPL", _E2, (90.0, 100.0, 110.0))]
    selection = ChainSelection(max_strikes_per_session=4)
    forward = select_capture_keys([stk, *options], spots={"AAPL": 100.0}, selection=selection)
    # Same instruments, reversed: subscription set and order must be identical.
    reversed_in = select_capture_keys(
        [*reversed(options), stk], spots={"AAPL": 100.0}, selection=selection
    )
    assert forward == reversed_in


def test_capture_of_an_empty_universe_is_empty() -> None:
    assert select_capture_keys([], spots={}, selection=ChainSelection()) == ()


def test_capture_of_underlyings_only_returns_just_the_underlyings() -> None:
    a, b = _stk("AAPL"), _stk("MSFT")
    selection = ChainSelection(max_strikes_per_session=5)
    result = select_capture_keys([b, a], spots={}, selection=selection)
    assert result == (a.canonical(), b.canonical())  # sorted: AAPL before MSFT
