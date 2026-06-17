"""Live path: diagnose the residual from the banked series, as-of, gated.

This ties persistence + the gated estimator together over the real store. It
reads the banked residual series as-of a date (no look-ahead), builds a
complete-case design, and runs the gated walk-forward regression. Against the
shallow store that exists today it returns the honest ``GATED`` status — it does
*not* fabricate a coefficient from a thin sample.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.storage import ParquetStore

from .persistence import build_regression_dataset, read_residual_series
from .regression import (
    DiagnosisStatus,
    RegressionConfig,
    ResidualDiagnosis,
    diagnose_residual,
)


def diagnose_residual_as_of(
    store: ParquetStore,
    *,
    as_of: date,
    portfolio_id: str,
    level: str,
    underlying: str | None = None,
    lookback_start: date | None = None,
    config: RegressionConfig | None = None,
) -> ResidualDiagnosis:
    """Diagnose the unmodeled exposure behind the residual, as-of ``as_of``.

    Reads only residual rows dated on or before ``as_of`` for the given
    book/strategy, drops rows with missing covariates (complete-case), and runs
    the gated walk-forward regression. Returns a ``GATED`` diagnosis when banked
    depth is below the configured floor — the honest refusal, not a fabricated
    finding.
    """

    cfg = config if config is not None else RegressionConfig()
    series = read_residual_series(
        store,
        as_of=as_of,
        portfolio_id=portfolio_id,
        underlying=underlying,
        level=level,
        lookback_start=lookback_start,
    )
    dataset = build_regression_dataset(series)

    if not dataset.factors:
        return ResidualDiagnosis(
            status=DiagnosisStatus.GATED,
            n_observations=len(series),
            n_train=0,
            n_oos=0,
            factors=(),
            reason=(
                f"insufficient banked depth — gated: {len(series)} residual row(s) banked "
                f"as-of {as_of.isoformat()}, but no candidate covariate is present on every "
                "row, so there is nothing to regress on. A missing covariate is dropped, "
                "never imputed."
            ),
        )

    return diagnose_residual(
        dataset.factor_matrix.tolist(),
        dataset.residuals.tolist(),
        dataset.factors,
        cfg,
    )
