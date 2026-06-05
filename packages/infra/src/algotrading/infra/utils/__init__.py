"""algotrading.infra.utils — shared pure numerical primitives for the analytics core.

Two small, dependency-light modules every analytics module may lean on:

* :mod:`~algotrading.infra.utils.daycount` — the single day-count source. Convert a date
  interval to a year fraction under an explicit convention; :class:`YearFraction` keeps the
  number bound to the convention that produced it.
* :mod:`~algotrading.infra.utils.robust` — median-based order statistics (MAD, MAD z-score,
  Theil-Sen line, residual outlier flagging, weighted median) that resist a few bad quotes.

Pure functions only: no I/O, no clock, no RNG.
"""

from __future__ import annotations

from .daycount import DayCountConvention, YearFraction, year_fraction
from .robust import (
    MAD_SCALE,
    median_absolute_deviation,
    outlier_flags,
    robust_zscore_vs_baseline,
    robust_zscores,
    theil_sen_line,
    weighted_median,
)

__all__ = [
    "MAD_SCALE",
    "DayCountConvention",
    "YearFraction",
    "median_absolute_deviation",
    "outlier_flags",
    "robust_zscore_vs_baseline",
    "robust_zscores",
    "theil_sen_line",
    "weighted_median",
    "year_fraction",
]
