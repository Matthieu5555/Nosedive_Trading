"""Tests for the forward & carry engine (step 6).

Independent oracles, never the code under test:

* ``fixtures.synthetic.build_synthetic_surface`` — generates call/put prices from a
  *known* forward (100.0) and discount factor (0.99) via Black-76, so put-call parity
  holds exactly. The engine must recover those known values. The generator is a
  different code path (it prices forward; the engine inverts), so this is a real
  oracle, not a round-trip against the engine.
* By-hand put-call parity ``F = K + (C - P) / DF`` (Eq 2), computed in the test.
* By-hand weighted least squares on a tiny hand-built line.
* By-hand implied carry/dividend ``r = -ln(DF)/T``, ``b = ln(F/spot)/T`` (Eq 5).

Float comparisons use explicit tolerances sized to each oracle.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest

from contracts import ForwardCurvePoint, table_for_contract, validate
from fixtures.synthetic import SyntheticSurface, build_synthetic_surface
from forwards import (
    DegenerateParityFit,
    ForwardError,
    ForwardPair,
    estimate_forward,
    forward_curve_point,
    median_absolute_deviation,
    outlier_flags,
    parity_forward_from_pair,
    regress_forward_and_discount_factor,
)
from forwards.parity import theil_sen_line

# A fixed spot for the synthetic chain: the fixture sets underlying_spot = F * DF.
_SYNTH_SPOT = 100.0 * 0.99  # 99.0


def _pair(strike: float, call_mid: float, put_mid: float, liquidity: float = 1.0) -> ForwardPair:
    """A ForwardPair with auto-derived lineage keys, to keep the tests readable."""
    return ForwardPair(
        strike=strike, call_mid=call_mid, put_mid=put_mid, liquidity=liquidity,
        call_key=f"C@{strike:g}", put_key=f"P@{strike:g}",
    )


def _synthetic_pairs(
    surface: SyntheticSurface, *, liquidity: float = 1.0
) -> tuple[ForwardPair, ...]:
    """Build engine input pairs from the known-answer surface (the oracle)."""
    return tuple(
        _pair(point.strike, point.call_price, point.put_price, liquidity)
        for point in surface.points
    )


# --------------------------------------------------------------------------- #
# Parity, regression, and robust-stat kernels (unit level)                    #
# --------------------------------------------------------------------------- #
def test_parity_forward_from_pair_matches_by_hand() -> None:
    # Eq 2: F = K + (C - P) / DF. By hand: 100 + (6.0 - 4.0)/0.95 = 102.1052631...
    got = parity_forward_from_pair(call_mid=6.0, put_mid=4.0, strike=100.0, discount_factor=0.95)
    assert got == pytest.approx(100.0 + 2.0 / 0.95, rel=1e-12)


def test_regression_recovers_hand_built_line() -> None:
    # Build y = DF*(F - K) for a chosen DF=0.90, F=110 at three strikes; the line's
    # slope is -DF and intercept is DF*F, so the fit must return DF=0.90, F=110.
    df_true, f_true = 0.90, 110.0
    strikes = (100.0, 110.0, 120.0)
    spreads = tuple(df_true * (f_true - k) for k in strikes)
    line = regress_forward_and_discount_factor(strikes, spreads, (1.0, 1.0, 1.0))
    assert line.discount_factor == pytest.approx(df_true, rel=1e-12)
    assert line.forward == pytest.approx(f_true, rel=1e-12)
    assert line.slope == pytest.approx(-df_true, rel=1e-12)


def test_regression_refuses_unphysical_discount_factor() -> None:
    # A line with positive slope implies DF < 0 (C - P rising in K), which is
    # impossible under parity; the fit must refuse rather than emit a junk forward.
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 110.0), (1.0, 2.0), (1.0, 1.0))


def test_regression_needs_two_distinct_strikes() -> None:
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 100.0), (5.0, 5.0), (1.0, 1.0))


def test_regression_refuses_nonpositive_forward() -> None:
    # A valid DF in (0,1] but a negative implied forward: C - P stays negative across
    # strikes, so DF*F < 0. K=100 -> -60, K=110 -> -65 gives slope -0.5 (DF=0.5),
    # intercept -10, F = -20. Refused rather than emitting a negative forward.
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 110.0), (-60.0, -65.0), (1.0, 1.0))


def test_theil_sen_needs_a_distinct_strike_pair() -> None:
    with pytest.raises(DegenerateParityFit):
        theil_sen_line((100.0, 100.0, 100.0), (1.0, 2.0, 3.0))


def test_median_absolute_deviation_by_hand() -> None:
    # values (1,2,4,8): median 3; abs devs (2,1,1,5); MAD = median(1,1,2,5) = 1.5.
    assert median_absolute_deviation((1.0, 2.0, 4.0, 8.0)) == pytest.approx(1.5)
    assert median_absolute_deviation(()) == 0.0


def test_theil_sen_is_robust_to_a_wing_outlier() -> None:
    # Four points on slope -1 through intercept 100, plus a gross wing outlier. The
    # OLS slope would be dragged toward the outlier; the Theil-Sen median slope is not.
    strikes = (80.0, 90.0, 100.0, 110.0, 120.0)
    spreads = tuple(100.0 - k for k in strikes[:-1]) + (999.0,)  # last strike corrupted
    slope, intercept = theil_sen_line(strikes, spreads)
    assert slope == pytest.approx(-1.0, abs=1e-9)
    assert intercept == pytest.approx(100.0, abs=1e-9)


def test_outlier_flags_floor_prevents_false_positives_on_clean_data() -> None:
    # One real outlier among otherwise-collinear (residual ~ 0) points. Without a
    # scale floor the MAD collapses to float noise and flags clean points; with the
    # floor only the genuine outlier (residual 0.5 on a ~100 price scale) is flagged.
    residuals = (0.5, 0.0, 0.0, 0.0, 0.0)
    flags = outlier_flags(residuals, scale_floor=1e-4 * 99.0)
    assert flags == (True, False, False, False, False)
    # Too few points to estimate spread -> never flags.
    assert outlier_flags((5.0, 0.0)) == (False, False)
    # All-equal residuals with no floor: the scale is zero, so nothing is flagged.
    assert outlier_flags((0.0, 0.0, 0.0)) == (False, False, False)


# --------------------------------------------------------------------------- #
# Synthetic recovery — the known-answer oracle                                #
# --------------------------------------------------------------------------- #
def test_recovers_known_forward_and_discount_factor() -> None:
    surface = build_synthetic_surface()  # F=100, DF=0.99, T=0.25
    estimate = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    assert estimate.is_usable
    assert estimate.forward == pytest.approx(surface.forward, rel=1e-9)
    assert estimate.discount_factor == pytest.approx(surface.discount_factor, rel=1e-9)
    assert estimate.reason_code == "ok"
    assert estimate.method == "parity_regression"
    assert estimate.quality_label == "good"
    assert estimate.confidence == pytest.approx(1.0)
    assert estimate.used_count == 5
    assert estimate.rejected_count == 0


def test_recovers_implied_carry_and_dividend() -> None:
    # Eq 5. The fixture sets spot = F * DF, i.e. carry == rate, so q == 0 by hand:
    #   r = -ln(0.99)/0.25 ; b = ln(100/99)/0.25 ; q = r - b == 0.
    surface = build_synthetic_surface()
    estimate = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    assert estimate.implied_rate == pytest.approx(-math.log(0.99) / 0.25, rel=1e-9)
    assert estimate.implied_carry == pytest.approx(math.log(100.0 / 99.0) / 0.25, rel=1e-9)
    assert estimate.implied_dividend == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# MAD outlier rejection (Eq 24)                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_strike", [80.0, 90.0, 100.0, 110.0, 120.0])
@pytest.mark.parametrize("bump", [2.0, -3.0])
def test_single_outlier_is_rejected_and_forward_is_unchanged(
    bad_strike: float, bump: float
) -> None:
    # Inject one corrupted call mid; assert exactly that strike is rejected and the
    # recovered forward is unchanged within tolerance (the oracle's F = 100).
    surface = build_synthetic_surface()
    pairs = tuple(
        ForwardPair(
            strike=point.strike,
            call_mid=point.call_price + (bump if point.strike == bad_strike else 0.0),
            put_mid=point.put_price,
            liquidity=1.0,
            call_key=f"C@{point.strike:g}",
            put_key=f"P@{point.strike:g}",
        )
        for point in surface.points
    )
    estimate = estimate_forward("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    rejected = [point.strike for point in estimate.points if point.rejected]
    assert rejected == [bad_strike]
    assert estimate.rejected_count == 1
    assert estimate.forward == pytest.approx(100.0, abs=1e-6)


def test_clean_chain_rejects_nothing() -> None:
    surface = build_synthetic_surface()
    estimate = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    assert estimate.rejected_count == 0
    assert all(not point.rejected for point in estimate.points)


# --------------------------------------------------------------------------- #
# Stability: small change in the eligible strike set -> small forward change   #
# --------------------------------------------------------------------------- #
def test_forward_is_stable_across_strike_subset_changes() -> None:
    # Documented stability bound: under a 0.2%-of-mid tilt of the quotes, the forward
    # recovered from any near-the-money strike subset stays within 0.1% of the forward
    # recovered from the full chain. The bound is a teeth-bearing assertion, not prose.
    surface = build_synthetic_surface()
    tilt = {80.0: -1.0, 90.0: -0.5, 100.0: 0.0, 110.0: 0.5, 120.0: 1.0}  # a deterministic tilt

    def pairs_for(strikes: tuple[float, ...]) -> tuple[ForwardPair, ...]:
        return tuple(
            ForwardPair(
                strike=p.strike,
                call_mid=p.call_price * (1.0 + 0.002 * tilt[p.strike]),
                put_mid=p.put_price,
                liquidity=1.0,
                call_key=f"C@{p.strike:g}",
                put_key=f"P@{p.strike:g}",
            )
            for p in surface.points
            if p.strike in strikes
        )

    full = estimate_forward("AAPL", surface.maturity_years,
                            pairs_for((80.0, 90.0, 100.0, 110.0, 120.0)), spot=_SYNTH_SPOT)
    assert full.forward is not None
    for subset in [(90.0, 100.0, 110.0), (80.0, 90.0, 100.0, 110.0), (90.0, 100.0, 110.0, 120.0)]:
        sub = estimate_forward("AAPL", surface.maturity_years, pairs_for(subset), spot=_SYNTH_SPOT)
        assert sub.forward is not None
        assert abs(sub.forward - full.forward) / full.forward < 1e-3


# --------------------------------------------------------------------------- #
# Zero-liquidity weighting (Eq 4)                                             #
# --------------------------------------------------------------------------- #
def test_zero_liquidity_strike_does_not_move_the_forward() -> None:
    # A corrupted strike given zero liquidity must drop out of the fit entirely: the
    # forward equals what the other strikes give, and the point records weight 0.
    surface = build_synthetic_surface()
    pairs = tuple(
        ForwardPair(
            strike=p.strike,
            call_mid=p.call_price + (50.0 if p.strike == 100.0 else 0.0),  # gross corruption
            put_mid=p.put_price,
            liquidity=0.0 if p.strike == 100.0 else 1.0,  # but zero weight
            call_key=f"C@{p.strike:g}",
            put_key=f"P@{p.strike:g}",
        )
        for p in surface.points
    )
    estimate = estimate_forward("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.forward == pytest.approx(100.0, abs=1e-6)
    zero_point = next(point for point in estimate.points if point.strike == 100.0)
    assert zero_point.weight == 0.0


# --------------------------------------------------------------------------- #
# Degenerate and negative paths — labeled, never a crash                      #
# --------------------------------------------------------------------------- #
def test_no_pairs_returns_low_confidence_reason() -> None:
    estimate = estimate_forward("AAPL", 0.25, (), spot=100.0)
    assert not estimate.is_usable
    assert estimate.forward is None
    assert estimate.reason_code == "no_pairs"
    assert estimate.confidence == 0.0


def test_single_pair_without_discount_factor_is_unidentified() -> None:
    # One pair is one equation in two unknowns (F and DF) -> not identifiable.
    pair = _pair(100.0, 6.0, 4.0)
    estimate = estimate_forward("AAPL", 0.25, (pair,), spot=100.0)
    assert not estimate.is_usable
    assert estimate.reason_code == "single_pair_no_discount_factor"


def test_single_pair_with_fallback_discount_factor_is_low_confidence() -> None:
    # Given a DF, one pair yields a forward via parity, but it is structurally
    # low-confidence and labeled as a fallback.
    pair = _pair(100.0, 6.0, 4.0)
    estimate = estimate_forward("AAPL", 0.25, (pair,), spot=100.0, fallback_discount_factor=0.95)
    assert estimate.is_usable
    assert estimate.method == "single_pair_fallback"
    assert estimate.reason_code == "single_pair_fallback"
    assert estimate.quality_label == "poor"
    assert estimate.forward == pytest.approx(100.0 + 2.0 / 0.95, rel=1e-12)
    assert estimate.discount_factor == 0.95
    assert estimate.confidence < 0.5


def test_degenerate_fit_is_labeled_not_raised() -> None:
    # Two strikes that imply an impossible (non-positive) discount factor: C - P rises
    # with K, so the slope is positive and DF would be negative. Labeled, not a crash.
    pairs = (_pair(100.0, 1.0, 5.0), _pair(110.0, 5.0, 1.0))
    estimate = estimate_forward("AAPL", 0.25, pairs, spot=100.0)
    assert not estimate.is_usable
    assert estimate.reason_code == "degenerate_fit"


def test_non_finite_and_negative_mids_are_dropped() -> None:
    surface = build_synthetic_surface()
    good = _synthetic_pairs(surface)
    junk = (
        _pair(130.0, math.nan, 1.0),
        _pair(140.0, math.inf, 1.0),
        _pair(150.0, -1.0, 1.0),
    )
    estimate = estimate_forward("AAPL", surface.maturity_years, good + junk, spot=_SYNTH_SPOT)
    # The three junk pairs are filtered out; only the five valid strikes are candidates.
    assert estimate.candidate_count == 5
    assert estimate.forward == pytest.approx(100.0, rel=1e-9)


def test_confidence_orders_clean_above_single_pair() -> None:
    surface = build_synthetic_surface()
    clean = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                             spot=_SYNTH_SPOT)
    single = estimate_forward(
        "AAPL", 0.25,
        (_pair(100.0, 6.0, 4.0),),
        spot=100.0, fallback_discount_factor=0.95,
    )
    assert clean.confidence > single.confidence


# --------------------------------------------------------------------------- #
# Quality tiers and carry availability                                        #
# --------------------------------------------------------------------------- #
def test_two_clean_strikes_are_fair_not_good() -> None:
    # Two distinct strikes identify F and DF exactly, but "good" requires >=3 used
    # pairs, so a clean two-strike fit is labeled "fair".
    surface = build_synthetic_surface()
    points = {point.strike: point for point in surface.points}
    pairs = tuple(_pair(k, points[k].call_price, points[k].put_price) for k in (90.0, 110.0))
    estimate = estimate_forward("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.is_usable
    assert estimate.used_count == 2
    assert estimate.quality_label == "fair"


def test_high_residual_fit_is_labeled_poor() -> None:
    # A symmetric "bow" on the quotes (even in K - F) leaves the slope and DF intact
    # but inflates the fit residual past ~1%, so the maturity is flagged "poor" with
    # low confidence -- the forward is still emitted, but labeled (the spec's
    # "diagnostics explain any maturity flagged poor quality").
    surface = build_synthetic_surface()
    points = {point.strike: point for point in surface.points}
    bow = {80.0: 6.0, 90.0: 1.5, 100.0: 0.0, 110.0: 1.5, 120.0: 6.0}
    pairs = tuple(
        _pair(k, points[k].call_price + bow[k], points[k].put_price)
        for k in (80.0, 90.0, 100.0, 110.0, 120.0)
    )
    estimate = estimate_forward("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.is_usable
    assert estimate.rejected_count == 0  # symmetric scatter, not a lone outlier
    assert estimate.quality_label == "poor"
    assert estimate.confidence < 0.3


def test_carry_is_unavailable_without_a_spot() -> None:
    # Forward and DF come from parity alone, but carry/dividend need a spot (Eq 5).
    surface = build_synthetic_surface()
    estimate = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=None)
    assert estimate.is_usable
    assert estimate.implied_rate is not None
    assert estimate.implied_carry is None
    assert estimate.implied_dividend is None


def test_single_pair_fallback_refuses_nonpositive_forward() -> None:
    # A pair whose C - P is very negative gives a negative parity forward at the
    # supplied DF, so no usable forward is emitted (labeled, not a crash).
    estimate = estimate_forward(
        "AAPL", 0.25, (_pair(10.0, 0.0, 100.0),), spot=50.0, fallback_discount_factor=0.5
    )
    assert not estimate.is_usable
    assert estimate.reason_code == "single_pair_no_discount_factor"


# --------------------------------------------------------------------------- #
# Contract adapter — a usable estimate projects to a valid stamped contract    #
# --------------------------------------------------------------------------- #
def test_forward_curve_point_is_a_valid_stamped_contract() -> None:
    surface = build_synthetic_surface()
    estimate = estimate_forward("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    point = forward_curve_point(
        estimate,
        snapshot_ts=snap_ts,
        expiry_date=date(2026, 6, 19),
        day_count="ACT/365",
        source_snapshot_ts=snap_ts,
        calc_ts=snap_ts,
        config_hash="cfg-hash-0",
    )
    assert isinstance(point, ForwardCurvePoint)
    validate(point)  # raises if any contract field rule is violated
    assert table_for_contract(ForwardCurvePoint) == "forward_curve"
    assert point.forward == pytest.approx(100.0, rel=1e-9)
    assert point.diagnostics.method == "parity_regression"
    assert point.diagnostics.candidate_count == 5
    assert point.diagnostics.quality_label == "good"
    # Lineage names both legs of every used strike (5 strikes -> 10 source records).
    assert len(point.provenance.source_records) == 10


def test_forward_curve_point_refuses_an_unusable_estimate() -> None:
    estimate = estimate_forward("AAPL", 0.25, (), spot=100.0)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    with pytest.raises(ForwardError):
        forward_curve_point(
            estimate, snapshot_ts=snap_ts, expiry_date=date(2026, 6, 19),
            day_count="ACT/365", source_snapshot_ts=snap_ts, calc_ts=snap_ts, config_hash="c",
        )
