"""The known-answer generators behind the synthetic fixtures.

These are the *oracles* for the analytics workstreams. They live here, in the
fixture library, so the code under test (C's IV solver, forward engine, surface
fitter) is never tested against itself. We pick the true volatility and the true
SVI parameters, generate option prices from them with the Black-76 formula, and
store both the prices and the true answers. C then inverts the prices and must
recover what we put in.

The math, kept deliberately small and closed-form:

* Black-76 forward-form call/put (roadmap Eqs 8-11), so put-call parity holds
  exactly: ``call - put = discount_factor * (forward - strike)``.
* The parity forward (Eq 2): ``forward = strike + (call - put) / discount_factor``.
* The raw SVI total-variance slice (Eq 20):
  ``w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))``.

The normal CDF uses ``math.erf`` so there is no scipy dependency here — the
generators stay self-contained.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_call(
    forward: float, strike: float, maturity: float, sigma: float, discount_factor: float
) -> float:
    """Black-76 forward-form European call price.

    Degenerate inputs (non-positive maturity or vol) fall back to the discounted
    intrinsic value, which keeps the generator total instead of raising.
    """
    if maturity <= 0.0 or sigma <= 0.0:
        return discount_factor * max(forward - strike, 0.0)
    sqrt_t = math.sqrt(maturity)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * maturity) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return discount_factor * (forward * normal_cdf(d1) - strike * normal_cdf(d2))


def black_put(
    forward: float, strike: float, maturity: float, sigma: float, discount_factor: float
) -> float:
    """Black-76 forward-form European put price."""
    if maturity <= 0.0 or sigma <= 0.0:
        return discount_factor * max(strike - forward, 0.0)
    sqrt_t = math.sqrt(maturity)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * maturity) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return discount_factor * (strike * normal_cdf(-d2) - forward * normal_cdf(-d1))


def parity_forward(call: float, put: float, strike: float, discount_factor: float) -> float:
    """Recover the forward from a call/put pair via put-call parity (Eq 2)."""
    return strike + (call - put) / discount_factor


def svi_total_variance(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    """Raw SVI total variance at log-moneyness ``k`` (Eq 20)."""
    return a + b * (rho * (k - m) + math.sqrt((k - m) ** 2 + sigma**2))


@dataclass(frozen=True, slots=True)
class SyntheticPoint:
    """One generated strike: its true vol, total variance, and call/put prices."""

    strike: float
    log_moneyness: float
    sigma: float
    total_variance: float
    call_price: float
    put_price: float


@dataclass(frozen=True, slots=True)
class SyntheticSurface:
    """A fully consistent synthetic slice: the inputs and the generated prices.

    Everything needed to check recovery is here: the true forward and discount
    factor (recover via parity), the true per-strike vols (recover via the IV
    solver), and the true SVI parameters (recover via the surface fit).
    """

    forward: float
    discount_factor: float
    maturity_years: float
    svi_a: float
    svi_b: float
    svi_rho: float
    svi_m: float
    svi_sigma: float
    points: tuple[SyntheticPoint, ...]


def build_synthetic_surface(
    *,
    forward: float = 100.0,
    discount_factor: float = 0.99,
    maturity_years: float = 0.25,
    strikes: tuple[float, ...] = (80.0, 90.0, 100.0, 110.0, 120.0),
    svi_a: float = 0.04,
    svi_b: float = 0.10,
    svi_rho: float = -0.30,
    svi_m: float = 0.0,
    svi_sigma: float = 0.20,
) -> SyntheticSurface:
    """Generate a consistent synthetic surface from chosen true parameters.

    Per strike: ``k = ln(K / forward)``; total variance from the SVI slice;
    ``sigma_k = sqrt(w / T)``; call and put from Black-76 at ``sigma_k``. By
    construction the IV solver, the parity forward, and the SVI fit are all
    analytically recoverable from the resulting prices.
    """
    points = []
    for strike in strikes:
        k = math.log(strike / forward)
        w = svi_total_variance(k, svi_a, svi_b, svi_rho, svi_m, svi_sigma)
        sigma_k = math.sqrt(w / maturity_years)
        call = black_call(forward, strike, maturity_years, sigma_k, discount_factor)
        put = black_put(forward, strike, maturity_years, sigma_k, discount_factor)
        points.append(
            SyntheticPoint(
                strike=strike,
                log_moneyness=k,
                sigma=sigma_k,
                total_variance=w,
                call_price=call,
                put_price=put,
            )
        )
    return SyntheticSurface(
        forward=forward,
        discount_factor=discount_factor,
        maturity_years=maturity_years,
        svi_a=svi_a,
        svi_b=svi_b,
        svi_rho=svi_rho,
        svi_m=svi_m,
        svi_sigma=svi_sigma,
        points=tuple(points),
    )
