"""Assemble the as-of candidate covariates for a residual observation.

Each ``ResidualObservation`` banks, alongside the realized residual, the candidate
unmodeled-exposure covariates that were observable *as-of* its trade date:

- **skew** — from the per-side / SVI surface parameters (``svi_rho`` is the SVI
  skew parameter); read as-of from stored ``surface_parameters``.
- **regime** and **vol-of-vol** — from the signal layer's banked ``strategy_signals``
  (IV-rank as a regime proxy; the spread of recent IV-rank readings as a
  vol-of-vol proxy); read as-of from stored signals.

Every reader is point-in-time: it reads only rows dated on or before ``as_of`` and
returns ``None`` when the covariate is unavailable. A ``None`` is honest absence —
it flows into ``ResidualObservation`` as ``None`` and is dropped (never imputed) by
the regression's complete-case assembly. Liquidity/slippage proxies are part of the
contract for when fills depth lands; they default to ``None`` here because no
fills/book partitions exist yet on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import ResidualObservation
from algotrading.infra.storage import ParquetStore

from ..attribution import RealizedBookAttribution
from .regression import DIAGNOSIS_VERSION

_SURFACE_TABLE = "surface_parameters"
_SIGNALS_TABLE = "strategy_signals"
_SIGNAL_KIND_IV_RANK = "iv_rank"


@dataclass(frozen=True, slots=True)
class CovariateReading:
    """The candidate unmodeled-exposure covariates observed as-of one trade date.

    Any field may be ``None`` — that is honest absence, recorded as ``None`` in the
    banked observation rather than a fabricated value.
    """

    skew_proxy: float | None = None
    vanna_proxy: float | None = None
    regime_proxy: float | None = None
    vol_of_vol_proxy: float | None = None
    liquidity_proxy: float | None = None
    slippage_proxy: float | None = None


def _skew_proxy_as_of(
    store: ParquetStore, *, underlying: str, as_of: date
) -> float | None:
    """The SVI skew parameter (svi_rho) at the shortest tenor, read as-of.

    Reads stored surface parameters dated on or before ``as_of`` and returns the
    ``svi_rho`` of the most recent, shortest-maturity fit. None if no surface is
    banked as-of.
    """

    rows = store.read(_SURFACE_TABLE, underlying=underlying, end_date=as_of)
    candidates = [row for row in rows if row.snapshot_ts.date() <= as_of]
    if not candidates:
        return None
    latest = max(row.snapshot_ts for row in candidates)
    same_day = [row for row in candidates if row.snapshot_ts == latest]
    front = min(same_day, key=lambda row: row.maturity_years)
    return float(front.svi_rho)


def _regime_and_vov_as_of(
    store: ParquetStore,
    *,
    underlying: str,
    as_of: date,
    vov_lookback_days: int = 30,
) -> tuple[float | None, float | None]:
    """Regime (latest IV-rank) and vol-of-vol (IV-rank dispersion), read as-of.

    Both come from the signal layer's banked ``strategy_signals``. The regime
    proxy is the most recent IV-rank reading as-of ``as_of`` for the index leg;
    the vol-of-vol proxy is the standard deviation of the IV-rank readings over the
    trailing ``vov_lookback_days`` (need >= 2 to define a dispersion). Both None if
    no signals are banked.
    """

    start = as_of - timedelta(days=vov_lookback_days)
    rows = store.read(_SIGNALS_TABLE, underlying=underlying, start_date=start, end_date=as_of)
    iv_rank_rows = [
        row
        for row in rows
        if row.signal_kind == _SIGNAL_KIND_IV_RANK
        and row.subject == underlying
        and row.snapshot_ts.date() <= as_of
    ]
    if not iv_rank_rows:
        return None, None
    ordered = sorted(iv_rank_rows, key=lambda row: row.snapshot_ts)
    regime = float(ordered[-1].value)
    values = [row.value for row in ordered]
    if len(values) < 2:
        return regime, None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return regime, float(variance**0.5)


def read_covariates_as_of(
    store: ParquetStore, *, underlying: str, as_of: date
) -> CovariateReading:
    """Read every candidate covariate available as-of ``as_of`` (no look-ahead).

    Unavailable covariates come back ``None`` — never imputed. Liquidity and
    slippage proxies stay ``None`` until a fills-based position store banks the
    execution data they need.
    """

    skew = _skew_proxy_as_of(store, underlying=underlying, as_of=as_of)
    regime, vov = _regime_and_vov_as_of(store, underlying=underlying, as_of=as_of)
    return CovariateReading(
        skew_proxy=skew,
        vanna_proxy=None,
        regime_proxy=regime,
        vol_of_vol_proxy=vov,
        liquidity_proxy=None,
        slippage_proxy=None,
    )


def observation_from_realized(
    attribution: RealizedBookAttribution,
    covariates: CovariateReading,
    *,
    as_of_date: date,
    underlying: str,
    level: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
    portfolio_id: str | None = None,
) -> ResidualObservation:
    """Bank one realized-attribution residual row with its as-of covariates.

    The named Taylor terms and the residual come straight from the realized book
    attribution; the covariates are the as-of reading. ``realized_pnl`` is the
    full realized reprice (what actually happened); ``residual`` is the part the
    Taylor terms could not name.
    """

    terms = attribution.terms
    pid = portfolio_id if portfolio_id is not None else attribution.portfolio_id
    return ResidualObservation(
        as_of_date=as_of_date,
        portfolio_id=pid,
        underlying=underlying,
        level=level,
        realized_pnl=attribution.full_reprice_pnl,
        approx_pnl=terms.total,
        residual=attribution.residual,
        delta_pnl=terms.delta_pnl,
        gamma_pnl=terms.gamma_pnl,
        vega_pnl=terms.vega_pnl,
        theta_pnl=terms.theta_pnl,
        rho_pnl=terms.rho_pnl,
        vanna_pnl=terms.vanna_pnl,
        volga_pnl=terms.volga_pnl,
        attribution_version=attribution.config.version,
        diagnosis_version=DIAGNOSIS_VERSION,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
        skew_proxy=covariates.skew_proxy,
        vanna_proxy=covariates.vanna_proxy,
        regime_proxy=covariates.regime_proxy,
        vol_of_vol_proxy=covariates.vol_of_vol_proxy,
        liquidity_proxy=covariates.liquidity_proxy,
        slippage_proxy=covariates.slippage_proxy,
    )
