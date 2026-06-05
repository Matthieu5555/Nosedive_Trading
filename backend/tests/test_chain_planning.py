"""Tests for the broker-agnostic chain-planning policy (`universe.chain_planning`).

This is the selection policy that used to live inside the IBKR adapter, now broker-
neutral: which listing to expand, which expiries to keep, which strikes to window, and
the composed :class:`ChainPlan`. No broker is involved — the policy reads only the plain
:class:`AvailableChain` shape. Every expected value is hand-derived from the inputs and
cited inline, never read back from the function under test.
"""

from __future__ import annotations

import pytest

from universe import (
    AvailableChain,
    ChainPlan,
    ChainSelection,
    plan_chain,
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
