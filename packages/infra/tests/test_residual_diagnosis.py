"""Tests for the residual-diagnosis infrastructure (persistence + gated estimator).

Three things are proven here:

1. The residual time series round-trips through a TEMP store and reads back
   as-of with no look-ahead (a past day never sees a future residual).
2. The walk-forward regression estimator recovers a KNOWN planted coefficient on
   a synthetic dataset within tolerance (the estimator is correct).
3. The live path returns the honest "gated — insufficient depth" status against a
   shallow store — NOT a fabricated coefficient.

No canonical data is touched; every write goes to ``tmp_path``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from algotrading.infra.contracts import ResidualObservation
from algotrading.infra.risk.residual_diagnosis import (
    CANDIDATE_FACTORS,
    CovariateReading,
    DiagnosisStatus,
    RegressionConfig,
    build_regression_dataset,
    diagnose_residual,
    diagnose_residual_as_of,
    observation_from_realized,
    persist_residual_observations,
    read_covariates_as_of,
    read_residual_series,
)
from algotrading.infra.storage import ParquetStore

from .fixtures.records import make_record, make_stamp

_UNDERLYING = "SX5E"
_PORTFOLIO = "book-core"
_LEVEL = "book"


def _observation(
    as_of: date,
    *,
    residual: float,
    skew: float | None = 0.1,
    regime: float | None = 0.5,
    vov: float | None = 0.2,
    portfolio_id: str = _PORTFOLIO,
    level: str = _LEVEL,
) -> ResidualObservation:
    return ResidualObservation(
        as_of_date=as_of,
        portfolio_id=portfolio_id,
        underlying=_UNDERLYING,
        level=level,
        realized_pnl=residual + 100.0,
        approx_pnl=100.0,
        residual=residual,
        delta_pnl=40.0,
        gamma_pnl=20.0,
        vega_pnl=20.0,
        theta_pnl=10.0,
        rho_pnl=5.0,
        vanna_pnl=3.0,
        volga_pnl=2.0,
        attribution_version="attribution-1.0.0",
        diagnosis_version="residual-diagnosis-1",
        source_snapshot_ts=datetime(2026, 6, 15, 15, 30, tzinfo=UTC),
        provenance=make_stamp(),
        skew_proxy=skew,
        regime_proxy=regime,
        vol_of_vol_proxy=vov,
    )


# --------------------------------------------------------------------------- #
# 1. Persistence round-trip + no-look-ahead read
# --------------------------------------------------------------------------- #


def test_residual_series_round_trips_through_a_temp_store(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    obs = [
        _observation(date(2026, 6, 15), residual=1.0),
        _observation(date(2026, 6, 16), residual=2.0),
        _observation(date(2026, 6, 17), residual=3.0),
    ]
    persist_residual_observations(store, obs)

    read_back = read_residual_series(store, as_of=date(2026, 6, 17), portfolio_id=_PORTFOLIO)

    assert [o.as_of_date for o in read_back] == [
        date(2026, 6, 15),
        date(2026, 6, 16),
        date(2026, 6, 17),
    ]
    assert [o.residual for o in read_back] == [1.0, 2.0, 3.0]


def test_as_of_read_excludes_future_rows(tmp_path: Path) -> None:
    # No look-ahead: a read as-of the 16th must not surface the 17th's residual.
    store = ParquetStore(tmp_path)
    persist_residual_observations(
        store,
        [
            _observation(date(2026, 6, 15), residual=1.0),
            _observation(date(2026, 6, 16), residual=2.0),
            _observation(date(2026, 6, 17), residual=99.0),
        ],
    )

    as_of_16 = read_residual_series(store, as_of=date(2026, 6, 16), portfolio_id=_PORTFOLIO)

    assert [o.as_of_date for o in as_of_16] == [date(2026, 6, 15), date(2026, 6, 16)]
    assert all(o.as_of_date <= date(2026, 6, 16) for o in as_of_16)
    assert 99.0 not in [o.residual for o in as_of_16]


def test_series_is_append_only_immutable_history(tmp_path: Path) -> None:
    from algotrading.infra.storage import AppendOnlyViolation

    store = ParquetStore(tmp_path)
    persist_residual_observations(store, [_observation(date(2026, 6, 15), residual=1.0)])
    with pytest.raises(AppendOnlyViolation):
        persist_residual_observations(
            store, [_observation(date(2026, 6, 15), residual=2.0)]
        )


def test_read_filters_by_portfolio_and_level(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    persist_residual_observations(
        store,
        [
            _observation(date(2026, 6, 15), residual=1.0, portfolio_id="book-a", level="book"),
            _observation(date(2026, 6, 15), residual=2.0, portfolio_id="book-b", level="book"),
            _observation(
                date(2026, 6, 15), residual=3.0, portfolio_id="book-a", level="strategy"
            ),
        ],
    )

    only_a_book = read_residual_series(
        store, as_of=date(2026, 6, 15), portfolio_id="book-a", level="book"
    )
    assert [o.residual for o in only_a_book] == [1.0]


# --------------------------------------------------------------------------- #
# 2. The estimator recovers a KNOWN planted coefficient (synthetic)
# --------------------------------------------------------------------------- #


def _planted_dataset(
    n: int,
    coefficients: dict[str, float],
    intercept: float,
    *,
    noise_sd: float = 0.0,
    seed: int = 7,
) -> tuple[list[list[float]], list[float]]:
    """Build a synthetic design with a KNOWN linear relationship.

    residual = intercept + sum(coef[f] * factor_f) + noise. Expected coefficients
    are derived independently from this construction, not from the estimator.
    """

    rng = np.random.default_rng(seed)
    n_factors = len(CANDIDATE_FACTORS)
    factor_matrix = rng.normal(size=(n, n_factors))
    residuals = np.full(n, intercept, dtype=float)
    for idx, name in enumerate(CANDIDATE_FACTORS):
        residuals += coefficients.get(name, 0.0) * factor_matrix[:, idx]
    if noise_sd > 0.0:
        residuals += rng.normal(scale=noise_sd, size=n)
    return factor_matrix.tolist(), residuals.tolist()


def test_estimator_recovers_planted_coefficients_noise_free() -> None:
    # Plant a clean linear relationship; with no noise OLS must recover it exactly.
    planted = {"skew_proxy": 2.5, "regime_proxy": -1.0, "vol_of_vol_proxy": 0.75}
    intercept = 3.0
    factor_matrix, residuals = _planted_dataset(60, planted, intercept, noise_sd=0.0)

    config = RegressionConfig(min_oos_days=10)
    diagnosis = diagnose_residual(factor_matrix, residuals, CANDIDATE_FACTORS, config)

    assert diagnosis.status is DiagnosisStatus.OK
    by_factor = {loading.factor: loading.coefficient for loading in diagnosis.loadings}
    for name in CANDIDATE_FACTORS:
        assert by_factor[name] == pytest.approx(planted.get(name, 0.0), abs=1e-6)
    # A noise-free planted relationship is explained out-of-sample.
    assert diagnosis.oos_r_squared == pytest.approx(1.0, abs=1e-6)


def test_estimator_recovers_planted_coefficients_with_noise() -> None:
    # With modest noise the recovered coefficients still land near the planted truth.
    planted = {"skew_proxy": 2.0, "liquidity_proxy": -1.5}
    factor_matrix, residuals = _planted_dataset(
        400, planted, intercept=0.5, noise_sd=0.05, seed=11
    )

    config = RegressionConfig(min_oos_days=80)
    diagnosis = diagnose_residual(factor_matrix, residuals, CANDIDATE_FACTORS, config)

    assert diagnosis.status is DiagnosisStatus.OK
    by_factor = {loading.factor: loading.coefficient for loading in diagnosis.loadings}
    assert by_factor["skew_proxy"] == pytest.approx(2.0, abs=0.05)
    assert by_factor["liquidity_proxy"] == pytest.approx(-1.5, abs=0.05)
    assert by_factor["regime_proxy"] == pytest.approx(0.0, abs=0.05)


def test_estimator_names_the_dominant_exposure() -> None:
    # skew has by far the largest loading -> it must be named the dominant exposure.
    planted = {"skew_proxy": 5.0, "regime_proxy": 0.2, "vol_of_vol_proxy": 0.1}
    factor_matrix, residuals = _planted_dataset(
        200, planted, intercept=0.0, noise_sd=0.02, seed=3
    )

    config = RegressionConfig(min_oos_days=40)
    diagnosis = diagnose_residual(factor_matrix, residuals, CANDIDATE_FACTORS, config)

    assert diagnosis.status is DiagnosisStatus.OK
    assert diagnosis.dominant_factor == "skew_proxy"


# --------------------------------------------------------------------------- #
# 3. The gate refuses on shallow depth (no fabrication)
# --------------------------------------------------------------------------- #


def test_estimator_is_gated_below_minimum_depth() -> None:
    # 3 rows (mirrors the 3 trade-dates on disk) is far below the floor -> gated.
    factor_matrix, residuals = _planted_dataset(3, {"skew_proxy": 2.0}, intercept=0.0)
    config = RegressionConfig()
    diagnosis = diagnose_residual(factor_matrix, residuals, CANDIDATE_FACTORS, config)

    assert diagnosis.status is DiagnosisStatus.GATED
    assert diagnosis.dominant_factor is None
    assert diagnosis.loadings == ()
    assert diagnosis.oos_r_squared is None
    assert "insufficient banked depth" in diagnosis.reason


def test_gate_threshold_is_exactly_at_the_boundary() -> None:
    config = RegressionConfig(min_oos_days=10)
    floor = config.min_total_rows(len(CANDIDATE_FACTORS))

    fm_below, r_below = _planted_dataset(floor - 1, {"skew_proxy": 1.0}, intercept=0.0)
    fm_at, r_at = _planted_dataset(floor, {"skew_proxy": 1.0}, intercept=0.0)

    assert diagnose_residual(fm_below, r_below, CANDIDATE_FACTORS, config).status is (
        DiagnosisStatus.GATED
    )
    assert diagnose_residual(fm_at, r_at, CANDIDATE_FACTORS, config).status is (
        DiagnosisStatus.OK
    )


# --------------------------------------------------------------------------- #
# Complete-case dataset assembly (missing covariate is dropped, never imputed)
# --------------------------------------------------------------------------- #


def test_dataset_drops_factor_absent_on_any_row() -> None:
    # vol_of_vol is None on the middle row -> it is dropped entirely as a column.
    obs = [
        _observation(date(2026, 6, 15), residual=1.0, vov=0.2),
        _observation(date(2026, 6, 16), residual=2.0, vov=None),
        _observation(date(2026, 6, 17), residual=3.0, vov=0.3),
    ]
    dataset = build_regression_dataset(obs)

    assert "vol_of_vol_proxy" not in dataset.factors
    assert "skew_proxy" in dataset.factors
    assert "regime_proxy" in dataset.factors
    assert dataset.factor_matrix.shape[0] == 3


def test_dataset_empty_when_no_covariate_is_ever_present() -> None:
    obs = [
        _observation(date(2026, 6, 15), residual=1.0, skew=None, regime=None, vov=None),
    ]
    dataset = build_regression_dataset(obs)
    assert dataset.factors == ()
    assert dataset.factor_matrix.shape == (0, 0)


# --------------------------------------------------------------------------- #
# Live path over the real (shallow) store -> honest gated status, no fabrication
# --------------------------------------------------------------------------- #


def test_live_path_is_gated_against_a_shallow_store(tmp_path: Path) -> None:
    # Three banked days (the depth that exists today) -> the live verdict is GATED.
    store = ParquetStore(tmp_path)
    persist_residual_observations(
        store,
        [_observation(date(2026, 6, 15) + timedelta(days=i), residual=float(i)) for i in range(3)],
    )

    diagnosis = diagnose_residual_as_of(
        store,
        as_of=date(2026, 6, 17),
        portfolio_id=_PORTFOLIO,
        level=_LEVEL,
        underlying=_UNDERLYING,
    )

    assert diagnosis.status is DiagnosisStatus.GATED
    assert diagnosis.dominant_factor is None
    assert "insufficient banked depth" in diagnosis.reason


def test_live_path_names_exposure_once_depth_is_banked(tmp_path: Path) -> None:
    # Bank a deep, planted series; the live path then names the dominant exposure.
    store = ParquetStore(tmp_path)
    rng = np.random.default_rng(5)
    start = date(2026, 1, 1)
    obs: list[ResidualObservation] = []
    for i in range(60):
        skew = float(rng.normal())
        regime = float(rng.normal())
        vov = float(rng.normal())
        residual = 4.0 * skew + 0.1 * regime + 0.05 * vov
        obs.append(
            _observation(
                start + timedelta(days=i),
                residual=residual,
                skew=skew,
                regime=regime,
                vov=vov,
            )
        )
    persist_residual_observations(store, obs)

    diagnosis = diagnose_residual_as_of(
        store,
        as_of=start + timedelta(days=59),
        portfolio_id=_PORTFOLIO,
        level=_LEVEL,
        underlying=_UNDERLYING,
        config=RegressionConfig(min_oos_days=12),
    )

    assert diagnosis.status is DiagnosisStatus.OK
    assert diagnosis.dominant_factor == "skew_proxy"


# --------------------------------------------------------------------------- #
# As-of covariate readers (skew from surfaces, regime from signals) — no look-ahead
# --------------------------------------------------------------------------- #


def _surface(snapshot: datetime, *, rho: float, maturity: float) -> object:
    return make_record(
        "surface_parameters",
        snapshot_ts=snapshot,
        underlying=_UNDERLYING,
        maturity_years=maturity,
        svi_rho=rho,
        source_snapshot_ts=snapshot,
    )


def _iv_rank_signal(snapshot: datetime, *, value: float) -> object:
    return make_record(
        "strategy_signals",
        snapshot_ts=snapshot,
        underlying=_UNDERLYING,
        signal_kind="iv_rank",
        subject=_UNDERLYING,
        tenor_label="3m",
        value=value,
        source_snapshot_ts=snapshot,
    )


def test_covariate_reader_returns_none_when_nothing_is_banked(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    reading = read_covariates_as_of(store, underlying=_UNDERLYING, as_of=date(2026, 6, 17))
    # Honest absence, never a fabricated zero.
    assert reading.skew_proxy is None
    assert reading.regime_proxy is None
    assert reading.vol_of_vol_proxy is None
    assert reading.liquidity_proxy is None


def test_skew_reader_picks_front_tenor_rho_as_of(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    snapshot = datetime(2026, 6, 16, 15, 30, tzinfo=UTC)
    store.write(
        "surface_parameters",
        [
            _surface(snapshot, rho=-0.5, maturity=0.08),  # front tenor
            _surface(snapshot, rho=-0.2, maturity=0.50),  # back tenor
        ],
    )

    reading = read_covariates_as_of(store, underlying=_UNDERLYING, as_of=date(2026, 6, 17))
    assert reading.skew_proxy == pytest.approx(-0.5)


def test_covariate_readers_do_not_look_ahead(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    past = datetime(2026, 6, 15, 15, 30, tzinfo=UTC)
    future = datetime(2026, 6, 18, 15, 30, tzinfo=UTC)
    store.write(
        "surface_parameters",
        [_surface(past, rho=-0.3, maturity=0.08), _surface(future, rho=-0.9, maturity=0.08)],
    )

    # As-of the 16th: only the past surface is visible; the future rho=-0.9 is hidden.
    reading = read_covariates_as_of(store, underlying=_UNDERLYING, as_of=date(2026, 6, 16))
    assert reading.skew_proxy == pytest.approx(-0.3)


def test_regime_and_vov_from_banked_signals(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write(
        "strategy_signals",
        [
            _iv_rank_signal(datetime(2026, 6, 15, 15, 30, tzinfo=UTC), value=0.4),
            _iv_rank_signal(datetime(2026, 6, 16, 15, 30, tzinfo=UTC), value=0.6),
        ],
    )

    reading = read_covariates_as_of(store, underlying=_UNDERLYING, as_of=date(2026, 6, 17))
    # Regime = most recent IV-rank reading.
    assert reading.regime_proxy == pytest.approx(0.6)
    # Vol-of-vol = sample std of the two readings.
    assert reading.vol_of_vol_proxy == pytest.approx(np.std([0.4, 0.6], ddof=1))


# --------------------------------------------------------------------------- #
# observation_from_realized wiring
# --------------------------------------------------------------------------- #


def test_observation_from_realized_carries_terms_and_covariates() -> None:
    from algotrading.infra.risk.attribution import RealizedBookAttribution
    from algotrading.infra.risk.config import AttributionConfig
    from algotrading.infra.risk.scenarios import TaylorTerms

    terms = TaylorTerms(
        delta_pnl=40.0,
        gamma_pnl=20.0,
        vega_pnl=20.0,
        theta_pnl=10.0,
        rho_pnl=5.0,
        vanna_pnl=3.0,
        volga_pnl=2.0,
    )
    attribution = RealizedBookAttribution(
        portfolio_id="book-core",
        terms=terms,
        full_reprice_pnl=110.0,
        residual=10.0,
        within_tolerance=False,
        diagnostic="",
        lines=(),
        config=AttributionConfig.defaults(),
    )
    reading = CovariateReading(skew_proxy=0.3, regime_proxy=0.6)

    obs = observation_from_realized(
        attribution,
        reading,
        as_of_date=date(2026, 6, 17),
        underlying=_UNDERLYING,
        level="book",
        source_snapshot_ts=datetime(2026, 6, 17, 15, 30, tzinfo=UTC),
        provenance=make_stamp(),
    )

    assert obs.residual == 10.0
    assert obs.realized_pnl == 110.0
    assert obs.delta_pnl == 40.0
    assert obs.skew_proxy == 0.3
    assert obs.regime_proxy == 0.6
    # Covariates with no as-of reading stay honestly absent.
    assert obs.vanna_proxy is None
    assert obs.liquidity_proxy is None
