"""The threshold bundle every check reads, derived from the platform config.

``config.QcThresholdConfig`` is small and core-owned (this workstream does not edit it):
it carries a ``version`` plus three cross-cutting cut-offs — ``max_spread_pct``,
``max_quote_age_seconds``, ``min_chain_count``. Two of those three are read by checks
here: ``max_spread_pct`` gates underlying quote health and ``min_chain_count`` gates
chain coverage. The third, ``max_quote_age_seconds``, is exposed on this bundle but is
*not* read by any QC check — quote staleness is gated upstream in the snapshot builder
(``snapshots.assess_quote``), and a snapshot that failed it is already non-usable before
it reaches a check here; the property is kept for completeness and traceability.
The remaining checks need their own cut-offs (a max gap count, a max parity residual,
a max solver non-convergence ratio, and so on). Rather than edit the core contract to
bolt those on, we wrap it.

:class:`QcThresholds` holds the platform ``QcThresholdConfig`` plus the QC-owned
supplementary cut-offs, and it derives a single ``threshold_version`` from the
config's version so every ``QcResult`` is traceable to the economics version that
produced it. The supplementary defaults live here, at the top of the file, each
with a comment on what it gates — the one place a future operator looks to retune
the validation plane.

The grid-aware cut-offs (WS 1H) are the exception to the leftover-literal pattern: the
per-tenor coverage floors and the Δ-band window live in the typed ``config.grid``
(``GridQcConfig``) block and are surfaced here via ``.grid`` / ``.tenor_floor(tenor)`` /
``.band_low_delta`` / ``.band_high_delta`` / ``.max_delta_step`` — read *only* from typed
config, with **no** module-level ``.py`` literal. They set the ADR-0028 precedent the
leftover ``DEFAULT_*`` supplements above are later pulled into.

Every threshold is the boundary itself: a value *exactly on* the boundary passes
(``<=``/``>=`` as documented per check), and the edge-case tests pin that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from algotrading.core.config import GridQcConfig, QcThresholdConfig

# --- supplementary defaults (not in QcThresholdConfig) ---------------------------
# Collector continuity: at most this many gap events in a session before we fail; a
# warn band sits below it.
DEFAULT_MAX_GAP_COUNT = 5
DEFAULT_WARN_GAP_COUNT = 1
# Collector continuity: the fraction of subscribed instruments that must actually be
# covered by the session. Below this the feed is too thin to trust.
DEFAULT_MIN_COVERAGE_RATIO = 0.95
# Forward stability: the largest acceptable parity-line residual MAD; above it the
# forward is unstable and the curve point is not trustworthy.
DEFAULT_MAX_RESIDUAL_MAD = 0.05
# Forward stability: the lowest acceptable estimate confidence (0..1).
DEFAULT_MIN_FORWARD_CONFIDENCE = 0.5
# Parity residual: the largest acceptable single put-call-parity residual.
DEFAULT_MAX_PARITY_RESIDUAL = 0.10
# IV convergence: the largest acceptable fraction of solver requests that did not
# converge. Above it the smile is too holey to fit.
DEFAULT_MAX_NON_CONVERGENCE_RATIO = 0.10
# Surface fit: the largest acceptable per-slice RMSE (in total-variance units).
DEFAULT_MAX_SURFACE_RMSE = 0.02
# Anomaly detection: how many baseline MADs from the baseline median a value may sit
# before it is a spike (a robust z-score cut-off).
DEFAULT_ANOMALY_MAD_MULTIPLIER = 5.0


@dataclass(frozen=True, slots=True)
class QcThresholds:
    """Every cut-off the ten checks read, plus the version that stamps each result.

    ``config`` is the core ``QcThresholdConfig`` (read-only here); its three fields gate
    quote health and chain coverage. The remaining fields are QC-owned supplements.
    ``threshold_version`` is the config's version, so a ``QcResult`` always points
    back at the economics version that judged it.
    """

    config: QcThresholdConfig
    max_gap_count: int = DEFAULT_MAX_GAP_COUNT
    warn_gap_count: int = DEFAULT_WARN_GAP_COUNT
    min_coverage_ratio: float = DEFAULT_MIN_COVERAGE_RATIO
    max_residual_mad: float = DEFAULT_MAX_RESIDUAL_MAD
    min_forward_confidence: float = DEFAULT_MIN_FORWARD_CONFIDENCE
    max_parity_residual: float = DEFAULT_MAX_PARITY_RESIDUAL
    max_non_convergence_ratio: float = DEFAULT_MAX_NON_CONVERGENCE_RATIO
    max_surface_rmse: float = DEFAULT_MAX_SURFACE_RMSE
    anomaly_mad_multiplier: float = DEFAULT_ANOMALY_MAD_MULTIPLIER

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

    @property
    def grid(self) -> GridQcConfig:
        """The grid-aware QC cut-offs (per-tenor floors + Δ-band window), from typed config.

        These are the cut-offs the two grid checks (WS 1H) read. Unlike the leftover
        supplementary ``DEFAULT_*`` literals above, they come *only* from the typed/hydrated
        config block (ADR 0028) — no module-level ``.py`` literal — so they set the precedent
        the leftover defaults are later pulled into.
        """
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
    """Build the default :class:`QcThresholds` for a platform ``QcThresholdConfig``.

    The supplementary cut-offs take their documented defaults; the platform config
    supplies the three cross-cutting ones and the version.
    """
    return QcThresholds(config=config)
