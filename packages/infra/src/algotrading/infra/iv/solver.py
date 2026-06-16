from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.config import SolverConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.pricing import from_forward, price_european
from scipy.optimize import brentq

SOLVER_VERSION = "iv-brentq-1.0.0"


_PRICE_RTOL = 1e-9
_PRICE_ATOL = 1e-12

STATUS_CONVERGED = "converged"
STATUS_BELOW_INTRINSIC = "below_intrinsic"
STATUS_ABOVE_MAX = "above_max"
STATUS_NON_CONVERGENCE = "non_convergence"

PriceFn = Callable[[float], float]


@dataclass(frozen=True, slots=True)
class SolveOutcome:

    iv: float | None
    status: str
    iterations: int
    residual: float
    bracket_low: float
    bracket_high: float

    @property
    def converged(self) -> bool:
        return self.status == STATUS_CONVERGED and self.iv is not None


@dataclass(frozen=True, slots=True)
class IvResult:

    contract_key: str
    iv: float | None
    k: float
    total_variance: float | None
    status: str
    iterations: int
    residual: float
    model: str
    bracket_low: float
    bracket_high: float
    forward: float
    strike: float
    maturity_years: float

    @property
    def converged(self) -> bool:
        return self.status == STATUS_CONVERGED and self.iv is not None


def european_price_bounds(
    forward: float, strike: float, discount_factor: float, option_right: str
) -> tuple[float, float]:
    if option_right == "C":
        intrinsic = discount_factor * max(forward - strike, 0.0)
        ceiling = discount_factor * forward
    else:
        intrinsic = discount_factor * max(strike - forward, 0.0)
        ceiling = discount_factor * strike
    return intrinsic, ceiling


def solve_implied_vol_scalar(
    target_price: float,
    price_fn: PriceFn,
    *,
    intrinsic: float,
    ceiling: float,
    config: SolverConfig,
) -> SolveOutcome:
    vol_min = config.vol_min
    vol_max = config.vol_max
    price_tolerance = _PRICE_RTOL * max(abs(ceiling), 1.0) + _PRICE_ATOL

    if target_price < intrinsic - price_tolerance:
        return SolveOutcome(
            None, STATUS_BELOW_INTRINSIC, 0, intrinsic - target_price, vol_min, vol_max
        )
    if target_price > ceiling + price_tolerance:
        return SolveOutcome(
            None, STATUS_ABOVE_MAX, 0, target_price - ceiling, vol_min, vol_max
        )

    price_at_floor = price_fn(vol_min)
    if target_price <= price_at_floor + price_tolerance:
        return SolveOutcome(0.0, STATUS_CONVERGED, 0, abs(target_price - price_at_floor),
                            vol_min, vol_max)

    price_at_ceiling = price_fn(vol_max)
    if target_price >= price_at_ceiling - price_tolerance:
        return SolveOutcome(
            None, STATUS_ABOVE_MAX, 0, abs(target_price - price_at_ceiling), vol_min, vol_max
        )

    def objective(vol: float) -> float:
        return price_fn(vol) - target_price

    root, results = brentq(
        objective, vol_min, vol_max,
        xtol=config.iv_tolerance, maxiter=config.max_iterations, full_output=True, disp=False,
    )
    residual = abs(price_fn(root) - target_price)
    if not results.converged:
        return SolveOutcome(
            None, STATUS_NON_CONVERGENCE, results.iterations, residual, vol_min, vol_max
        )
    return SolveOutcome(float(root), STATUS_CONVERGED, results.iterations, residual,
                        vol_min, vol_max)


def solve_iv(
    target_price: float,
    *,
    contract_key: str,
    forward: float,
    strike: float,
    maturity_years: float,
    discount_factor: float,
    option_right: str,
    config: SolverConfig,
) -> IvResult:
    k = math.log(strike / forward)

    def price_fn(vol: float) -> float:
        state = from_forward(
            forward=forward, strike=strike, maturity_years=maturity_years,
            volatility=vol, discount_factor=discount_factor, option_right=option_right,
        )
        return price_european(state).price

    intrinsic, ceiling = european_price_bounds(forward, strike, discount_factor, option_right)
    outcome = solve_implied_vol_scalar(
        target_price, price_fn, intrinsic=intrinsic, ceiling=ceiling, config=config
    )
    total_variance = (
        outcome.iv * outcome.iv * maturity_years if outcome.iv is not None else None
    )
    return IvResult(
        contract_key=contract_key,
        iv=outcome.iv,
        k=k,
        total_variance=total_variance,
        status=outcome.status,
        iterations=outcome.iterations,
        residual=outcome.residual,
        model="black76",
        bracket_low=outcome.bracket_low,
        bracket_high=outcome.bracket_high,
        forward=forward,
        strike=strike,
        maturity_years=maturity_years,
    )


@dataclass(frozen=True, slots=True)
class IvRequest:

    target_price: float
    contract_key: str
    forward: float
    strike: float
    maturity_years: float
    discount_factor: float
    option_right: str


def solve_iv_batch(
    requests: tuple[IvRequest, ...], *, config: SolverConfig
) -> tuple[IvResult, ...]:
    return tuple(
        solve_iv(
            request.target_price,
            contract_key=request.contract_key,
            forward=request.forward,
            strike=request.strike,
            maturity_years=request.maturity_years,
            discount_factor=request.discount_factor,
            option_right=request.option_right,
            config=config,
        )
        for request in requests
    )


def iv_point(
    result: IvResult,
    *,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> IvPoint:
    if not result.converged or result.iv is None or result.total_variance is None:
        raise ValueError(
            f"cannot emit an IvPoint for an unconverged solve ({result.status})"
        )
    provenance: ProvenanceStamp = stamp(
        calc_ts=calc_ts,
        code_version=SOLVER_VERSION,
        config_hashes=config_hashes,
        source_records=(
            source_ref("market_state_snapshots", source_snapshot_ts, result.contract_key),
        ),
        source_timestamps=(source_snapshot_ts,),
    )
    return IvPoint(
        snapshot_ts=snapshot_ts,
        contract_key=result.contract_key,
        implied_vol=result.iv,
        log_moneyness=result.k,
        total_variance=result.total_variance,
        solver_version=SOLVER_VERSION,
        diagnostics=IvDiagnostics(
            converged=result.converged,
            iterations=result.iterations,
            residual=result.residual,
            status=result.status,
        ),
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
