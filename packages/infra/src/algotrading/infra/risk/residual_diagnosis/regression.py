"""Gated walk-forward regression of the attribution residual on candidate factors.

TARGET §5.2 asks us to *name* the unmodeled exposure behind the attribution
residual: regress the banked realized residual against candidate covariates
(skew/vanna dynamics, regime/vol-of-vol, liquidity/slippage) and report which one
the book silently carries. §6 raises the bar for this one step from deterministic
decomposition to **statistical inference**: it must be out-of-sample / walk-forward,
with no data-snooping.

The hard reality on disk today is shallow: a handful of banked residual days is
*exactly* the thin, friendly sample §6 forbids regressing on. So this estimator is
**gated**. Below a configured minimum out-of-sample depth it refuses to produce a
"dominant exposure" verdict and returns an honest ``gated`` status instead. The
regression mathematics below is real and unit-tested against a synthetic dataset
with a known planted coefficient — but the live verdict stays gated until the bank
is deep enough for an out-of-sample split to mean anything.

We lean on ``scipy.linalg.lstsq`` for the least-squares solve rather than
hand-rolling the normal equations.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from scipy import linalg

DIAGNOSIS_VERSION = "residual-diagnosis-1"

# The candidate unmodeled-exposure covariates, named once. Order is the column
# order of the design matrix and must match ResidualObservation's proxy fields.
CANDIDATE_FACTORS: tuple[str, ...] = (
    "skew_proxy",
    "vanna_proxy",
    "regime_proxy",
    "vol_of_vol_proxy",
    "liquidity_proxy",
    "slippage_proxy",
)


class DiagnosisStatus(StrEnum):
    """Whether the regression produced a usable verdict or refused."""

    OK = "ok"
    GATED = "gated"


@dataclass(frozen=True, slots=True)
class RegressionConfig:
    """Knobs for the gated walk-forward residual regression.

    ``min_oos_days`` is the gate. It is the minimum number of *out-of-sample*
    observations the walk-forward must score before any "dominant exposure"
    verdict is allowed. Below it, the regression returns a ``GATED`` status.

    Default reasoning (documented, defensible — not a magic number):
    a residual regression needs (a) enough training rows to identify the
    coefficients of all six candidate factors without overfitting, and (b) a
    held-out tail long enough that out-of-sample R² is not a single lucky point.
    With six candidates, a rule-of-thumb of ~5 training rows per estimated
    coefficient puts a sane training floor near 30, and we want at least ~10
    out-of-sample days on top so the walk-forward score is more than noise. That
    is ~40 banked days total — comfortably more than a "week-plus" and far beyond
    the 3 trade-dates currently on disk. We encode the *out-of-sample* floor
    (10) as the gate and derive the training floor from the factor count, so the
    threshold scales if the candidate set grows.
    """

    min_oos_days: int = 10
    min_train_rows_per_factor: int = 5
    ridge_lambda: float = 1e-8

    def __post_init__(self) -> None:
        if self.min_oos_days < 1:
            raise ValueError("min_oos_days must be at least 1")
        if self.min_train_rows_per_factor < 1:
            raise ValueError("min_train_rows_per_factor must be at least 1")
        if self.ridge_lambda < 0.0:
            raise ValueError("ridge_lambda must be non-negative")

    def min_train_rows(self, n_factors: int) -> int:
        """Training-window floor derived from the number of candidate factors.

        +1 for the intercept column; ``min_train_rows_per_factor`` rows per
        coefficient so the fit is identified rather than interpolated.
        """

        return (n_factors + 1) * self.min_train_rows_per_factor

    def min_total_rows(self, n_factors: int) -> int:
        """Total banked-depth floor: a full training window plus the OOS tail."""

        return self.min_train_rows(n_factors) + self.min_oos_days


@dataclass(frozen=True, slots=True)
class FactorLoading:
    """One candidate factor's fitted contribution to the residual."""

    factor: str
    coefficient: float
    # Share of explained out-of-sample variation attributable to this factor,
    # in [0, 1]; the dominant exposure is the largest.
    contribution_share: float


@dataclass(frozen=True, slots=True)
class ResidualDiagnosis:
    """The outcome of a gated walk-forward residual regression.

    When ``status`` is ``GATED`` the loadings are empty and ``dominant_factor`` is
    ``None``: the bank is too shallow to name an exposure without data-snooping,
    and ``reason`` says so precisely. When ``status`` is ``OK`` the loadings are
    populated and ``dominant_factor`` names the unmodeled exposure the book
    silently carries.
    """

    status: DiagnosisStatus
    n_observations: int
    n_train: int
    n_oos: int
    factors: tuple[str, ...]
    reason: str
    loadings: tuple[FactorLoading, ...] = field(default_factory=tuple)
    dominant_factor: str | None = None
    oos_r_squared: float | None = None
    diagnosis_version: str = DIAGNOSIS_VERSION


@dataclass(frozen=True, slots=True)
class _FitResult:
    coefficients: np.ndarray  # intercept first, then one per factor
    oos_r_squared: float
    oos_predictions: np.ndarray
    oos_targets: np.ndarray


def _ols(design: np.ndarray, target: np.ndarray, ridge_lambda: float) -> np.ndarray:
    """Least-squares solve with a tiny ridge for numerical conditioning.

    Leans on ``scipy.linalg.lstsq``; the ridge term is stacked as extra rows so
    we still go through the library solver rather than forming X'X by hand.
    """

    n_features = design.shape[1]
    if ridge_lambda > 0.0:
        penalty = math.sqrt(ridge_lambda) * np.eye(n_features)
        augmented_design = np.vstack([design, penalty])
        augmented_target = np.concatenate([target, np.zeros(n_features)])
    else:
        augmented_design = design
        augmented_target = target
    coefficients, _, _, _ = linalg.lstsq(augmented_design, augmented_target)
    return np.asarray(coefficients, dtype=float)


def _design_matrix(factor_columns: np.ndarray) -> np.ndarray:
    """Prepend an intercept column to the factor columns."""

    n_rows = factor_columns.shape[0]
    intercept = np.ones((n_rows, 1), dtype=float)
    return np.hstack([intercept, factor_columns])


def _walk_forward_fit(
    factor_matrix: np.ndarray,
    residuals: np.ndarray,
    n_train: int,
    ridge_lambda: float,
) -> _FitResult:
    """Fit on the first ``n_train`` rows, score on the held-out tail.

    A single expanding-origin split: train on the leading window (the past),
    predict the trailing window (the future). No future row enters the fit, so
    the out-of-sample score cannot be inflated by look-ahead.
    """

    train_factors = factor_matrix[:n_train]
    train_targets = residuals[:n_train]
    oos_factors = factor_matrix[n_train:]
    oos_targets = residuals[n_train:]

    train_design = _design_matrix(train_factors)
    coefficients = _ols(train_design, train_targets, ridge_lambda)

    oos_design = _design_matrix(oos_factors)
    oos_predictions = oos_design @ coefficients

    target_mean = float(np.mean(oos_targets))
    ss_tot = float(np.sum((oos_targets - target_mean) ** 2))
    ss_res = float(np.sum((oos_targets - oos_predictions) ** 2))
    oos_r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0

    return _FitResult(
        coefficients=coefficients,
        oos_r_squared=oos_r_squared,
        oos_predictions=oos_predictions,
        oos_targets=oos_targets,
    )


def _contribution_shares(
    coefficients: np.ndarray, oos_factors: np.ndarray
) -> tuple[float, ...]:
    """Share of out-of-sample explained variation per factor.

    For each factor we measure the spread of its fitted contribution
    (coefficient × factor values) over the out-of-sample window and normalise to
    a [0, 1] share. The intercept (coefficients[0]) is excluded — it is the
    average level, not an exposure.
    """

    factor_coeffs = coefficients[1:]
    contributions = oos_factors * factor_coeffs  # (n_oos, n_factors)
    magnitudes = np.std(contributions, axis=0)
    total = float(np.sum(magnitudes))
    if total <= 0.0:
        n = magnitudes.shape[0]
        return tuple(0.0 for _ in range(n))
    return tuple(float(m / total) for m in magnitudes)


def diagnose_residual(
    factor_matrix: Sequence[Sequence[float]],
    residuals: Sequence[float],
    factors: Sequence[str],
    config: RegressionConfig,
) -> ResidualDiagnosis:
    """Run the gated walk-forward residual regression.

    ``factor_matrix`` is row-aligned with ``residuals`` and time-ordered
    (oldest first). Rows with any missing (non-finite) factor must be dropped by
    the caller *before* this call — a missing covariate is honestly absent, never
    imputed here.

    Returns a ``ResidualDiagnosis``. If banked depth is below the configured
    floor, the status is ``GATED`` and no coefficients are reported — that is the
    honest refusal §6 mandates, not a fabricated finding.
    """

    factors = tuple(factors)
    n_factors = len(factors)
    if n_factors == 0:
        raise ValueError("at least one candidate factor is required")

    matrix = np.asarray(factor_matrix, dtype=float)
    target = np.asarray(residuals, dtype=float)
    n_obs = matrix.shape[0]
    if matrix.ndim != 2 or matrix.shape[1] != n_factors:
        raise ValueError("factor_matrix shape must be (n_observations, n_factors)")
    if target.shape[0] != n_obs:
        raise ValueError("residuals must be row-aligned with factor_matrix")
    if not np.all(np.isfinite(matrix)) or not np.all(np.isfinite(target)):
        raise ValueError("factor_matrix and residuals must be finite (caller drops gaps)")

    min_train = config.min_train_rows(n_factors)
    min_total = config.min_total_rows(n_factors)
    if n_obs < min_total:
        return ResidualDiagnosis(
            status=DiagnosisStatus.GATED,
            n_observations=n_obs,
            n_train=0,
            n_oos=0,
            factors=factors,
            reason=(
                f"insufficient banked depth — gated: have {n_obs} complete residual "
                f"observation(s), need >= {min_total} "
                f"({min_train} train + {config.min_oos_days} out-of-sample) before an "
                "out-of-sample residual regression is meaningful. Regressing a thinner, "
                "friendly sample is the data-snooping the quant-guard bar forbids."
            ),
        )

    n_train = n_obs - config.min_oos_days
    n_oos = config.min_oos_days
    fit = _walk_forward_fit(matrix, target, n_train, config.ridge_lambda)

    oos_factors = matrix[n_train:]
    shares = _contribution_shares(fit.coefficients, oos_factors)
    loadings = tuple(
        FactorLoading(
            factor=name,
            coefficient=float(fit.coefficients[i + 1]),
            contribution_share=shares[i],
        )
        for i, name in enumerate(factors)
    )
    dominant = max(loadings, key=lambda loading: loading.contribution_share)

    return ResidualDiagnosis(
        status=DiagnosisStatus.OK,
        n_observations=n_obs,
        n_train=n_train,
        n_oos=n_oos,
        factors=factors,
        reason="out-of-sample walk-forward fit over banked residual depth",
        loadings=loadings,
        dominant_factor=dominant.factor,
        oos_r_squared=fit.oos_r_squared,
    )
