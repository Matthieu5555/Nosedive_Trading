"""INDEPENDENT cross-check of residual diagnosis — commit 78c684b, ADR 0057.

Adversarial second opinion on the gated walk-forward residual regression and its
as-of persistence. Independent oracles, deliberately different from the
implementer's ``test_residual_diagnosis.py``:

1. NO-LOOK-AHEAD: a residual series with a FUTURE-dated row; an as-of read at an
   earlier date D must exclude it (the implementer probes adjacent days; this plants
   a row dated months ahead and asserts it is invisible as-of D). Cross-checked via
   the ``check-lookahead-bias`` skill: the read is keyed on ``as_of_date`` and
   filters ``<= as_of``, the past never sees the future.

2. PLANTED-COEFFICIENT RECOVERY on a SECOND synthetic dataset with a DIFFERENT known
   relationship (slippage-dominant, not skew-dominant), and recovered via an
   INDEPENDENT normal-equations OLS oracle (numpy only, NOT scipy.linalg.lstsq) so
   the check does not share a solver with the code under test.

3. GATED below the depth floor: dominant=None, no fabricated coefficient.

4. STRICTLY TIME-ORDERED SPLIT: a series with a step change at a known index proves
   the first n_train rows train and the trailing min_oos_days score — past trains,
   future scores.

Every store write goes to ``tmp_path`` (a TEMP store); canonical ``data/`` is never
touched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from algotrading.infra.contracts import ResidualObservation
from algotrading.infra.risk.residual_diagnosis import (
    CANDIDATE_FACTORS,
    DiagnosisStatus,
    RegressionConfig,
    diagnose_residual,
    persist_residual_observations,
    read_residual_series,
)
from algotrading.infra.storage import ParquetStore

from .fixtures.records import make_stamp

_UNDERLYING = "SX5E"
_PORTFOLIO = "book-xcheck"
_LEVEL = "book"


def _observation(
    as_of: date,
    *,
    residual: float,
    skew: float | None = 0.1,
    regime: float | None = 0.5,
    vov: float | None = 0.2,
) -> ResidualObservation:
    return ResidualObservation(
        as_of_date=as_of,
        portfolio_id=_PORTFOLIO,
        underlying=_UNDERLYING,
        level=_LEVEL,
        realized_pnl=residual + 50.0,
        approx_pnl=50.0,
        residual=residual,
        delta_pnl=20.0,
        gamma_pnl=10.0,
        vega_pnl=10.0,
        theta_pnl=5.0,
        rho_pnl=2.0,
        vanna_pnl=2.0,
        volga_pnl=1.0,
        attribution_version="attribution-1.0.0",
        diagnosis_version="residual-diagnosis-1",
        source_snapshot_ts=datetime(2026, 1, 1, 15, 30, tzinfo=UTC),
        provenance=make_stamp(),
        skew_proxy=skew,
        regime_proxy=regime,
        vol_of_vol_proxy=vov,
    )


# --------------------------------------------------------------------------- #
# 1. NO-LOOK-AHEAD: a future-dated row is invisible to an earlier as-of read
# --------------------------------------------------------------------------- #


def test_as_of_read_hides_a_row_dated_months_in_the_future(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # Three banked-in-the-past rows + ONE row dated far in the future.
    persist_residual_observations(
        store,
        [
            _observation(date(2026, 1, 5), residual=1.0),
            _observation(date(2026, 1, 6), residual=2.0),
            _observation(date(2026, 1, 7), residual=3.0),
            _observation(date(2026, 12, 31), residual=999.0),  # the future
        ],
    )

    as_of = date(2026, 1, 7)
    series = read_residual_series(store, as_of=as_of, portfolio_id=_PORTFOLIO)

    # The future residual (999.0) must NOT leak into an as-of-Jan-7 read.
    assert all(o.as_of_date <= as_of for o in series)
    assert 999.0 not in [o.residual for o in series]
    assert [o.as_of_date for o in series] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
    ]
    # And it is delivered oldest-first (the ordering the walk-forward depends on).
    dates = [o.as_of_date for o in series]
    assert dates == sorted(dates)


# --------------------------------------------------------------------------- #
# 2. Planted coefficient recovery — DIFFERENT relationship, INDEPENDENT solver
# --------------------------------------------------------------------------- #


def _independent_ols(design: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Normal-equations OLS, numpy only — a solver independent of scipy.lstsq.

    coefficients = (XᵀX)⁻¹ Xᵀy. On a noiseless, well-conditioned design this gives
    the exact planted coefficients, so it is a legitimate independent oracle.
    """
    xtx = design.T @ design
    xty = design.T @ target
    return np.linalg.solve(xtx, xty)


def test_estimator_recovers_a_slippage_dominant_relationship() -> None:
    # DIFFERENT planted truth from the implementer (theirs led with skew):
    # residual = 1.5 + (-3.0)*slippage + 0.8*regime, all else zero. slippage dominates.
    rng = np.random.default_rng(101)
    n = 50
    n_factors = len(CANDIDATE_FACTORS)
    matrix = rng.normal(size=(n, n_factors))
    planted = {"slippage_proxy": -3.0, "regime_proxy": 0.8}
    intercept = 1.5
    residuals = np.full(n, intercept)
    for j, name in enumerate(CANDIDATE_FACTORS):
        residuals = residuals + planted.get(name, 0.0) * matrix[:, j]

    config = RegressionConfig(min_oos_days=10)
    diagnosis = diagnose_residual(
        matrix.tolist(), residuals.tolist(), CANDIDATE_FACTORS, config
    )

    assert diagnosis.status is DiagnosisStatus.OK

    # Independent oracle: refit the TRAIN window (first n_train rows) with numpy
    # normal equations and confirm the estimator's coefficients agree exactly.
    n_train = diagnosis.n_train
    train_design = np.hstack([np.ones((n_train, 1)), matrix[:n_train]])
    oracle = _independent_ols(train_design, residuals[:n_train])

    by_factor = {ld.factor: ld.coefficient for ld in diagnosis.loadings}
    for j, name in enumerate(CANDIDATE_FACTORS):
        # oracle[0] is the intercept; oracle[j+1] is factor j.
        assert by_factor[name] == pytest.approx(oracle[j + 1], abs=1e-7)
        # and both match the PLANTED truth.
        assert by_factor[name] == pytest.approx(planted.get(name, 0.0), abs=1e-7)

    # The dominant exposure is the one we planted largest.
    assert diagnosis.dominant_factor == "slippage_proxy"
    assert diagnosis.oos_r_squared == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 3. GATED below the depth floor — no fabricated coefficient
# --------------------------------------------------------------------------- #


def test_below_floor_is_gated_with_dominant_none() -> None:
    config = RegressionConfig(min_oos_days=10)
    floor = config.min_total_rows(len(CANDIDATE_FACTORS))
    # One row below the floor must gate; independently: (6+1)*5 + 10 = 45.
    assert floor == 45

    rng = np.random.default_rng(7)
    matrix = rng.normal(size=(floor - 1, len(CANDIDATE_FACTORS)))
    residuals = matrix[:, 0] * 2.0  # some real relationship, but too few rows

    diagnosis = diagnose_residual(
        matrix.tolist(), residuals.tolist(), CANDIDATE_FACTORS, config
    )
    assert diagnosis.status is DiagnosisStatus.GATED
    assert diagnosis.dominant_factor is None  # no fabricated finding
    assert diagnosis.loadings == ()
    assert diagnosis.oos_r_squared is None
    assert diagnosis.n_observations == floor - 1


# --------------------------------------------------------------------------- #
# 4. Strictly time-ordered split — past trains, future scores
# --------------------------------------------------------------------------- #


def test_split_is_strictly_time_ordered_past_trains_future_scores() -> None:
    # Single-factor design. floor for 1 factor = (1+1)*5 + 10 = 20.
    config = RegressionConfig(min_oos_days=10)
    n = 24  # > 20 floor; n_train = 24 - 10 = 14, n_oos = 10.
    factors = ("skew_proxy",)

    # Plant a clean line residual = 2*x on the TRAIN window; corrupt the OOS tail
    # with a constant offset so that IF the fit had peeked at OOS rows the recovered
    # coefficient/intercept would shift. Because the fit is train-only, the
    # coefficient stays exactly 2.0 and the intercept exactly 0.0.
    rng = np.random.default_rng(55)
    x = rng.normal(size=n)
    residuals = 2.0 * x
    # Corrupt only the trailing OOS rows (indices 14..23) with a big level shift.
    residuals[14:] = residuals[14:] + 1000.0

    diagnosis = diagnose_residual(
        x.reshape(n, 1).tolist(), residuals.tolist(), factors, config
    )

    assert diagnosis.status is DiagnosisStatus.OK
    assert diagnosis.n_train == 14
    assert diagnosis.n_oos == 10
    # Train-only fit: coefficient recovered from the clean train window, untouched
    # by the corrupted future tail. If OOS rows had leaked into the fit, this would
    # not be 2.0.
    by_factor = {ld.factor: ld.coefficient for ld in diagnosis.loadings}
    assert by_factor["skew_proxy"] == pytest.approx(2.0, abs=1e-9)
    # The corrupted future tail tanks OOS R² (the offset is unexplained), proving
    # the tail was SCORED, not trained on.
    assert diagnosis.oos_r_squared is not None
    assert diagnosis.oos_r_squared < 0.0
