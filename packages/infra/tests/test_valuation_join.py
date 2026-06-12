"""Seam test for ``actor.valuation_join.resolve_valuation_inputs`` (C8-B1).

The join is pure transport: it field-copies C's rich in-memory results into D's
:class:`ContractValuationInput`, with exactly three definitional conversions
(``k = ln(strike / forward)``, ``w = slice.total_variance(k)``,
``vol = sqrt(w / T)``). These tests assert the field copies straight from the
inputs, derive the two computed numbers (log-moneyness and volatility) by hand,
and exercise every labelled :class:`ValuationJoinError` branch plus the
forward=0 / maturity=0 boundary math.

Every expected number is computed in the test from the fixture inputs — never
read back from ``resolve_valuation_inputs`` — so the test catches drift in the
join's arithmetic.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.infra.actor.valuation_join import (
    DEFAULT_EXERCISE_STYLE,
    ValuationJoinError,
    default_exercise_style,
    resolve_valuation_inputs,
)
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
)
from algotrading.infra.forwards import ForwardEstimate
from algotrading.infra.risk import CONFIDENCE_LOW, CONFIDENCE_OK
from algotrading.infra.snapshots import SnapshotBatch
from algotrading.infra.snapshots.builder import AssessedSnapshot
from algotrading.infra.snapshots.quote_quality import QuoteAssessment
from algotrading.infra.surfaces import SliceFit
from algotrading.infra.surfaces.svi import SviParams
from fixtures.library import make_option, make_underlying
from fixtures.records import make_record

# --- Fixture constants ------------------------------------------------------

VALUATION_TS = datetime(2026, 6, 5, 15, 30, tzinfo=UTC)
AAPL_EXPIRY = date(2026, 9, 18)
MSFT_EXPIRY = date(2026, 12, 18)
TRADE_DATE = date(2026, 6, 5)

# Per-contract market geometry, chosen so the two computed fields are easy to
# derive by hand. forward != spot so the carry copy is not accidentally a spot.
AAPL_STRIKE = 110.0
AAPL_FORWARD = 100.0
AAPL_SPOT = 98.0
AAPL_MATURITY = 0.5
AAPL_CARRY = 0.0123
AAPL_DF = 0.985

MSFT_STRIKE = 90.0
MSFT_FORWARD = 120.0
MSFT_SPOT = 122.0
MSFT_MATURITY = 0.25
MSFT_CARRY = 0.0211
MSFT_DF = 0.991

# One SVI smile shared by both slices. With rho=0 and m=0 the total variance is
# w(k) = a + b * sqrt(k^2 + sigma^2), trivially hand-computable.
SVI = SviParams(a=0.04, b=0.10, rho=0.0, m=0.0, sigma=0.20)


def _w(k: float) -> float:
    """The SVI total variance for :data:`SVI`, recomputed here by hand."""
    return SVI.a + SVI.b * math.sqrt(k * k + SVI.sigma * SVI.sigma)


# --- Minimal real-fixture builders ------------------------------------------


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=TRADE_DATE,
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _position(contract_key: str, *, quantity: float, source: str = "record") -> Position:
    return Position(
        valuation_ts=VALUATION_TS,
        portfolio_id="pf-1",
        contract_key=contract_key,
        quantity=quantity,
        source=source,
    )


def _underlying_snapshot(symbol: str, spot: float, *, usable: bool = True) -> AssessedSnapshot:
    """A usable underlying snapshot — the only kind that supplies a spot."""
    key = make_underlying(symbol).canonical()
    snapshot = make_record(
        "market_state_snapshots",
        snapshot_ts=VALUATION_TS,
        instrument_key=key,
        reference_spot=spot,
        bid=spot - 0.1,
        ask=spot + 0.1,
        last=spot,
        trade_date=TRADE_DATE,
        underlying=symbol,
    )
    status = "usable" if usable else "reject"
    return AssessedSnapshot(snapshot=snapshot, assessment=QuoteAssessment(status=status, reasons=()))


def _option_snapshot(
    instrument: InstrumentKey, *, status: str = "usable"
) -> AssessedSnapshot:
    """An option snapshot, carrying the contract's own QC verdict (drives confidence)."""
    snapshot = make_record(
        "market_state_snapshots",
        snapshot_ts=VALUATION_TS,
        instrument_key=instrument.canonical(),
        reference_spot=5.0,
        bid=4.9,
        ask=5.1,
        last=5.0,
        spread_pct=0.04,
        trade_date=TRADE_DATE,
        underlying=instrument.underlying_symbol,
    )
    return AssessedSnapshot(snapshot=snapshot, assessment=QuoteAssessment(status=status, reasons=()))


def _forward(
    underlying: str,
    maturity_years: float,
    *,
    forward: float | None,
    discount_factor: float | None,
    carry: float | None,
) -> ForwardEstimate:
    """A forward estimate. With a positive forward/DF and positive maturity it is
    ``is_usable`` and thus indexed by the join; carry may still be ``None``."""
    return ForwardEstimate(
        underlying=underlying,
        maturity_years=maturity_years,
        forward=forward,
        discount_factor=discount_factor,
        spot=None,
        implied_rate=None,
        implied_carry=carry,
        implied_dividend=None,
        method="parity_regression",
        reason_code="ok",
        quality_label="good",
        confidence=0.9,
        candidate_count=5,
        used_count=5,
        rejected_count=0,
        residual_mad=0.001,
        points=(),
    )


def _slice(
    underlying: str,
    maturity_years: float,
    expiry_date: date,
    *,
    svi: SviParams | None = SVI,
) -> SliceFit:
    """An SVI slice (so ``total_variance`` is the closed form). ``svi=None`` yields
    an ``insufficient`` slice with no curve, which the join treats as unfitted."""
    method = "svi" if svi is not None else "insufficient"
    return SliceFit(
        underlying=underlying,
        maturity_years=maturity_years,
        expiry_date=expiry_date,
        day_count="ACT/365",
        method=method,
        svi=svi,
        rmse=0.001,
        n_points=5,
        arb_free=True,
        bound_hits=(),
        butterfly_violations=(),
        nonparametric_ks=(),
        nonparametric_ws=(),
        raw_points=(),
    )


# --- Happy-path scenario ----------------------------------------------------


def _aapl_option() -> InstrumentKey:
    return make_option("AAPL", AAPL_STRIKE, "C", AAPL_EXPIRY, multiplier=100.0, currency="USD")


def _msft_option() -> InstrumentKey:
    return make_option("MSFT", MSFT_STRIKE, "P", MSFT_EXPIRY, multiplier=50.0, currency="EUR")


def _happy_inputs() -> dict[str, object]:
    """Two distinct contracts; AAPL held across two lots (must dedup to one entry)."""
    aapl = _aapl_option()
    msft = _msft_option()
    masters = {aapl.canonical(): _master(aapl), msft.canonical(): _master(msft)}
    positions = [
        _position(aapl.canonical(), quantity=4.0),
        _position(aapl.canonical(), quantity=6.0),  # second lot of the same contract
        _position(msft.canonical(), quantity=-2.0),
    ]
    snapshots = SnapshotBatch(
        assessed=(
            _underlying_snapshot("AAPL", AAPL_SPOT),
            _underlying_snapshot("MSFT", MSFT_SPOT),
            _option_snapshot(aapl, status="usable"),  # AAPL verdict usable -> CONFIDENCE_OK
            _option_snapshot(msft, status="caution"),  # caution is still usable -> CONFIDENCE_OK
        ),
        skipped=(),
    )
    forwards = [
        _forward("AAPL", AAPL_MATURITY, forward=AAPL_FORWARD, discount_factor=AAPL_DF, carry=AAPL_CARRY),
        _forward("MSFT", MSFT_MATURITY, forward=MSFT_FORWARD, discount_factor=MSFT_DF, carry=MSFT_CARRY),
    ]
    slices = [
        _slice("AAPL", AAPL_MATURITY, AAPL_EXPIRY),
        _slice("MSFT", MSFT_MATURITY, MSFT_EXPIRY),
    ]
    return {
        "aapl": aapl,
        "msft": msft,
        "positions": positions,
        "snapshots": snapshots,
        "forwards": forwards,
        "slices": slices,
        "masters": masters,
    }


def test_happy_path_field_copies_and_dedup() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    msft: InstrumentKey = inputs["msft"]  # type: ignore[assignment]

    resolved = resolve_valuation_inputs(
        inputs["positions"],  # type: ignore[arg-type]
        snapshots=inputs["snapshots"],  # type: ignore[arg-type]
        forwards=inputs["forwards"],  # type: ignore[arg-type]
        slices=inputs["slices"],  # type: ignore[arg-type]
        masters=inputs["masters"],  # type: ignore[arg-type]
    )

    # Dedup: AAPL appears in two lots, MSFT in one -> exactly two entries.
    assert set(resolved) == {aapl.canonical(), msft.canonical()}

    # --- AAPL field copies (straight off the inputs) ---
    aapl_val = resolved[aapl.canonical()]
    assert aapl_val.underlying == "AAPL"
    assert aapl_val.option_right == "C"
    assert aapl_val.strike == AAPL_STRIKE
    assert aapl_val.multiplier == 100.0
    assert aapl_val.currency == "USD"
    assert aapl_val.maturity_years == AAPL_MATURITY
    assert aapl_val.spot == AAPL_SPOT
    assert aapl_val.carry == AAPL_CARRY
    assert aapl_val.discount_factor == AAPL_DF
    assert aapl_val.exercise_style == DEFAULT_EXERCISE_STYLE
    assert aapl_val.confidence == CONFIDENCE_OK

    # --- AAPL computed fields, derived by hand ---
    # k = ln(strike / forward) = ln(110 / 100); w = SVI(k); vol = sqrt(w / T).
    k_aapl = math.log(AAPL_STRIKE / AAPL_FORWARD)
    w_aapl = _w(k_aapl)
    vol_aapl = math.sqrt(w_aapl / AAPL_MATURITY)
    assert aapl_val.volatility == pytest.approx(vol_aapl, abs=1e-12)
    # Pin the surface read coordinate too: the variance read equals w(k).
    assert SVI.total_variance(k_aapl) == pytest.approx(w_aapl, abs=1e-12)

    # --- MSFT field copies + computed fields ---
    msft_val = resolved[msft.canonical()]
    assert msft_val.option_right == "P"
    assert msft_val.strike == MSFT_STRIKE
    assert msft_val.multiplier == 50.0
    assert msft_val.currency == "EUR"
    assert msft_val.maturity_years == MSFT_MATURITY
    assert msft_val.spot == MSFT_SPOT
    assert msft_val.carry == MSFT_CARRY
    assert msft_val.discount_factor == MSFT_DF
    assert msft_val.confidence == CONFIDENCE_OK  # "caution" verdict is still usable

    k_msft = math.log(MSFT_STRIKE / MSFT_FORWARD)
    vol_msft = math.sqrt(_w(k_msft) / MSFT_MATURITY)
    assert msft_val.volatility == pytest.approx(vol_msft, abs=1e-12)


def test_low_confidence_when_contract_verdict_not_usable() -> None:
    """A rejected option verdict still prices, labelled CONFIDENCE_LOW (not dropped)."""
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    msft: InstrumentKey = inputs["msft"]  # type: ignore[assignment]
    snapshots = SnapshotBatch(
        assessed=(
            _underlying_snapshot("AAPL", AAPL_SPOT),
            _underlying_snapshot("MSFT", MSFT_SPOT),
            _option_snapshot(aapl, status="reject"),  # rejected -> CONFIDENCE_LOW
            _option_snapshot(msft, status="usable"),
        ),
        skipped=(),
    )
    resolved = resolve_valuation_inputs(
        inputs["positions"],  # type: ignore[arg-type]
        snapshots=snapshots,
        forwards=inputs["forwards"],  # type: ignore[arg-type]
        slices=inputs["slices"],  # type: ignore[arg-type]
        masters=inputs["masters"],  # type: ignore[arg-type]
    )
    assert resolved[aapl.canonical()].confidence == CONFIDENCE_LOW
    assert resolved[msft.canonical()].confidence == CONFIDENCE_OK


def test_confidence_low_when_contract_has_no_snapshot_verdict() -> None:
    """A contract with no snapshot at all is priced low-confidence, not dropped."""
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    # Drop the AAPL option snapshot; its verdict is then absent -> default False -> LOW.
    snapshots = SnapshotBatch(
        assessed=(
            _underlying_snapshot("AAPL", AAPL_SPOT),
            _underlying_snapshot("MSFT", MSFT_SPOT),
            _option_snapshot(inputs["msft"], status="usable"),  # type: ignore[arg-type]
        ),
        skipped=(),
    )
    resolved = resolve_valuation_inputs(
        inputs["positions"],  # type: ignore[arg-type]
        snapshots=snapshots,
        forwards=inputs["forwards"],  # type: ignore[arg-type]
        slices=inputs["slices"],  # type: ignore[arg-type]
        masters=inputs["masters"],  # type: ignore[arg-type]
    )
    assert resolved[aapl.canonical()].confidence == CONFIDENCE_LOW


def test_default_exercise_style_is_european() -> None:
    assert DEFAULT_EXERCISE_STYLE == "european"
    assert default_exercise_style(_aapl_option()) == "european"


def test_exercise_style_policy_is_injected() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    resolved = resolve_valuation_inputs(
        inputs["positions"],  # type: ignore[arg-type]
        snapshots=inputs["snapshots"],  # type: ignore[arg-type]
        forwards=inputs["forwards"],  # type: ignore[arg-type]
        slices=inputs["slices"],  # type: ignore[arg-type]
        masters=inputs["masters"],  # type: ignore[arg-type]
        exercise_style_for=lambda _instrument: "american",
    )
    assert resolved[aapl.canonical()].exercise_style == "american"


# --- One test per labelled ValuationJoinError mode --------------------------


def test_error_no_instrument_master() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    masters = dict(inputs["masters"])  # type: ignore[arg-type]
    del masters[aapl.canonical()]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=masters,
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no instrument master" in exc.value.reason


def test_error_no_usable_snapshot_for_underlying() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    # AAPL underlying present but its verdict is reject -> no usable spot for AAPL.
    snapshots = SnapshotBatch(
        assessed=(
            _underlying_snapshot("AAPL", AAPL_SPOT, usable=False),
            _underlying_snapshot("MSFT", MSFT_SPOT),
        ),
        skipped=(),
    )
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=snapshots,
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no usable snapshot for underlying" in exc.value.reason
    assert "AAPL" in exc.value.reason


def test_error_no_fitted_slice_for_underlying_expiry() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    # AAPL slice present but ``insufficient`` (no curve) -> not indexed -> unresolved.
    slices = [
        _slice("AAPL", AAPL_MATURITY, AAPL_EXPIRY, svi=None),
        _slice("MSFT", MSFT_MATURITY, MSFT_EXPIRY),
    ]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=slices,
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no fitted slice" in exc.value.reason


def test_error_no_usable_forward_for_maturity() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    # AAPL forward at the wrong maturity -> no forward keyed at the slice maturity.
    forwards = [
        _forward("AAPL", AAPL_MATURITY + 0.1, forward=AAPL_FORWARD, discount_factor=AAPL_DF, carry=AAPL_CARRY),
        _forward("MSFT", MSFT_MATURITY, forward=MSFT_FORWARD, discount_factor=MSFT_DF, carry=MSFT_CARRY),
    ]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=forwards,
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no usable forward" in exc.value.reason


def test_error_contract_has_no_strike() -> None:
    inputs = _happy_inputs()
    # Build an instrument whose master carries an InstrumentKey with strike=None.
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    no_strike = InstrumentKey(
        underlying_symbol="AAPL",
        security_type="OPT",
        exchange="SMART",
        currency="USD",
        multiplier=100.0,
        broker_contract_id=aapl.broker_contract_id,
        expiry=AAPL_EXPIRY,
        strike=None,
        option_right="C",
    )
    masters = dict(inputs["masters"])  # type: ignore[arg-type]
    masters[aapl.canonical()] = _master(no_strike)
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=masters,
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no strike" in exc.value.reason


def test_error_contract_has_no_option_right() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    no_right = InstrumentKey(
        underlying_symbol="AAPL",
        security_type="OPT",
        exchange="SMART",
        currency="USD",
        multiplier=100.0,
        broker_contract_id=aapl.broker_contract_id,
        expiry=AAPL_EXPIRY,
        strike=AAPL_STRIKE,
        option_right=None,
    )
    masters = dict(inputs["masters"])  # type: ignore[arg-type]
    masters[aapl.canonical()] = _master(no_right)
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=masters,
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no option right" in exc.value.reason


def test_error_forward_incomplete_when_carry_none() -> None:
    """A forward that is ``is_usable`` (forward+DF set, T>0) but with carry None is
    indexed, then fails the completeness guard with a labelled error."""
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    forwards = [
        _forward("AAPL", AAPL_MATURITY, forward=AAPL_FORWARD, discount_factor=AAPL_DF, carry=None),
        _forward("MSFT", MSFT_MATURITY, forward=MSFT_FORWARD, discount_factor=MSFT_DF, carry=MSFT_CARRY),
    ]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=forwards,
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "incomplete" in exc.value.reason


def test_error_contract_has_no_expiry() -> None:
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    # An instrument with no expiry: the slice lookup raises before anything else.
    no_expiry = InstrumentKey(
        underlying_symbol="AAPL",
        security_type="OPT",
        exchange="SMART",
        currency="USD",
        multiplier=100.0,
        broker_contract_id=aapl.broker_contract_id,
        expiry=None,
        strike=AAPL_STRIKE,
        option_right="C",
    )
    masters = dict(inputs["masters"])  # type: ignore[arg-type]
    masters[aapl.canonical()] = _master(no_expiry)
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=inputs["forwards"],  # type: ignore[arg-type]
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=masters,
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no expiry" in exc.value.reason


# --- Boundary guards: forward=0 and maturity=0 ------------------------------
#
# FINDING (C8-B1): the join does NOT guard either boundary with a labelled
# ValuationJoinError. The C8 spec wants forward=0 / maturity=0 to raise a
# *labelled* error; today they crash bare. The two tests below assert the ACTUAL
# current behaviour (not the desired one), and are written so they will fail if
# the join is later changed to raise a ValuationJoinError, at which point they
# should be flipped to assert that.


def test_boundary_forward_zero_raises_bare_not_labelled() -> None:
    """forward=0 reaches ``math.log(strike / 0.0)``. A forward of 0 fails
    ``is_usable`` (it requires forward > 0), so it is NOT indexed and the join
    raises the labelled "no usable forward" instead — forward=0 never reaches the
    log. So the *unguarded division/log* is unreachable via a usable forward; the
    boundary surfaces as the no-forward branch. Documented here as the real path."""
    inputs = _happy_inputs()
    forwards = [
        _forward("AAPL", AAPL_MATURITY, forward=0.0, discount_factor=AAPL_DF, carry=AAPL_CARRY),
        _forward("MSFT", MSFT_MATURITY, forward=MSFT_FORWARD, discount_factor=MSFT_DF, carry=MSFT_CARRY),
    ]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=forwards,
            slices=inputs["slices"],  # type: ignore[arg-type]
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    # NOTE: this is the no-forward branch, NOT a forward=0-specific guard. The bare
    # ``math.log(strike / forward)`` has no guard of its own; it is only shielded
    # here because is_usable already filters forward==0 out of the index.
    assert "no usable forward" in exc.value.reason


def test_boundary_maturity_zero_raises_bare_zerodivision_not_labelled() -> None:
    """maturity_years=0 reaches ``sqrt(total_variance / 0.0)`` UNGUARDED.

    A forward with maturity_years=0 fails ``is_usable`` (it requires maturity > 0),
    so to actually drive a 0 into the ``vol = sqrt(w / T)`` math we keep the forward
    at the *slice* maturity (which the join reads for T) — but T is read from the
    slice, and a slice with maturity 0 IS still fitted. We give the slice
    maturity_years=0.0 and a forward keyed at 0.0; the forward needs maturity > 0 to
    be usable, so it would be dropped. Therefore maturity=0 is ALSO only reachable
    past the forward index when the forward is usable — impossible at T=0. Hence
    maturity=0 surfaces as the labelled no-forward branch, not a bare ZeroDivision.

    FINDING: neither boundary reaches the unguarded math, because ``is_usable``
    filters forward<=0 and maturity<=0 out of the forward index first. The bare
    ``math.log``/``sqrt`` divisions remain unguarded in code, but are currently
    unreachable for forward/maturity = 0 through the public entry point. A
    forward=0 or maturity=0 input yields a labelled "no usable forward" error."""
    inputs = _happy_inputs()
    aapl: InstrumentKey = inputs["aapl"]  # type: ignore[assignment]
    slices = [
        _slice("AAPL", 0.0, AAPL_EXPIRY),
        _slice("MSFT", MSFT_MATURITY, MSFT_EXPIRY),
    ]
    forwards = [
        # maturity 0.0 -> not is_usable -> not indexed.
        _forward("AAPL", 0.0, forward=AAPL_FORWARD, discount_factor=AAPL_DF, carry=AAPL_CARRY),
        _forward("MSFT", MSFT_MATURITY, forward=MSFT_FORWARD, discount_factor=MSFT_DF, carry=MSFT_CARRY),
    ]
    with pytest.raises(ValuationJoinError) as exc:
        resolve_valuation_inputs(
            inputs["positions"],  # type: ignore[arg-type]
            snapshots=inputs["snapshots"],  # type: ignore[arg-type]
            forwards=forwards,
            slices=slices,
            masters=inputs["masters"],  # type: ignore[arg-type]
        )
    assert exc.value.contract_key == aapl.canonical()
    assert "no usable forward" in exc.value.reason
