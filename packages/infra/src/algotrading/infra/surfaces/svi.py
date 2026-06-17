from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from algotrading.core.config import SurfaceConfig
from scipy.optimize import least_squares

SURFACE_VERSION = "svi-1.0.0"

_PARAM_NAMES = ("a", "b", "rho", "m", "sigma")

MIN_POINTS_FOR_SVI = 5


def _svi_bounds(config: SurfaceConfig) -> tuple[tuple[float, ...], tuple[float, ...]]:
    pairs = (
        config.svi_a_bounds,
        config.svi_b_bounds,
        config.svi_rho_bounds,
        config.svi_m_bounds,
        config.svi_sigma_bounds,
    )
    return tuple(p[0] for p in pairs), tuple(p[1] for p in pairs)


@dataclass(frozen=True, slots=True)
class SviParams:

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def total_variance(self, k: float) -> float:
        x = k - self.m
        return self.a + self.b * (self.rho * x + math.sqrt(x * x + self.sigma * self.sigma))

    def minimum_total_variance(self) -> float:
        return self.a + self.b * self.sigma * math.sqrt(1.0 - self.rho * self.rho)

    def first_derivative(self, k: float) -> float:
        x = k - self.m
        r = math.sqrt(x * x + self.sigma * self.sigma)
        return self.b * (self.rho + x / r)

    def second_derivative(self, k: float) -> float:
        x = k - self.m
        r = math.sqrt(x * x + self.sigma * self.sigma)
        return self.b * self.sigma * self.sigma / (r * r * r)


@dataclass(frozen=True, slots=True)
class SviFit:

    params: SviParams
    rmse: float
    n_points: int
    bound_hits: tuple[str, ...]
    converged: bool


def _bound_hits(
    values: tuple[float, ...],
    *,
    lower: tuple[float, ...],
    upper: tuple[float, ...],
    tol: float,
) -> tuple[str, ...]:
    hits: list[str] = []
    for name, value, low, high in zip(_PARAM_NAMES, values, lower, upper, strict=True):
        span = high - low
        if value - low <= tol * span:
            hits.append(f"{name}_lower")
        elif high - value <= tol * span:
            hits.append(f"{name}_upper")
    return tuple(hits)


def fit_svi(
    ks: tuple[float, ...], total_variances: tuple[float, ...], *, config: SurfaceConfig
) -> SviFit:
    if len(ks) < MIN_POINTS_FOR_SVI:
        raise ValueError(f"SVI needs at least {MIN_POINTS_FOR_SVI} points, got {len(ks)}")

    lower, upper = _svi_bounds(config)
    k_array = np.asarray(ks, dtype=float)
    w_array = np.asarray(total_variances, dtype=float)

    def residuals(params: np.ndarray) -> np.ndarray:
        a, b, rho, m, sigma = params
        x = k_array - m
        return a + b * (rho * x + np.sqrt(x * x + sigma * sigma)) - w_array

    k_at_min = float(k_array[int(np.argmin(w_array))])
    initial = (float(w_array.min()), 0.1, 0.0, k_at_min, 0.1)
    result = least_squares(
        residuals, initial, bounds=(lower, upper),
        xtol=1e-14, ftol=1e-14, gtol=1e-14,
        max_nfev=config.svi_max_iterations * len(_PARAM_NAMES),
    )
    fitted = tuple(float(value) for value in result.x)
    rmse = float(np.sqrt(np.mean(np.asarray(result.fun) ** 2)))
    return SviFit(
        params=SviParams(*fitted),
        rmse=rmse,
        n_points=len(ks),
        bound_hits=_bound_hits(fitted, lower=lower, upper=upper, tol=config.svi_bound_hit_tol),
        converged=bool(result.success),
    )
