"""The threshold bundle every check reads, derived from the platform config.

``config.QcThresholdConfig`` is core-owned: it carries a ``version`` plus the three
cross-cutting cut-offs — ``max_spread_pct``, ``max_quote_age_seconds``,
``min_chain_count`` — and four nested economic blocks (``grid``, ``continuity``,
``forward_engine``, ``fit_tolerance``, ``anomaly``). Every economic QC cut-off lives in
that hashed config (ADR 0028): there are **no** module-level ``.py`` literals here. A cut-off
read from a ``.py`` default would not enter ``config_hashes["qc"]`` and so would not be part
of the reproducibility handle a derived record is branded with — the precise failure ADR 0028
exists to prevent.

:class:`QcThresholds` wraps the platform ``QcThresholdConfig`` and surfaces every cut-off as
a read-only property delegating to the typed config, plus a single ``threshold_version``
derived from the config's version so every ``QcResult`` is traceable to the economics version
that produced it. The grid-aware cut-offs (WS 1H) set this precedent — read *only* from typed
config — and the supplementary continuity / forward / fit / anomaly cut-offs now follow it.

Every threshold is the boundary itself: a value *exactly on* the boundary passes
(``<=``/``>=`` as documented per check), and the edge-case tests pin that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from algotrading.core.config import GridQcConfig, QcThresholdConfig


@dataclass(frozen=True, slots=True)
class QcThresholds:
    """Every cut-off the checks read, plus the version that stamps each result.

    ``config`` is the core ``QcThresholdConfig`` (read-only here). Every cut-off — the three
    cross-cutting scalars, the grid-aware block, and the supplementary continuity / forward /
    fit / anomaly cut-offs — is surfaced as a property delegating to the typed config, so no
    economic QC number lives as a ``.py`` literal outside ``config_hashes["qc"]`` (ADR 0028).
    ``threshold_version`` is the config's version, so a ``QcResult`` always points back at the
    economics version that judged it.
    """

    config: QcThresholdConfig

    @property
    def threshold_version(self) -> str:
        """The version stamp every ``QcResult`` carries: the config section version."""
        return self.config.version

    @property
    def max_spread_pct(self) -> float:
        """Quote-health cut-off, read straight from the platform config."""
        return self.config.max_spread_pct

    @property
    def max_quote_age_seconds(self) -> float:
        """Quote-health cut-off, read straight from the platform config."""
        return self.config.max_quote_age_seconds

    @property
    def min_chain_count(self) -> int:
        """Chain-coverage cut-off, read straight from the platform config."""
        return self.config.min_chain_count

    # --- collector continuity (config.continuity) --------------------------------
    @property
    def max_gap_count(self) -> int:
        """At most this many gap events in a session before it fails (from config)."""
        return self.config.continuity.max_gap_count

    @property
    def warn_gap_count(self) -> int:
        """A gap count above this (but at or below ``max_gap_count``) warns (from config)."""
        return self.config.continuity.warn_gap_count

    @property
    def min_coverage_ratio(self) -> float:
        """The fraction of subscribed instruments that must be covered (from config)."""
        return self.config.continuity.min_coverage_ratio

    # --- forward stability + parity (config.forward_engine) ----------------------
    @property
    def max_rel_residual_mad(self) -> float:
        """Largest acceptable parity-line residual MAD as a fraction of the forward (config)."""
        return self.config.forward_engine.max_rel_residual_mad

    @property
    def min_forward_confidence(self) -> float:
        """The lowest acceptable forward-estimate confidence (from config)."""
        return self.config.forward_engine.min_forward_confidence

    @property
    def max_rel_parity_residual(self) -> float:
        """Largest acceptable single put-call-parity residual as a fraction of the forward."""
        return self.config.forward_engine.max_rel_parity_residual

    # --- IV convergence + surface fit (config.fit_tolerance) ---------------------
    @property
    def max_non_convergence_ratio(self) -> float:
        """The largest acceptable solver non-convergence fraction (from config)."""
        return self.config.fit_tolerance.max_non_convergence_ratio

    @property
    def max_surface_rmse(self) -> float:
        """The largest acceptable per-slice RMSE, total-variance units (from config)."""
        return self.config.fit_tolerance.max_surface_rmse

    # --- anomaly (config.anomaly) ------------------------------------------------
    @property
    def anomaly_mad_multiplier(self) -> float:
        """The static anomaly-check spike cut-off, in baseline MADs (from config)."""
        return self.config.anomaly.mad_multiplier

    # --- grid-aware (config.grid) ------------------------------------------------
    @property
    def grid(self) -> GridQcConfig:
        """The grid-aware QC cut-offs (per-tenor floors + Δ-band window), from typed config."""
        return self.config.grid

    @property
    def tenor_floors(self) -> Mapping[str, int]:
        """The per-tenor coverage floors, keyed on the P0.1 pinned tenor grid."""
        return self.config.grid.tenor_floors

    def tenor_floor(self, tenor: str) -> int:
        """The configured coverage floor for ``tenor``; raises if a pinned tenor has none.

        Delegates to :meth:`GridQcConfig.floor_for` so a missing per-tenor floor is a config
        error (it never silently defaults to zero, which would pass a tenor for free).
        """
        return self.config.grid.floor_for(tenor)

    @property
    def band_low_delta(self) -> float:
        """The low (signed) edge of the Δ-band the selected strikes must span (e.g. -0.30)."""
        return self.config.grid.band_low_delta

    @property
    def band_high_delta(self) -> float:
        """The high (signed) edge of the Δ-band the selected strikes must span (e.g. +0.30)."""
        return self.config.grid.band_high_delta

    @property
    def max_delta_step(self) -> float:
        """The largest acceptable gap between consecutive selected deltas inside the band."""
        return self.config.grid.max_delta_step


def thresholds_from_config(config: QcThresholdConfig) -> QcThresholds:
    """Build the :class:`QcThresholds` bundle for a platform ``QcThresholdConfig``.

    Every cut-off is taken from the typed/hashed config — the cross-cutting scalars, the
    grid block, and the supplementary continuity / forward / fit / anomaly blocks — so no
    economic QC number is defaulted from a ``.py`` literal (ADR 0028).
    """
    return QcThresholds(config=config)
