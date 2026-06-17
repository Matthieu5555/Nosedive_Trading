from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.infra.contracts import ForwardCurvePoint, table_for_contract, validate
from algotrading.infra.forwards import (
    DegenerateParityFit,
    ForwardError,
    ForwardPair,
    estimate_forward,
    forward_curve_point,
    parity_forward_from_pair,
    regress_forward_and_discount_factor,
)
from algotrading.infra.utils.robust import (
    median_absolute_deviation,
    outlier_flags,
    theil_sen_line,
)
from fixtures.library import FORWARD_CONFIG
from fixtures.synthetic import SyntheticSurface, build_synthetic_surface

_SYNTH_SPOT = 100.0 * 0.99


def _estimate_fwd(*args: object, **kwargs: object) -> object:
    return estimate_forward(*args, config=FORWARD_CONFIG, **kwargs)  # type: ignore[arg-type]


def _pair(strike: float, call_mid: float, put_mid: float, liquidity: float = 1.0) -> ForwardPair:
    return ForwardPair(
        strike=strike, call_mid=call_mid, put_mid=put_mid, liquidity=liquidity,
        call_key=f"C@{strike:g}", put_key=f"P@{strike:g}",
    )


def _synthetic_pairs(
    surface: SyntheticSurface, *, liquidity: float = 1.0
) -> tuple[ForwardPair, ...]:
    return tuple(
        _pair(point.strike, point.call_price, point.put_price, liquidity)
        for point in surface.points
    )


def test_parity_forward_from_pair_matches_by_hand() -> None:
    got = parity_forward_from_pair(call_mid=6.0, put_mid=4.0, strike=100.0, discount_factor=0.95)
    assert got == pytest.approx(100.0 + 2.0 / 0.95, rel=1e-12)


def test_regression_recovers_hand_built_line() -> None:
    df_true, f_true = 0.90, 110.0
    strikes = (100.0, 110.0, 120.0)
    spreads = tuple(df_true * (f_true - k) for k in strikes)
    line = regress_forward_and_discount_factor(strikes, spreads, (1.0, 1.0, 1.0))
    assert line.discount_factor == pytest.approx(df_true, rel=1e-12)
    assert line.forward == pytest.approx(f_true, rel=1e-12)
    assert line.slope == pytest.approx(-df_true, rel=1e-12)


def test_regression_refuses_unphysical_discount_factor() -> None:
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 110.0), (1.0, 2.0), (1.0, 1.0))


def test_regression_needs_two_distinct_strikes() -> None:
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 100.0), (5.0, 5.0), (1.0, 1.0))


def test_regression_refuses_nonpositive_forward() -> None:
    with pytest.raises(DegenerateParityFit):
        regress_forward_and_discount_factor((100.0, 110.0), (-60.0, -65.0), (1.0, 1.0))


def test_theil_sen_needs_a_distinct_strike_pair() -> None:
    with pytest.raises(ValueError, match="no distinct-x pair"):
        theil_sen_line((100.0, 100.0, 100.0), (1.0, 2.0, 3.0))


def test_median_absolute_deviation_by_hand() -> None:
    assert median_absolute_deviation((1.0, 2.0, 4.0, 8.0)) == pytest.approx(1.5)
    assert median_absolute_deviation(()) == 0.0


def test_theil_sen_is_robust_to_a_wing_outlier() -> None:
    strikes = (80.0, 90.0, 100.0, 110.0, 120.0)
    spreads = tuple(100.0 - k for k in strikes[:-1]) + (999.0,)
    slope, intercept = theil_sen_line(strikes, spreads)
    assert slope == pytest.approx(-1.0, abs=1e-9)
    assert intercept == pytest.approx(100.0, abs=1e-9)


def test_outlier_flags_floor_prevents_false_positives_on_clean_data() -> None:
    residuals = (0.5, 0.0, 0.0, 0.0, 0.0)
    flags = outlier_flags(residuals, scale_floor=1e-4 * 99.0)
    assert flags == (True, False, False, False, False)
    assert outlier_flags((5.0, 0.0)) == (False, False)
    assert outlier_flags((0.0, 0.0, 0.0)) == (False, False, False)


def test_recovers_known_forward_and_discount_factor() -> None:
    surface = build_synthetic_surface()
    estimate = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
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
    surface = build_synthetic_surface()
    estimate = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    assert estimate.implied_rate == pytest.approx(-math.log(0.99) / 0.25, rel=1e-9)
    assert estimate.implied_carry == pytest.approx(math.log(100.0 / 99.0) / 0.25, rel=1e-9)
    assert estimate.implied_dividend == pytest.approx(0.0, abs=1e-9)


def test_explicit_config_rate_overrides_the_carry_split() -> None:
    surface = build_synthetic_surface()
    explicit = FORWARD_CONFIG.model_copy(update={"rate": 0.05})
    estimate = estimate_forward(
        "AAPL", surface.maturity_years, _synthetic_pairs(surface), config=explicit, spot=_SYNTH_SPOT
    )
    carry = math.log(100.0 / 99.0) / 0.25
    assert estimate.implied_rate == pytest.approx(0.05, rel=1e-12)
    assert estimate.implied_carry == pytest.approx(carry, rel=1e-9)
    assert estimate.implied_dividend == pytest.approx(0.05 - carry, rel=1e-9)


def test_default_none_rate_keeps_the_parity_implied_rate() -> None:
    assert FORWARD_CONFIG.rate is None
    surface = build_synthetic_surface()
    estimate = estimate_forward(
        "AAPL", surface.maturity_years, _synthetic_pairs(surface), config=FORWARD_CONFIG,
        spot=_SYNTH_SPOT,
    )
    assert estimate.implied_rate == pytest.approx(-math.log(0.99) / 0.25, rel=1e-9)


@pytest.mark.parametrize("bad_strike", [80.0, 90.0, 100.0, 110.0, 120.0])
@pytest.mark.parametrize("bump", [2.0, -3.0])
def test_single_outlier_is_rejected_and_forward_is_unchanged(
    bad_strike: float, bump: float
) -> None:
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
    estimate = _estimate_fwd("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    rejected = [point.strike for point in estimate.points if point.rejected]
    assert rejected == [bad_strike]
    assert estimate.rejected_count == 1
    assert estimate.forward == pytest.approx(100.0, abs=1e-6)


def test_clean_chain_rejects_nothing() -> None:
    surface = build_synthetic_surface()
    estimate = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    assert estimate.rejected_count == 0
    assert all(not point.rejected for point in estimate.points)


def _clean_chain_with_low_liquidity_wing() -> tuple[ForwardPair, ...]:
    df, f = 0.95, 100.0
    return tuple(
        _pair(k, call_mid=20.0 + df * (f - k), put_mid=20.0,
              liquidity=0.1 if k >= 102.0 else 1.0)
        for k in (float(s) for s in range(90, 105))
    )


def test_max_candidate_count_keeps_the_most_liquid_pairs() -> None:
    pairs = _clean_chain_with_low_liquidity_wing()
    capped = FORWARD_CONFIG.model_copy(update={"max_candidate_count": 12})
    estimate = estimate_forward("IDX", 0.5, pairs, config=capped, spot=95.0)
    assert estimate.candidate_count == 12
    kept = {point.strike for point in estimate.points}
    assert {102.0, 103.0, 104.0}.isdisjoint(kept)
    assert estimate.forward == pytest.approx(100.0, rel=1e-9)


def test_no_candidate_cap_uses_every_valid_pair() -> None:
    pairs = _clean_chain_with_low_liquidity_wing()
    estimate = estimate_forward("IDX", 0.5, pairs, config=FORWARD_CONFIG, spot=95.0)
    assert estimate.candidate_count == 15


def test_candidate_cap_above_the_pair_count_is_a_no_op() -> None:
    pairs = _clean_chain_with_low_liquidity_wing()
    big = FORWARD_CONFIG.model_copy(update={"max_candidate_count": 50})
    estimate = estimate_forward("IDX", 0.5, pairs, config=big, spot=95.0)
    assert estimate.candidate_count == 15


def _single_outlier_chain(bump: float) -> tuple[ForwardPair, ...]:
    surface = build_synthetic_surface()
    return tuple(
        ForwardPair(
            strike=point.strike,
            call_mid=point.call_price + (bump if point.strike == 100.0 else 0.0),
            put_mid=point.put_price,
            liquidity=1.0,
            call_key=f"C@{point.strike:g}",
            put_key=f"P@{point.strike:g}",
        )
        for point in surface.points
    )


def test_outlier_method_none_disables_rejection() -> None:
    pairs = _single_outlier_chain(bump=3.0)
    maturity = build_synthetic_surface().maturity_years
    mad = estimate_forward("AAPL", maturity, pairs, config=FORWARD_CONFIG, spot=_SYNTH_SPOT)
    assert mad.rejected_count == 1
    off = FORWARD_CONFIG.model_copy(update={"outlier_method": "none"})
    none = estimate_forward("AAPL", maturity, pairs, config=off, spot=_SYNTH_SPOT)
    assert none.rejected_count == 0
    assert all(not point.rejected for point in none.points)


def test_max_robust_zscore_loosening_keeps_the_outlier() -> None:
    pairs = _single_outlier_chain(bump=3.0)
    maturity = build_synthetic_surface().maturity_years
    loose = FORWARD_CONFIG.model_copy(update={"max_robust_zscore": 1000.0})
    estimate = estimate_forward("AAPL", maturity, pairs, config=loose, spot=_SYNTH_SPOT)
    assert estimate.rejected_count == 0


def test_forward_is_stable_across_strike_subset_changes() -> None:
    surface = build_synthetic_surface()
    tilt = {80.0: -1.0, 90.0: -0.5, 100.0: 0.0, 110.0: 0.5, 120.0: 1.0}

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

    full = _estimate_fwd("AAPL", surface.maturity_years,
                            pairs_for((80.0, 90.0, 100.0, 110.0, 120.0)), spot=_SYNTH_SPOT)
    assert full.forward is not None
    for subset in [(90.0, 100.0, 110.0), (80.0, 90.0, 100.0, 110.0), (90.0, 100.0, 110.0, 120.0)]:
        sub = _estimate_fwd("AAPL", surface.maturity_years, pairs_for(subset), spot=_SYNTH_SPOT)
        assert sub.forward is not None
        assert abs(sub.forward - full.forward) / full.forward < 1e-3


def test_zero_liquidity_strike_does_not_move_the_forward() -> None:
    surface = build_synthetic_surface()
    pairs = tuple(
        ForwardPair(
            strike=p.strike,
            call_mid=p.call_price + (50.0 if p.strike == 100.0 else 0.0),
            put_mid=p.put_price,
            liquidity=0.0 if p.strike == 100.0 else 1.0,
            call_key=f"C@{p.strike:g}",
            put_key=f"P@{p.strike:g}",
        )
        for p in surface.points
    )
    estimate = _estimate_fwd("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.forward == pytest.approx(100.0, abs=1e-6)
    zero_point = next(point for point in estimate.points if point.strike == 100.0)
    assert zero_point.weight == 0.0


def test_no_pairs_returns_low_confidence_reason() -> None:
    estimate = _estimate_fwd("AAPL", 0.25, (), spot=100.0)
    assert not estimate.is_usable
    assert estimate.forward is None
    assert estimate.reason_code == "no_pairs"
    assert estimate.confidence == 0.0


def test_single_pair_without_discount_factor_is_unidentified() -> None:
    pair = _pair(100.0, 6.0, 4.0)
    estimate = _estimate_fwd("AAPL", 0.25, (pair,), spot=100.0)
    assert not estimate.is_usable
    assert estimate.reason_code == "single_pair_no_discount_factor"


def test_single_pair_with_fallback_discount_factor_is_low_confidence() -> None:
    pair = _pair(100.0, 6.0, 4.0)
    estimate = _estimate_fwd("AAPL", 0.25, (pair,), spot=100.0, fallback_discount_factor=0.95)
    assert estimate.is_usable
    assert estimate.method == "single_pair_fallback"
    assert estimate.reason_code == "single_pair_fallback"
    assert estimate.quality_label == "poor"
    assert estimate.forward == pytest.approx(100.0 + 2.0 / 0.95, rel=1e-12)
    assert estimate.discount_factor == 0.95
    assert estimate.confidence < 0.5


def test_degenerate_fit_is_labeled_not_raised() -> None:
    pairs = (_pair(100.0, 1.0, 5.0), _pair(110.0, 5.0, 1.0))
    estimate = _estimate_fwd("AAPL", 0.25, pairs, spot=100.0)
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
    estimate = _estimate_fwd("AAPL", surface.maturity_years, good + junk, spot=_SYNTH_SPOT)
    assert estimate.candidate_count == 5
    assert estimate.forward == pytest.approx(100.0, rel=1e-9)


def test_confidence_orders_clean_above_single_pair() -> None:
    surface = build_synthetic_surface()
    clean = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                             spot=_SYNTH_SPOT)
    single = _estimate_fwd(
        "AAPL", 0.25,
        (_pair(100.0, 6.0, 4.0),),
        spot=100.0, fallback_discount_factor=0.95,
    )
    assert clean.confidence > single.confidence


def test_two_clean_strikes_are_fair_not_good() -> None:
    surface = build_synthetic_surface()
    points = {point.strike: point for point in surface.points}
    pairs = tuple(_pair(k, points[k].call_price, points[k].put_price) for k in (90.0, 110.0))
    estimate = _estimate_fwd("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.is_usable
    assert estimate.used_count == 2
    assert estimate.quality_label == "fair"


def test_high_residual_fit_is_labeled_poor() -> None:
    surface = build_synthetic_surface()
    points = {point.strike: point for point in surface.points}
    bow = {80.0: 6.0, 90.0: 1.5, 100.0: 0.0, 110.0: 1.5, 120.0: 6.0}
    pairs = tuple(
        _pair(k, points[k].call_price + bow[k], points[k].put_price)
        for k in (80.0, 90.0, 100.0, 110.0, 120.0)
    )
    estimate = _estimate_fwd("AAPL", surface.maturity_years, pairs, spot=_SYNTH_SPOT)
    assert estimate.is_usable
    assert estimate.rejected_count == 0
    assert estimate.quality_label == "poor"
    assert estimate.confidence < 0.3


def test_carry_is_unavailable_without_a_spot() -> None:
    surface = build_synthetic_surface()
    estimate = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=None)
    assert estimate.is_usable
    assert estimate.implied_rate is not None
    assert estimate.implied_carry is None
    assert estimate.implied_dividend is None


def test_single_pair_fallback_refuses_nonpositive_forward() -> None:
    estimate = _estimate_fwd(
        "AAPL", 0.25, (_pair(10.0, 0.0, 100.0),), spot=50.0, fallback_discount_factor=0.5
    )
    assert not estimate.is_usable
    assert estimate.reason_code == "single_pair_no_discount_factor"


def test_forward_curve_point_is_a_valid_stamped_contract() -> None:
    surface = build_synthetic_surface()
    estimate = _estimate_fwd("AAPL", surface.maturity_years, _synthetic_pairs(surface),
                                spot=_SYNTH_SPOT)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    point = forward_curve_point(
        estimate,
        snapshot_ts=snap_ts,
        expiry_date=date(2026, 6, 19),
        day_count="ACT/365",
        source_snapshot_ts=snap_ts,
        calc_ts=snap_ts,
        config_hashes={"cfg": "cfg-hash-0"},
    )
    assert isinstance(point, ForwardCurvePoint)
    validate(point)
    assert table_for_contract(ForwardCurvePoint) == "forward_curve"
    assert point.forward_price == pytest.approx(100.0, rel=1e-9)
    assert point.diagnostics.method == "parity_regression"
    assert point.diagnostics.candidate_count == 5
    assert point.diagnostics.quality_label == "good"
    assert len(point.provenance.source_records) == 10
    assert point.provenance.stamp_hash == (
        "15d18389881d129812d0500be89a58a774747ede196dee576ea8b58f69000088"
    )


def _curve_point_for_config(config: object) -> ForwardCurvePoint:
    surface = build_synthetic_surface()
    estimate = estimate_forward(
        "AAPL", surface.maturity_years, _synthetic_pairs(surface),
        config=config, spot=_SYNTH_SPOT,  # type: ignore[arg-type]
    )
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    return forward_curve_point(
        estimate, snapshot_ts=snap_ts, expiry_date=date(2026, 6, 19),
        day_count="ACT/365", source_snapshot_ts=snap_ts, calc_ts=snap_ts,
        config_hashes={"cfg": "cfg-hash-0"},
    )


def test_forward_curve_point_surfaces_parity_implied_rate_when_config_rate_is_none() -> None:
    assert FORWARD_CONFIG.rate is None
    point = _curve_point_for_config(FORWARD_CONFIG)
    parity_rate = -math.log(0.99) / 0.25
    parity_carry = math.log(100.0 / 99.0) / 0.25
    assert point.implied_rate == pytest.approx(parity_rate, rel=1e-9)
    assert point.implied_carry == pytest.approx(parity_carry, rel=1e-9)
    assert point.implied_dividend == pytest.approx(parity_rate - parity_carry, rel=1e-9)
    assert point.provenance.stamp_hash == (
        "15d18389881d129812d0500be89a58a774747ede196dee576ea8b58f69000088"
    )


def test_forward_curve_point_surfaces_explicit_config_rate_and_eq5_split() -> None:
    explicit = FORWARD_CONFIG.model_copy(update={"rate": 0.05})
    point = _curve_point_for_config(explicit)
    carry = math.log(100.0 / 99.0) / 0.25
    assert point.implied_rate == pytest.approx(0.05, rel=1e-12)
    assert point.implied_carry == pytest.approx(carry, rel=1e-9)
    assert point.implied_dividend == pytest.approx(0.05 - carry, rel=1e-9)


def test_forward_curve_point_refuses_an_unusable_estimate() -> None:
    estimate = _estimate_fwd("AAPL", 0.25, (), spot=100.0)
    snap_ts = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
    with pytest.raises(ForwardError):
        forward_curve_point(
            estimate, snapshot_ts=snap_ts, expiry_date=date(2026, 6, 19),
            day_count="ACT/365", source_snapshot_ts=snap_ts, calc_ts=snap_ts, config_hashes={"cfg": "c"},
        )
