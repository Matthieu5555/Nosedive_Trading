"""Per-currency risk-free rate-curve `r(T)` ingest, evaluation, Rho basis, and spread QC (ADR 0054).

The external curve is the *risk* rate (Rho's basis); the parity-implied rate stays the
*pricing-consistency* rate and is never displaced. The two are kept separate by design.
"""

from __future__ import annotations

from .conventions import (
    COMPOUNDINGS,
    DAY_COUNTS,
    RateConventionError,
    to_continuous_act365,
)
from .curve import RateCurve, RateCurveError, RatePillar
from .ecb_source import (
    SERIES_BY_INSTRUMENT,
    EcbRateSource,
    EcbRateSourceError,
    EcbSeries,
    FetchedLevels,
    ingest_ecb_rates,
    parse_observation_csv,
)
from .ingest import (
    CANONICAL_DAY_COUNT,
    RATES_VERSION,
    RateIngestError,
    build_rate_points,
    curve_from_points,
)
from .rho import DEFAULT_RATE_BUMP, ExternalRhoError, external_curve_rho
from .spread import (
    DISPOSITION_FAIL,
    DISPOSITION_WARN,
    QC_FAIL,
    QC_OK,
    QC_WARN,
    ImpliedRiskfreeSpread,
    SpreadDiagnosticError,
    implied_riskfree_spread,
)

__all__ = [
    "CANONICAL_DAY_COUNT",
    "COMPOUNDINGS",
    "DAY_COUNTS",
    "DEFAULT_RATE_BUMP",
    "DISPOSITION_FAIL",
    "DISPOSITION_WARN",
    "QC_FAIL",
    "QC_OK",
    "QC_WARN",
    "RATES_VERSION",
    "SERIES_BY_INSTRUMENT",
    "EcbRateSource",
    "EcbRateSourceError",
    "EcbSeries",
    "ExternalRhoError",
    "FetchedLevels",
    "ImpliedRiskfreeSpread",
    "RateConventionError",
    "RateCurve",
    "RateCurveError",
    "RateIngestError",
    "RatePillar",
    "SpreadDiagnosticError",
    "build_rate_points",
    "curve_from_points",
    "external_curve_rho",
    "implied_riskfree_spread",
    "ingest_ecb_rates",
    "parse_observation_csv",
    "to_continuous_act365",
]
