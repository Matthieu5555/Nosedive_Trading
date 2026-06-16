from __future__ import annotations

import bisect
import math
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SnapshotMarketState:

    underlying: str
    provider: str
    spot: float
    discount_factors: Mapping[float, float] = field(default_factory=dict)
    default_discount_factor: float = 1.0
    discount_factors_by_tenor: Mapping[str, float] = field(default_factory=dict)

    def discount_factor_for(self, tenor_label: str, maturity_years: float) -> float:
        by_tenor = self.discount_factors_by_tenor.get(tenor_label)
        if by_tenor is not None:
            return by_tenor
        return self.discount_factor_at(maturity_years)

    def discount_factor_at(self, maturity_years: float) -> float:
        exact = self.discount_factors.get(maturity_years)
        if exact is not None:
            return exact
        knots = sorted(
            (t, df)
            for t, df in self.discount_factors.items()
            if math.isfinite(t) and math.isfinite(df) and t > 0.0 and df > 0.0
        )
        if not knots:
            return self.default_discount_factor
        times = [t for t, _ in knots]
        log_discounts = [-math.log(df) for _, df in knots]
        if maturity_years <= times[0]:
            return math.exp(-(log_discounts[0] / times[0]) * maturity_years)
        if maturity_years >= times[-1]:
            return math.exp(-(log_discounts[-1] / times[-1]) * maturity_years)
        index = bisect.bisect_left(times, maturity_years)
        span = times[index] - times[index - 1]
        weight = (maturity_years - times[index - 1]) / span
        interpolated = log_discounts[index - 1] + weight * (
            log_discounts[index] - log_discounts[index - 1]
        )
        return math.exp(-interpolated)
