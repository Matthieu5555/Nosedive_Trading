"""The raw SVI parameterization, its derivatives, and the least-squares fit.

SVI (Stochastic-Volatility-Inspired) describes one maturity's total-variance smile
with five parameters (roadmap Eq 20):

    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))

where ``w`` is total variance ``sigma_impl^2 * T`` and ``k`` is log-moneyness
``ln(K/F)``. The parameters: ``a`` sets the overall level, ``b >= 0`` the wing
slope, ``rho in [-1, 1]`` the skew, ``m`` the horizontal shift, ``sigma > 0`` the
at-the-money curvature. ``b`` and ``sigma`` are bounded strictly positive so the
fitted parameters always satisfy A's ``SurfaceParameters`` contract.

The first and second derivatives are needed by the butterfly no-arbitrage check, and
they are available in closed form (no finite differencing): with ``x = k - m`` and
``r = sqrt(x^2 + sigma^2)``,

    w'(k)  = b * (rho + x / r)
    w''(k) = b * sigma^2 / r^3   (>= 0 always, since b > 0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from algotrading.core.config import SurfaceConfig
from scipy.optimize import least_squares

# Bump only on a real change to the surface logic, never on config.
SURFACE_VERSION = "svi-1.0.0"

# The five SVI parameters, in fit order. Their feasible ranges and the bound-hit
# tolerance are economic inputs and live in SurfaceConfig (pricing.yaml), not here.
_PARAM_NAMES = ("a", "b", "rho", "m", "sigma")

# SVI has five parameters, so a genuine fit needs at least five points. A mathematical
# invariant (you cannot identify five parameters from fewer than five points), not a
# tunable — it stays a code constant per the config standard's invariant carve-out.
MIN_POINTS_FOR_SVI = 5


def _svi_bounds(config: SurfaceConfig) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The (lower, upper) least-squares bounds for the five SVI params, from config."""
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
    """The five raw-SVI parameters for one maturity slice."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def total_variance(self, k: float) -> float:
        """Total variance ``w(k)`` at log-moneyness ``k`` (Eq 20)."""
        x = k - self.m
        return self.a + self.b * (self.rho * x + math.sqrt(x * x + self.sigma * self.sigma))

    def first_derivative(self, k: float) -> float:
        """``dw/dk`` in closed form."""
        x = k - self.m
        r = math.sqrt(x * x + self.sigma * self.sigma)
        return self.b * (self.rho + x / r)

    def second_derivative(self, k: float) -> float:
        """``d2w/dk2`` in closed form (non-negative whenever ``b > 0``)."""
        x = k - self.m
        r = math.sqrt(x * x + self.sigma * self.sigma)
        return self.b * self.sigma * self.sigma / (r * r * r)


@dataclass(frozen=True, slots=True)
class SviFit:
    """A calibrated SVI slice and how well it fit.

    ``bound_hits`` names every parameter that pinned against a bound (e.g.
    ``"rho_lower"``), so a fit that ran into its feasible edge is visible, not
    silently trusted. ``rmse`` is in total-variance units.
    """

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
    """Name the parameters sitting at a lower/upper bound after the fit."""
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
    """Calibrate raw SVI to ``(k, w)`` points by bounded least squares (Eq 20).

    Minimizes the sum of squared total-variance residuals with
    :func:`scipy.optimize.least_squares` under the parameter bounds from ``config``.
    The initial guess anchors ``a`` at the lowest observed variance and ``m`` at the
    lowest-w strike, which converges cleanly for a well-formed smile. Raises
    ``ValueError`` for fewer than five points (SVI has five parameters); the slice
    orchestrator routes sparse slices to the nonparametric fallback instead.
    """
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
