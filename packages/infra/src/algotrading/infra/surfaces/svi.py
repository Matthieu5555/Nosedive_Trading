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
from scipy.optimize import least_squares

# Bump only on a real change to the surface logic, never on config.
SURFACE_VERSION = "svi-1.0.0"

# Parameter bounds for the fit, named so the bound-hit diagnostic can report which
# one a calibrated parameter pinned against. b and sigma are floored strictly above
# zero so the contract's positivity rule (svi_b > 0, svi_sigma > 0) always holds.
_A_BOUNDS = (0.0, 10.0)
_B_BOUNDS = (1e-8, 10.0)
_RHO_BOUNDS = (-0.999, 0.999)
_M_BOUNDS = (-5.0, 5.0)
_SIGMA_BOUNDS = (1e-8, 10.0)
_PARAM_NAMES = ("a", "b", "rho", "m", "sigma")
_LOWER = (_A_BOUNDS[0], _B_BOUNDS[0], _RHO_BOUNDS[0], _M_BOUNDS[0], _SIGMA_BOUNDS[0])
_UPPER = (_A_BOUNDS[1], _B_BOUNDS[1], _RHO_BOUNDS[1], _M_BOUNDS[1], _SIGMA_BOUNDS[1])

# A parameter within this (relative-to-range) distance of a bound is "at the bound".
_BOUND_HIT_TOL = 1e-5

# SVI has five parameters, so a genuine fit needs at least five points.
MIN_POINTS_FOR_SVI = 5


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


def _bound_hits(values: tuple[float, ...]) -> tuple[str, ...]:
    """Name the parameters sitting at a lower/upper bound after the fit."""
    hits: list[str] = []
    for name, value, low, high in zip(_PARAM_NAMES, values, _LOWER, _UPPER, strict=True):
        span = high - low
        if value - low <= _BOUND_HIT_TOL * span:
            hits.append(f"{name}_lower")
        elif high - value <= _BOUND_HIT_TOL * span:
            hits.append(f"{name}_upper")
    return tuple(hits)


def fit_svi(
    ks: tuple[float, ...], total_variances: tuple[float, ...], *, max_iterations: int = 200
) -> SviFit:
    """Calibrate raw SVI to ``(k, w)`` points by bounded least squares (Eq 20).

    Minimizes the sum of squared total-variance residuals with
    :func:`scipy.optimize.least_squares` under the parameter bounds. The initial
    guess anchors ``a`` at the lowest observed variance and ``m`` at the lowest-w
    strike, which converges cleanly for a well-formed smile. Raises ``ValueError``
    for fewer than five points (SVI has five parameters); the slice orchestrator
    routes sparse slices to the nonparametric fallback instead.
    """
    if len(ks) < MIN_POINTS_FOR_SVI:
        raise ValueError(f"SVI needs at least {MIN_POINTS_FOR_SVI} points, got {len(ks)}")

    k_array = np.asarray(ks, dtype=float)
    w_array = np.asarray(total_variances, dtype=float)

    def residuals(params: np.ndarray) -> np.ndarray:
        a, b, rho, m, sigma = params
        x = k_array - m
        return a + b * (rho * x + np.sqrt(x * x + sigma * sigma)) - w_array

    k_at_min = float(k_array[int(np.argmin(w_array))])
    initial = (float(w_array.min()), 0.1, 0.0, k_at_min, 0.1)
    result = least_squares(
        residuals, initial, bounds=(_LOWER, _UPPER),
        xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=max_iterations * len(_PARAM_NAMES),
    )
    fitted = tuple(float(value) for value in result.x)
    rmse = float(np.sqrt(np.mean(np.asarray(result.fun) ** 2)))
    return SviFit(
        params=SviParams(*fitted),
        rmse=rmse,
        n_points=len(ks),
        bound_hits=_bound_hits(fitted),
        converged=bool(result.success),
    )
