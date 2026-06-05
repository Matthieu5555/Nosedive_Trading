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
from dataclasses import dataclass
from typing import Any

from .aggregation import resolve_grouping_key
from .reconciliation import DEFAULT_RECON_TOLERANCE, ReconciliationTolerance

# The grouping keys the daily risk snapshot publishes by default — the intrinsic
# dimensions step 11 names. ``desk`` is opt-in (it needs a contract->desk mapping).
DEFAULT_GROUPING_KEYS = ("underlying", "maturity", "instrument")


@dataclass(frozen=True)
class RiskParams:
    """Grouping keys and per-Greek reconciliation tolerances for the risk service.

    ``config_version`` brands every snapshot built with these params, so a stored result
    traces back to the exact settings that produced it.
    """

    grouping_keys: tuple[str, ...]
    reconciliation_tolerance: ReconciliationTolerance
    config_version: str

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
        )

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> RiskParams:
        """Build risk params from a plain ``risk`` config mapping.

        Expects ``grouping_keys`` (a list of key names) and, optionally,
        ``reconciliation_tolerances`` (a mapping of greek -> absolute threshold) and a
        ``version``. Missing tolerances fall back to :data:`DEFAULT_RECON_TOLERANCE`'s
        values; an unknown grouping key raises via :meth:`__post_init__`.
        """
        keys = tuple(str(name) for name in section["grouping_keys"])
        raw = dict(section.get("reconciliation_tolerances", {}))
        tol_map = {str(g): float(v) for g, v in raw.items()}
        version = str(section.get("version", DEFAULT_RECON_TOLERANCE.version))
        tolerance = ReconciliationTolerance(
            version=version,
            delta=tol_map.get("delta", DEFAULT_RECON_TOLERANCE.delta),
            gamma=tol_map.get("gamma", DEFAULT_RECON_TOLERANCE.gamma),
            vega=tol_map.get("vega", DEFAULT_RECON_TOLERANCE.vega),
            theta=tol_map.get("theta", DEFAULT_RECON_TOLERANCE.theta),
        )
        return cls(
            grouping_keys=keys,
            reconciliation_tolerance=tolerance,
            config_version=str(section.get("version", "risk-config-1.0.0")),
        )
