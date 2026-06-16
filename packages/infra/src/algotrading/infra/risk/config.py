from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .aggregation import resolve_grouping_key
from .reconciliation import DEFAULT_RECON_TOLERANCE, ReconciliationTolerance

DEFAULT_GROUPING_KEYS = ("underlying", "maturity", "instrument")

ATTRIBUTION_GAMMA_NORMALISATIONS = ("one_dollar", "one_pct")
ATTRIBUTION_THETA_DAY_COUNTS = (365, 252)


@dataclass(frozen=True)
class AttributionConfig:

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
        return cls(version=version)

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> AttributionConfig:
        return cls(
            version=str(section.get("version", "attribution-1.0.0")),
            gamma_normalisation=str(section.get("gamma_normalisation", "one_dollar")),
            theta_day_count=int(section.get("theta_day_count", 365)),
            residual_abs_tol=float(section.get("residual_abs_tol", 1.0)),
            residual_rel_tol=float(section.get("residual_rel_tol", 0.05)),
        )


@dataclass(frozen=True)
class RiskParams:

    grouping_keys: tuple[str, ...]
    reconciliation_tolerance: ReconciliationTolerance
    config_version: str
    attribution: AttributionConfig = field(default_factory=AttributionConfig.defaults)

    def __post_init__(self) -> None:
        if not self.grouping_keys:
            raise ValueError("grouping_keys must list at least one key")
        for name in self.grouping_keys:
            resolve_grouping_key(name)

    @classmethod
    def defaults(cls, *, config_version: str = "risk-config-1.0.0") -> RiskParams:
        return cls(
            grouping_keys=DEFAULT_GROUPING_KEYS,
            reconciliation_tolerance=DEFAULT_RECON_TOLERANCE,
            config_version=config_version,
            attribution=AttributionConfig.defaults(),
        )

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> RiskParams:
        keys = tuple(str(name) for name in section["grouping_keys"])
        raw = dict(section.get("reconciliation_tolerances", {}))
        tol_map = {str(g): float(v) for g, v in raw.items()}
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
