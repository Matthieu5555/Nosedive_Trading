"""Persist and read back the realized-attribution residual time series.

This is the "raw material" persistence TARGET §5.2 asks for: a contract-typed,
as-of banked series of ``ResidualObservation`` rows that accumulates depth one
trading day at a time. It is fully buildable and testable today, independent of
whether enough days are yet banked to *regress* on.

The read is point-in-time. ``read_residual_series(store, ..., as_of)`` returns
only rows dated on or before ``as_of`` — a past day's diagnosis can never see a
future residual. This mirrors the signal layer's ``end_date=as_of`` no-look-ahead
read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from algotrading.infra.contracts import ResidualObservation
from algotrading.infra.storage import ParquetStore

from .regression import CANDIDATE_FACTORS

_TABLE = "residual_observations"


def persist_residual_observations(
    store: ParquetStore, observations: Sequence[ResidualObservation]
) -> None:
    """Bank residual observations into the as-of time series.

    Append-only: each (as_of_date, portfolio_id, level) row is immutable history.
    Re-banking the same key raises rather than silently overwriting, so the series
    is a faithful append log.
    """

    if not observations:
        return
    store.write(_TABLE, list(observations))


def read_residual_series(
    store: ParquetStore,
    *,
    as_of: date,
    portfolio_id: str | None = None,
    underlying: str | None = None,
    level: str | None = None,
    lookback_start: date | None = None,
) -> tuple[ResidualObservation, ...]:
    """Read the banked residual series as-of ``as_of`` (no look-ahead).

    Returns rows dated on or before ``as_of``, oldest first, optionally filtered
    to one portfolio/level. ``lookback_start`` bounds the window below; omit it to
    read the full banked history up to ``as_of``.
    """

    rows = store.read(
        _TABLE,
        underlying=underlying,
        start_date=lookback_start,
        end_date=as_of,
    )
    selected = [
        row
        for row in rows
        if row.as_of_date <= as_of
        and (portfolio_id is None or row.portfolio_id == portfolio_id)
        and (level is None or row.level == level)
    ]
    selected.sort(key=lambda row: (row.as_of_date, row.portfolio_id, row.level))
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class RegressionDataset:
    """A complete-case design built from banked residual observations.

    ``factor_matrix`` is row-aligned with ``residuals`` and time-ordered. Only the
    candidate factors present (non-None, finite) on *every* selected row are kept
    as columns, and only rows with all of those present become observations — a
    missing covariate is dropped, never imputed.
    """

    factor_matrix: np.ndarray
    residuals: np.ndarray
    factors: tuple[str, ...]
    n_dropped_rows: int


def build_regression_dataset(
    observations: Sequence[ResidualObservation],
    *,
    candidate_factors: Sequence[str] = CANDIDATE_FACTORS,
) -> RegressionDataset:
    """Assemble a complete-case (factor_matrix, residuals) design.

    A factor is kept only if it is observed (non-None, finite) on every row; a row
    is kept only if all kept factors are observed on it. This is honest
    complete-case handling: gaps shrink the usable design rather than being filled
    with fabricated values. The resulting depth is what the gate then judges.
    """

    rows = list(observations)

    def _present(value: float | None) -> bool:
        return value is not None and np.isfinite(value)

    kept_factors = tuple(
        name
        for name in candidate_factors
        if rows and all(_present(getattr(row, name)) for row in rows)
    )

    if not kept_factors:
        return RegressionDataset(
            factor_matrix=np.empty((0, 0), dtype=float),
            residuals=np.empty((0,), dtype=float),
            factors=(),
            n_dropped_rows=len(rows),
        )

    complete_rows = [
        row for row in rows if all(_present(getattr(row, name)) for name in kept_factors)
    ]
    n_dropped = len(rows) - len(complete_rows)

    factor_matrix = np.array(
        [[float(getattr(row, name)) for name in kept_factors] for row in complete_rows],
        dtype=float,
    ).reshape(len(complete_rows), len(kept_factors))
    residuals = np.array([row.residual for row in complete_rows], dtype=float)

    return RegressionDataset(
        factor_matrix=factor_matrix,
        residuals=residuals,
        factors=kept_factors,
        n_dropped_rows=n_dropped,
    )
