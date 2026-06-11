"""Risk service settings: which grouping keys to publish and the reconciliation tolerances.

The blueprint keeps thresholds and grouping in config, not code ("Keep aggregate formulas
transparent and queryable", "support grouping by any configured key", and the QC/risk rule
"Keep thresholds in config, not in code"). :class:`RiskParams` is the typed, versioned bundle
of those settings.

It is deliberately *self-contained* rather than a section of the M0-frozen
:class:`algotrading.core.config.PlatformConfig`: the frozen platform config is a keystone seam
this workstream does not own, so risk carries its own params object that a future ``risk``
config section (or a service loader) can build via :meth:`RiskParams.from_mapping`. Grouping
keys are validated through :func:`aggregation.resolve_grouping_key` at construction, so a typo
fails loudly before any snapshot is built.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .aggregation import resolve_grouping_key
from .reconciliation import DEFAULT_RECON_TOLERANCE, ReconciliationTolerance

# The grouping keys the daily risk snapshot publishes by default — the intrinsic
# dimensions step 11 names. ``desk`` is opt-in (it needs a contract->desk mapping).
DEFAULT_GROUPING_KEYS = ("underlying", "maturity", "instrument")

# The two decomposition-convention forks for the by-Greek PnL attribution (2C). They
# carry the ADR-0036 / :class:`MonetizationConfig` vocabulary so a reader recognises the
# same forks the $-Greek display uses — but the *defaults differ deliberately*: the
# attribution defaults reproduce the blueprint Eq-19 Taylor term exactly (the lumped path),
# whereas the display layer defaults to the per-1%/per-calendar-day presentation.
#
# * ``gamma_normalisation``: ``"one_dollar"`` (default) is the blueprint ½·Γ·(dS)² with the
#   raw curvature Γ·S²; ``"one_pct"`` re-expresses the second-order spot contribution per
#   1% move (÷100), mirroring ``dollar_gamma``'s 1%-vs-$1 fork.
# * ``theta_day_count``: ``365`` (default, calendar) reproduces the scenario grid's own 365
#   day-count so the split equals the lumped Taylor; ``252`` (trading) re-expresses the time
#   contribution per trading day (×365/252).
#
# Both are *reporting normalisations on the decomposition only*. The full reprice stays the
# oracle (ADR 0006); flipping a flag moves that term and the residual absorbs the difference
# — it never touches the truth. See ``attribution.py`` and ADR 0038.
ATTRIBUTION_GAMMA_NORMALISATIONS = ("one_dollar", "one_pct")
ATTRIBUTION_THETA_DAY_COUNTS = (365, 252)


@dataclass(frozen=True)
class AttributionConfig:
    """Conventions + residual tolerance for the by-Greek PnL attribution (2C).

    ``version`` brands every attribution record built with these settings, so a stored
    record traces back to the exact decomposition conventions and tolerance that produced
    it. ``residual_abs_tol`` / ``residual_rel_tol`` bound the *reported* residual: the
    attribution is *accepted* when ``|residual| <= max(abs_tol, rel_tol*|full_reprice|)``.
    The residual is always reported regardless — for a large shock the Taylor decomposition
    is expected to diverge, and that divergence is the headline number, not an error.
    """

    version: str
    gamma_normalisation: str = "one_dollar"
    theta_day_count: int = 365
    residual_abs_tol: float = 1.0
    residual_rel_tol: float = 0.05

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("attribution config version must be non-empty")
        if self.gamma_normalisation not in ATTRIBUTION_GAMMA_NORMALISATIONS:
            raise ValueError(
                f"gamma_normalisation must be one of {ATTRIBUTION_GAMMA_NORMALISATIONS}"
            )
        if self.theta_day_count not in ATTRIBUTION_THETA_DAY_COUNTS:
            raise ValueError(f"theta_day_count must be one of {ATTRIBUTION_THETA_DAY_COUNTS}")
        for name in ("residual_abs_tol", "residual_rel_tol"):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and value >= 0.0):
                raise ValueError(f"{name} must be a non-negative number")

    @classmethod
    def defaults(cls, *, version: str = "attribution-1.0.0") -> AttributionConfig:
        """Blueprint-faithful defaults: Eq-19 gamma (one_dollar), calendar theta (365)."""
        return cls(version=version)

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> AttributionConfig:
        """Build attribution config from a plain ``attribution`` config mapping.

        Every field is optional; a missing one falls back to its blueprint-faithful
        default. An out-of-range value fails loudly via :meth:`__post_init__`.
        """
        return cls(
            version=str(section.get("version", "attribution-1.0.0")),
            gamma_normalisation=str(section.get("gamma_normalisation", "one_dollar")),
            theta_day_count=int(section.get("theta_day_count", 365)),
            residual_abs_tol=float(section.get("residual_abs_tol", 1.0)),
            residual_rel_tol=float(section.get("residual_rel_tol", 0.05)),
        )


@dataclass(frozen=True)
class RiskParams:
    """Grouping keys and per-Greek reconciliation tolerances for the risk service.

    ``config_version`` brands every snapshot built with these params, so a stored result
    traces back to the exact settings that produced it.
    """

    grouping_keys: tuple[str, ...]
    reconciliation_tolerance: ReconciliationTolerance
    config_version: str
    attribution: AttributionConfig = field(default_factory=AttributionConfig.defaults)

    def __post_init__(self) -> None:
        if not self.grouping_keys:
            raise ValueError("grouping_keys must list at least one key")
        for name in self.grouping_keys:
            resolve_grouping_key(name)  # fail fast on a typo, before any snapshot is built

    @classmethod
    def defaults(cls, *, config_version: str = "risk-config-1.0.0") -> RiskParams:
        """The default risk params: intrinsic grouping dimensions, default tolerances."""
        return cls(
            grouping_keys=DEFAULT_GROUPING_KEYS,
            reconciliation_tolerance=DEFAULT_RECON_TOLERANCE,
            config_version=config_version,
            attribution=AttributionConfig.defaults(),
        )

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> RiskParams:
        """Build risk params from a plain ``risk`` config mapping.

        Expects ``grouping_keys`` (a list of key names) and, optionally,
        ``reconciliation_tolerances`` (a mapping of greek -> absolute threshold),
        ``version`` (the risk-params lineage stamp), and ``recon_version`` (the
        reconciliation-tolerance lineage stamp — a distinct key so bumping one does not
        silently bump the other). Missing tolerances fall back to
        :data:`DEFAULT_RECON_TOLERANCE`'s values; an unknown grouping key raises via
        :meth:`__post_init__`.
        """
        keys = tuple(str(name) for name in section["grouping_keys"])
        raw = dict(section.get("reconciliation_tolerances", {}))
        tol_map = {str(g): float(v) for g, v in raw.items()}
        # Independent lineage stamps (ADR 0028): recon_version keys the tolerance
        # record; version (config_version) keys the overall risk-params record.
        # Using the same key for both means bumping one silently bumps the other.
        recon_version = str(section.get("recon_version", DEFAULT_RECON_TOLERANCE.version))
        tolerance = ReconciliationTolerance(
            version=recon_version,
            delta=tol_map.get("delta", DEFAULT_RECON_TOLERANCE.delta),
            gamma=tol_map.get("gamma", DEFAULT_RECON_TOLERANCE.gamma),
            vega=tol_map.get("vega", DEFAULT_RECON_TOLERANCE.vega),
            theta=tol_map.get("theta", DEFAULT_RECON_TOLERANCE.theta),
        )
        attribution_section = section.get("attribution")
        attribution = (
            AttributionConfig.from_mapping(attribution_section)
            if attribution_section is not None
            else AttributionConfig.defaults()
        )
        return cls(
            grouping_keys=keys,
            reconciliation_tolerance=tolerance,
            config_version=str(section.get("version", "risk-config-1.0.0")),
            attribution=attribution,
        )
