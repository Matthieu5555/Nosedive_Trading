"""Unit tests for the actor's valuation join — pure transport, named failures.

The join (`actor.resolve_valuation_inputs`) is the one place C's snapshot/forward/
surface results meet D's `ContractValuationInput` (ADR 0006 decision 1). It does no
pricing; the only arithmetic is the three definitional conversions
(`k = ln(strike/forward)`, the implied carry copied through, `vol = sqrt(w/T)`). The
oracles here are independent of the join: the expected volatility comes from the
*synthetic surface's own SVI parameters* (the generator, not the code under test),
and the carry/discount/spot are read off hand-built `ForwardEstimate`/snapshot inputs.

The edge-case floor (TESTING.md) is exercised against named library fixtures rather
than inline literals: missing master, no usable underlying snapshot, no usable
forward, no fitted slice, a low-confidence quote labeled not dropped, and the
one-point degenerate slice.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from actor import ValuationJoinError, resolve_valuation_inputs
from config import SolverConfig
from contracts import InstrumentMaster, MarketStateSnapshot, Position
from contracts.instrument_key import InstrumentKey
from fixtures.library import NEAR_EXPIRY, make_option, make_underlying
from fixtures.synthetic import build_synthetic_surface, svi_total_variance
from forwards import ForwardEstimate, ForwardPair, estimate_forward
from iv import iv_point, solve_iv
from provenance import ProvenanceStamp, stamp
from risk import CONFIDENCE_LOW, CONFIDENCE_OK
from snapshots import SnapshotBatch
from snapshots.builder import AssessedSnapshot
from snapshots.quote_quality import QuoteAssessment
from surfaces import SliceFit, fit_slice

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
SURFACE = build_synthetic_surface()  # F=100, DF=0.99, T=0.25, known SVI params
MATURITY = SURFACE.maturity_years
SPOT = SURFACE.forward * SURFACE.discount_factor
SOLVER = SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200)

UNDERLYING = make_underlying("AAPL")


def _stamp() -> ProvenanceStamp:
    return stamp(
        calc_ts=TS, code_version="v", config_hash="c", source_records=(), source_timestamps=()
    )


def _snapshot(instrument: InstrumentKey, reference_spot: float) -> MarketStateSnapshot:
    return MarketStateSnapshot(
        snapshot_ts=TS,
        instrument_key=instrument.canonical(),
        reference_spot=reference_spot,
        bid=reference_spot,
        ask=reference_spot,
        last=reference_spot,
        spread_pct=0.0,
        reference_type="mid",
        flags=("open",),
        completeness=1.0,
        trade_date=TS.date(),
        underlying=instrument.underlying_symbol,
        provenance=_stamp(),
    )


def _assessed(
    instrument: InstrumentKey, reference_spot: float, *, status: str = "usable"
) -> AssessedSnapshot:
    return AssessedSnapshot(
        snapshot=_snapshot(instrument, reference_spot),
        assessment=QuoteAssessment(status=status, reasons=()),
    )


def _forward_estimate() -> ForwardEstimate:
    """A usable forward estimate for AAPL at MATURITY, anchored to the real spot.

    Built from synthetic-surface call/put prices so its recovered forward is ~100 and
    its implied carry is the real cost of carry; the join copies these through.
    """
    pairs = tuple(
        ForwardPair(
            strike=p.strike,
            call_mid=p.call_price,
            put_mid=p.put_price,
            liquidity=1.0,
            call_key=make_option("AAPL", p.strike, "C", NEAR_EXPIRY).canonical(),
            put_key=make_option("AAPL", p.strike, "P", NEAR_EXPIRY).canonical(),
        )
        for p in SURFACE.points
    )
    return estimate_forward("AAPL", MATURITY, pairs, spot=SPOT)


def _slice() -> SliceFit:
    """A fitted SVI slice over the synthetic IV points, keyed to NEAR_EXPIRY."""
    points = []
    for p in SURFACE.points:
        result = solve_iv(
            p.call_price,
            contract_key=make_option("AAPL", p.strike, "C", NEAR_EXPIRY).canonical(),
            forward=SURFACE.forward,
            strike=p.strike,
            maturity_years=MATURITY,
            discount_factor=SURFACE.discount_factor,
            option_right="C",
            config=SOLVER,
        )
        points.append(
            iv_point(result, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS, config_hash="c")
        )
    return fit_slice("AAPL", MATURITY, tuple(points), expiry_date=NEAR_EXPIRY, day_count="ACT/365")


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=TS.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _position(instrument: InstrumentKey, quantity: float = 1.0) -> Position:
    return Position(
        valuation_ts=TS,
        portfolio_id="pf",
        contract_key=instrument.canonical(),
        quantity=quantity,
        source="record",
    )


def _full_batch(option: InstrumentKey, *, status: str = "usable") -> SnapshotBatch:
    assert option.strike is not None
    return SnapshotBatch(
        assessed=(
            _assessed(UNDERLYING, SPOT),
            _assessed(option, option.strike, status=status),
        ),
        skipped=(),
    )


# --------------------------------------------------------------------------- #
# Happy path: every field is a faithful copy / the three conversions          #
# --------------------------------------------------------------------------- #
def test_resolves_every_field_by_copy_and_three_conversions() -> None:
    option = make_option("AAPL", 105.0, "C", NEAR_EXPIRY)
    estimate = _forward_estimate()
    slice_fit = _slice()
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option),
        forwards=[estimate],
        slices=[slice_fit],
        masters={option.canonical(): _master(option)},
    )
    value = resolved[option.canonical()]

    # Identity / monetization fields are copies off the master's InstrumentKey.
    assert value.underlying == "AAPL"
    assert value.option_right == "C"
    assert value.strike == pytest.approx(105.0)
    assert value.multiplier == pytest.approx(100.0)
    assert value.currency == "USD"
    assert value.exercise_style == "european"  # the default policy

    # Carry, discount factor, maturity, spot are copies off the rich estimate/snapshot.
    assert value.carry == pytest.approx(estimate.implied_carry)
    assert value.discount_factor == pytest.approx(estimate.discount_factor)
    assert value.maturity_years == pytest.approx(slice_fit.maturity_years)
    assert value.spot == pytest.approx(SPOT)

    # Volatility is the one nontrivial conversion. Independent oracle: read the SAME
    # SVI total variance the surface generator defines, at k = ln(K/F), then w -> vol.
    assert estimate.forward is not None
    k = math.log(105.0 / estimate.forward)
    w = svi_total_variance(
        k, SURFACE.svi_a, SURFACE.svi_b, SURFACE.svi_rho, SURFACE.svi_m, SURFACE.svi_sigma
    )
    expected_vol = math.sqrt(w / MATURITY)
    assert value.volatility == pytest.approx(expected_vol, rel=1e-3)


def test_atm_volatility_recovers_the_generated_sigma() -> None:
    # At the at-the-money generated strike, the recovered vol equals that point's true
    # sigma (the generator's per-point sigma, an oracle independent of the join).
    atm = next(p for p in SURFACE.points if abs(p.strike - 100.0) < 1e-9)
    option = make_option("AAPL", atm.strike, "C", NEAR_EXPIRY)
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option),
        forwards=[_forward_estimate()],
        slices=[_slice()],
        masters={option.canonical(): _master(option)},
    )
    assert resolved[option.canonical()].volatility == pytest.approx(atm.sigma, rel=2e-3)


def test_low_confidence_quote_is_priced_and_labeled_not_dropped() -> None:
    # A rejected option snapshot still resolves; its confidence is LOW, never absent.
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option, status="reject"),
        forwards=[_forward_estimate()],
        slices=[_slice()],
        masters={option.canonical(): _master(option)},
    )
    assert option.canonical() in resolved
    assert resolved[option.canonical()].confidence == CONFIDENCE_LOW


def test_usable_quote_is_labeled_confidence_ok() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option, status="usable"),
        forwards=[_forward_estimate()],
        slices=[_slice()],
        masters={option.canonical(): _master(option)},
    )
    assert resolved[option.canonical()].confidence == CONFIDENCE_OK


def test_lots_of_one_contract_dedup_to_one_input() -> None:
    # Two lots of one contract share one market state (what net_lots requires).
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    resolved = resolve_valuation_inputs(
        [_position(option, 10.0), _position(option, -5.0)],
        snapshots=_full_batch(option),
        forwards=[_forward_estimate()],
        slices=[_slice()],
        masters={option.canonical(): _master(option)},
    )
    assert list(resolved) == [option.canonical()]


def test_exercise_style_policy_is_applied() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option),
        forwards=[_forward_estimate()],
        slices=[_slice()],
        masters={option.canonical(): _master(option)},
        exercise_style_for=lambda _instrument: "american",
    )
    assert resolved[option.canonical()].exercise_style == "american"


# --------------------------------------------------------------------------- #
# Negative paths: each missing piece raises, naming the contract and reason    #
# --------------------------------------------------------------------------- #
def test_missing_master_raises_naming_the_contract() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    with pytest.raises(ValuationJoinError) as info:
        resolve_valuation_inputs(
            [_position(option)],
            snapshots=_full_batch(option),
            forwards=[_forward_estimate()],
            slices=[_slice()],
            masters={},
        )
    assert info.value.contract_key == option.canonical()
    assert "master" in info.value.reason


def test_no_usable_underlying_snapshot_raises() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    # Only the option snapshot present; the underlying has no usable spot.
    batch = SnapshotBatch(assessed=(_assessed(option, 100.0),), skipped=())
    with pytest.raises(ValuationJoinError) as info:
        resolve_valuation_inputs(
            [_position(option)],
            snapshots=batch,
            forwards=[_forward_estimate()],
            slices=[_slice()],
            masters={option.canonical(): _master(option)},
        )
    assert "underlying" in info.value.reason


def test_no_usable_forward_raises() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    with pytest.raises(ValuationJoinError) as info:
        resolve_valuation_inputs(
            [_position(option)],
            snapshots=_full_batch(option),
            forwards=[],  # no forward for the maturity
            slices=[_slice()],
            masters={option.canonical(): _master(option)},
        )
    assert "forward" in info.value.reason


def test_no_fitted_slice_raises() -> None:
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    with pytest.raises(ValuationJoinError) as info:
        resolve_valuation_inputs(
            [_position(option)],
            snapshots=_full_batch(option),
            forwards=[_forward_estimate()],
            slices=[],  # no slice for the maturity
            masters={option.canonical(): _master(option)},
        )
    assert "slice" in info.value.reason


def test_insufficient_slice_counts_as_no_fitted_slice() -> None:
    # A zero-point (insufficient) slice has no curve, so the join treats it as missing.
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    empty_slice = fit_slice("AAPL", MATURITY, (), expiry_date=NEAR_EXPIRY, day_count="ACT/365")
    assert empty_slice.method == "insufficient"
    with pytest.raises(ValuationJoinError) as info:
        resolve_valuation_inputs(
            [_position(option)],
            snapshots=_full_batch(option),
            forwards=[_forward_estimate()],
            slices=[empty_slice],
            masters={option.canonical(): _master(option)},
        )
    assert "slice" in info.value.reason


def test_one_point_degenerate_slice_is_usable_flat() -> None:
    # A single-strike (nonparametric) slice is NOT insufficient: the join reads a flat
    # total variance off it and resolves the contract.
    option = make_option("AAPL", 100.0, "C", NEAR_EXPIRY)
    one_point = _slice().raw_points[:1]
    degenerate = fit_slice(
        "AAPL", MATURITY, tuple(one_point), expiry_date=NEAR_EXPIRY, day_count="ACT/365"
    )
    assert degenerate.method == "nonparametric"
    resolved = resolve_valuation_inputs(
        [_position(option)],
        snapshots=_full_batch(option),
        forwards=[_forward_estimate()],
        slices=[degenerate],
        masters={option.canonical(): _master(option)},
    )
    # vol = sqrt(w_flat / T) where w_flat is the single point's total variance.
    w_flat = one_point[0].total_variance
    assert resolved[option.canonical()].volatility == pytest.approx(math.sqrt(w_flat / MATURITY))


def test_empty_positions_resolve_to_empty_mapping() -> None:
    assert resolve_valuation_inputs(
        [], snapshots=SnapshotBatch((), ()), forwards=[], slices=[], masters={}
    ) == {}
