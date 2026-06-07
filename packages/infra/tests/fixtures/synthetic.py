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


def delta_band_boundary_strike(
    *, forward: float, maturity_years: float, volatility: float, target_call_nd1: float
) -> float:
    """The strike whose undiscounted forward call delta ``N(d1)`` equals ``target_call_nd1``.

    The independent oracle for the delta-band selection boundary (WS 1B). Invert the
    standard-normal CDF (``norm.ppf`` here is a *different* implementation from the engine's
    ``math.erf`` path, so this is a genuine independent oracle, not a round-trip): from
    ``N(d1) = target`` recover ``d1``, then solve
    ``d1 = (ln(forward / K) + 0.5 σ² T) / (σ √T)`` for the strike ``K``. The 30Δ call sits at
    ``target = 0.30`` (a high strike); the 30Δ put sits at ``target = 1 − 0.30 = 0.70`` (a
    low strike, where the put delta magnitude ``1 − N(d1)`` equals 0.30). Carry is 0 by
    construction, so spot and forward delta coincide.
    """
    from scipy.stats import norm  # independent CDF impl (engine uses math.erf)

    d1 = float(norm.ppf(target_call_nd1))
    ln_fk = d1 * volatility * math.sqrt(maturity_years) - 0.5 * volatility**2 * maturity_years
    return forward / math.exp(ln_fk)


@dataclass(frozen=True, slots=True)
class DeltaBandLadder:
    """A hand-built strike ladder at one tenor with its 30Δ boundaries from the oracle.

    Everything a delta-band selection test needs: the per-tenor pricing inputs (forward,
    maturity, working vol, discount factor), the listed ``strikes``, and the
    independently-derived 30Δ put/call boundary strikes (via :func:`delta_band_boundary_strike`,
    a scipy-``norm.ppf`` oracle distinct from the engine). The expected kept set is the
    contiguous block of ``strikes`` lying in ``[put_boundary, call_boundary]`` inclusive.
    """

    forward: float
    maturity_years: float
    volatility: float
    discount_factor: float
    strikes: tuple[float, ...]
    put_boundary: float
    call_boundary: float

    def expected_band(self) -> tuple[float, ...]:
        """The listed strikes inside ``[put_boundary, call_boundary]`` inclusive, ascending.

        Derived from the oracle boundaries with a tiny tolerance so a strike placed *exactly*
        on a boundary by the oracle is counted in (the boundary-exact case). This is the
        expected answer the selection code must reproduce — computed here without calling the
        selection code.
        """
        tol = 1e-9 * self.forward
        return tuple(
            strike
            for strike in sorted(self.strikes)
            if self.put_boundary - tol <= strike <= self.call_boundary + tol
        )


def build_delta_band_ladder(
    *,
    forward: float = 100.0,
    maturity_years: float = 0.25,
    volatility: float = 0.20,
    discount_factor: float = 0.99,
    interior_strikes: tuple[float, ...] = (96.0, 98.0, 100.0, 102.0, 104.0),
    wing_strikes: tuple[float, ...] = (80.0, 88.0, 112.0, 120.0),
    include_exact_boundaries: bool = True,
) -> DeltaBandLadder:
    """Build a :class:`DeltaBandLadder`: interior strikes, wings, and the exact 30Δ boundaries.

    The 30Δ put/call boundary strikes are computed from the oracle and (by default) added to
    the listed ``strikes`` so the test can assert the boundary-exact strike is kept. The
    interior strikes sit inside the band, the wings outside it.
    """
    put_boundary = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity_years, volatility=volatility,
        target_call_nd1=0.70,
    )
    call_boundary = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity_years, volatility=volatility,
        target_call_nd1=0.30,
    )
    listed = set(interior_strikes) | set(wing_strikes)
    if include_exact_boundaries:
        listed |= {put_boundary, call_boundary}
    return DeltaBandLadder(
        forward=forward,
        maturity_years=maturity_years,
        volatility=volatility,
        discount_factor=discount_factor,
        strikes=tuple(sorted(listed)),
        put_boundary=put_boundary,
        call_boundary=call_boundary,
    )


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


@dataclass(frozen=True, slots=True)
class SyntheticTermSurface:
    """A calendar-consistent term structure of synthetic slices — the WS 1F oracle.

    Several :class:`SyntheticSurface` slices at increasing maturities, generated so total
    variance is **non-decreasing in maturity at every log-moneyness** (calendar no-arb,
    Eq 21): the SVI level ``a`` grows linearly with maturity while the shape (``b, rho, m,
    sigma``) is held fixed, so ``w(k, T)`` rises with ``T`` for every ``k``. This is the
    independent generator the projection's tenor regrid and its calendar-no-arb property
    test check against — the true total variance at any ``(k, T)`` is recoverable here
    without calling the projection code.
    """

    forward: float
    discount_factor: float
    rate: float
    maturities: tuple[float, ...]
    slices: tuple[SyntheticSurface, ...]

    def true_total_variance(self, k: float, maturity_years: float) -> float:
        """The generator's total variance at ``(k, maturity)`` — linear in ``w`` across slices.

        The same calendar-consistent interpolation the projection must reproduce: flat
        beyond the maturity ends, linear in total variance between bracketing slices. The
        oracle for the regrid, computed from the true SVI parameters, not the fit.
        """
        ordered = self.slices
        if maturity_years <= ordered[0].maturity_years:
            target = ordered[0]
            return svi_total_variance(
                k, target.svi_a, target.svi_b, target.svi_rho, target.svi_m, target.svi_sigma
            )
        if maturity_years >= ordered[-1].maturity_years:
            target = ordered[-1]
            return svi_total_variance(
                k, target.svi_a, target.svi_b, target.svi_rho, target.svi_m, target.svi_sigma
            )
        for low, high in zip(ordered, ordered[1:], strict=False):
            if low.maturity_years <= maturity_years <= high.maturity_years:
                span = high.maturity_years - low.maturity_years
                weight = (maturity_years - low.maturity_years) / span
                w_low = svi_total_variance(
                    k, low.svi_a, low.svi_b, low.svi_rho, low.svi_m, low.svi_sigma
                )
                w_high = svi_total_variance(
                    k, high.svi_a, high.svi_b, high.svi_rho, high.svi_m, high.svi_sigma
                )
                return w_low + weight * (w_high - w_low)
        raise ValueError("maturity not bracketed")  # pragma: no cover - range-guarded above


def build_synthetic_term_surface(
    *,
    forward: float = 100.0,
    rate: float = 0.02,
    maturities: tuple[float, ...] = (10.0 / 365.0, 0.5, 1.0, 2.0, 3.0),
    strikes: tuple[float, ...] = (60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0),
    svi_a_per_year: float = 0.04,
    svi_b: float = 0.06,
    svi_rho: float = -0.20,
    svi_m: float = 0.0,
    svi_sigma: float = 0.30,
) -> SyntheticTermSurface:
    """Generate a calendar-consistent synthetic term surface (slices at several maturities).

    The SVI level scales with maturity (``a = svi_a_per_year * T``) while the shape is
    held fixed, so total variance rises with maturity at every strike (calendar no-arb).
    The discount factor at each maturity is ``exp(-rate * T)``. The wide strike ladder
    (0.6×–1.4× forward) is deliberately broad so the 30Δ delta band lands inside the fitted
    span at every tenor. By construction the IV solver, SVI fit, and the projection's
    regrid are all analytically recoverable.
    """
    slices = tuple(
        build_synthetic_surface(
            forward=forward,
            discount_factor=math.exp(-rate * maturity),
            maturity_years=maturity,
            strikes=strikes,
            svi_a=svi_a_per_year * maturity,
            svi_b=svi_b,
            svi_rho=svi_rho,
            svi_m=svi_m,
            svi_sigma=svi_sigma,
        )
        for maturity in maturities
    )
    return SyntheticTermSurface(
        forward=forward,
        discount_factor=math.exp(-rate * maturities[-1]),
        rate=rate,
        maturities=maturities,
        slices=slices,
    )
