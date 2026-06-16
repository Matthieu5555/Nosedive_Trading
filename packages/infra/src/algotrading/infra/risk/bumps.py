from __future__ import annotations

from dataclasses import dataclass

BUMP_VERSION = "risk-bumps-1.0.0"


@dataclass(frozen=True, slots=True)
class BumpSpec:

    version: str
    spot_first_rel: float
    spot_second_rel: float
    vol_abs: float
    time_abs: float

    def spot_first(self, spot: float) -> float:
        return self.spot_first_rel * spot

    def spot_second(self, spot: float) -> float:
        return self.spot_second_rel * spot


DEFAULT_BUMPS = BumpSpec(
    version=BUMP_VERSION,
    spot_first_rel=1e-6,
    spot_second_rel=1e-4,
    vol_abs=1e-5,
    time_abs=1e-5,
)
