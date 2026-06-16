from __future__ import annotations

import math
from dataclasses import dataclass


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_call(
    forward: float, strike: float, maturity: float, sigma: float, discount_factor: float
) -> float:
    if maturity <= 0.0 or sigma <= 0.0:
        return discount_factor * max(forward - strike, 0.0)
    sqrt_t = math.sqrt(maturity)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * maturity) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return discount_factor * (forward * normal_cdf(d1) - strike * normal_cdf(d2))


def black_put(
    forward: float, strike: float, maturity: float, sigma: float, discount_factor: float
) -> float:
    if maturity <= 0.0 or sigma <= 0.0:
        return discount_factor * max(strike - forward, 0.0)
    sqrt_t = math.sqrt(maturity)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * maturity) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return discount_factor * (strike * normal_cdf(-d2) - forward * normal_cdf(-d1))


def parity_forward(call: float, put: float, strike: float, discount_factor: float) -> float:
    return strike + (call - put) / discount_factor


def svi_total_variance(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    return a + b * (rho * (k - m) + math.sqrt((k - m) ** 2 + sigma**2))


def delta_band_boundary_strike(
    *, forward: float, maturity_years: float, volatility: float, target_call_nd1: float
) -> float:
    from scipy.stats import norm

    d1 = float(norm.ppf(target_call_nd1))
    ln_fk = d1 * volatility * math.sqrt(maturity_years) - 0.5 * volatility**2 * maturity_years
    return forward / math.exp(ln_fk)


@dataclass(frozen=True, slots=True)
class DeltaBandLadder:

    forward: float
    maturity_years: float
    volatility: float
    discount_factor: float
    strikes: tuple[float, ...]
    put_boundary: float
    call_boundary: float

    def expected_band(self) -> tuple[float, ...]:
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

    strike: float
    log_moneyness: float
    sigma: float
    total_variance: float
    call_price: float
    put_price: float


@dataclass(frozen=True, slots=True)
class SyntheticSurface:

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

    forward: float
    discount_factor: float
    rate: float
    maturities: tuple[float, ...]
    slices: tuple[SyntheticSurface, ...]

    def true_total_variance(self, k: float, maturity_years: float) -> float:
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
