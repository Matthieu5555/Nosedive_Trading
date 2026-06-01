"""The shared fixture library loads, and the synthetic case is self-consistent.

This is the committed acceptance test for the fixture library: every named
fixture exists, each pathology actually carries its pathology, and the synthetic
known-answer case is internally consistent so the analytics workstreams can use
it as an independent oracle.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from fixtures import (
    ALL_FIXTURES,
    black_call,
    black_put,
    fixture_names,
    get_fixture,
    parity_forward,
    svi_total_variance,
)

REQUIRED_FIXTURES = {
    "liquid_aapl",
    "liquid_msft",
    "liquid_spy",
    "crossed_quote",
    "zero_bid",
    "single_strike_maturity",
    "missing_multiplier",
    "missing_currency",
    "stale_option",
    "negative_or_zero_tte",
    "synthetic_known_answer",
}


def test_the_full_named_fixture_set_loads() -> None:
    assert set(fixture_names()) == REQUIRED_FIXTURES


def test_fixtures_are_immutable() -> None:
    fixture = get_fixture("liquid_aapl")
    with pytest.raises(dataclasses.FrozenInstanceError):
        # setattr, not direct assignment, so this checks the frozen dataclass's
        # runtime __setattr__ rather than tripping a static type error.
        setattr(fixture, "underlying_spot", 1.0)  # noqa: B010


def test_liquid_chains_have_sane_two_sided_quotes() -> None:
    for name in ("liquid_aapl", "liquid_msft", "liquid_spy"):
        chain = get_fixture(name)
        assert chain.quotes
        for quote in chain.quotes:
            assert quote.bid is not None and quote.ask is not None
            assert 0.0 < quote.bid < quote.ask


def test_crossed_quote_has_bid_above_ask() -> None:
    quote = get_fixture("crossed_quote").quotes[0]
    assert quote.bid is not None and quote.ask is not None
    assert quote.bid > quote.ask


def test_zero_bid_chain_has_a_zero_bid_and_a_one_sided_quote() -> None:
    quotes = get_fixture("zero_bid").quotes
    assert any(q.bid == 0.0 for q in quotes)
    assert any(q.bid is None for q in quotes)


def test_single_strike_maturity_has_exactly_one_strike() -> None:
    quotes = get_fixture("single_strike_maturity").quotes
    strikes = {q.instrument.strike for q in quotes}
    assert len(strikes) == 1


def test_missing_multiplier_and_currency_are_encoded() -> None:
    assert get_fixture("missing_multiplier").quotes[0].instrument.multiplier == 0.0
    assert get_fixture("missing_currency").quotes[0].instrument.currency == ""


def test_stale_option_quote_is_older_than_the_threshold() -> None:
    chain = get_fixture("stale_option")
    quote = chain.quotes[0]
    age_seconds = (chain.as_of - quote.quote_ts).total_seconds()
    assert age_seconds > 30.0


def test_negative_and_zero_time_to_expiry_present() -> None:
    chain = get_fixture("negative_or_zero_tte")
    as_of_date = chain.as_of.date()
    expiries = {q.instrument.expiry for q in chain.quotes if q.instrument.expiry is not None}
    assert any(expiry < as_of_date for expiry in expiries)  # negative TTE
    assert any(expiry == as_of_date for expiry in expiries)  # zero TTE


def test_black76_parity_holds_for_the_generator() -> None:
    # Independent identity: call - put == df * (F - K). Hand check: F=K => 0.
    forward, strike, maturity, sigma, df = 100.0, 100.0, 0.25, 0.2, 0.99
    call = black_call(forward, strike, maturity, sigma, df)
    put = black_put(forward, strike, maturity, sigma, df)
    assert call - put == pytest.approx(df * (forward - strike), abs=1e-12)
    assert call == pytest.approx(put, abs=1e-12)


def test_synthetic_case_is_analytically_recoverable() -> None:
    surface = get_fixture("synthetic_known_answer").known_answers
    assert surface is not None
    for point in surface.points:
        # Forward recovered from each call/put pair by put-call parity.
        recovered_forward = parity_forward(
            point.call_price, point.put_price, point.strike, surface.discount_factor
        )
        assert recovered_forward == pytest.approx(surface.forward, rel=1e-12)
        # Total variance matches the SVI slice it was generated from.
        w = svi_total_variance(
            point.log_moneyness,
            surface.svi_a,
            surface.svi_b,
            surface.svi_rho,
            surface.svi_m,
            surface.svi_sigma,
        )
        assert w == pytest.approx(point.total_variance, rel=1e-12)
        # And the per-strike vol is sqrt(w / T).
        assert math.sqrt(w / surface.maturity_years) == pytest.approx(point.sigma, rel=1e-12)


def test_all_fixtures_are_chain_fixtures() -> None:
    from fixtures.quotes import ChainFixture

    assert all(isinstance(fixture, ChainFixture) for fixture in ALL_FIXTURES.values())
