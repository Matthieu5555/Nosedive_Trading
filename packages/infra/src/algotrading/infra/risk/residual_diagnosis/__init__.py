"""Residual-diagnosis infrastructure (TARGET §5.2, §7 #10).

Names the unmodeled exposure behind the attribution residual. Two parts:

- **Persistence** — bank the realized-attribution residual as an as-of, contract-typed
  time series alongside the named Taylor terms and the candidate unmodeled-exposure
  covariates observable as-of (``ResidualObservation``). Buildable today; accumulates
  banked depth one day at a time.
- **Gated regression** — a documented out-of-sample / walk-forward regression of the
  residual on the candidate factors that REFUSES to name a dominant exposure until a
  configured minimum banked depth is met. Below threshold it returns an honest
  ``GATED`` status, never a fabricated coefficient. The estimator math is proven on
  synthetic data with a known planted relationship; the live path stays gated because
  canonical depth is currently insufficient (3 trade-dates, no banked residual series,
  no fills/book partitions).
"""

from __future__ import annotations

from .covariates import (
    CovariateReading,
    observation_from_realized,
    read_covariates_as_of,
)
from .diagnose import diagnose_residual_as_of
from .persistence import (
    RegressionDataset,
    build_regression_dataset,
    persist_residual_observations,
    read_residual_series,
)
from .regression import (
    CANDIDATE_FACTORS,
    DIAGNOSIS_VERSION,
    DiagnosisStatus,
    FactorLoading,
    RegressionConfig,
    ResidualDiagnosis,
    diagnose_residual,
)

__all__ = [
    "CANDIDATE_FACTORS",
    "DIAGNOSIS_VERSION",
    "CovariateReading",
    "DiagnosisStatus",
    "FactorLoading",
    "RegressionConfig",
    "RegressionDataset",
    "ResidualDiagnosis",
    "build_regression_dataset",
    "diagnose_residual",
    "diagnose_residual_as_of",
    "observation_from_realized",
    "persist_residual_observations",
    "read_residual_series",
    "read_covariates_as_of",
]
