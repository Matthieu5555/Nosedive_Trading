"""Implied-volatility inversion: a price in, a volatility (or a labeled failure) out.

The one legitimate round-trip in the platform. The pricing engine maps a vol to a
price; this module inverts that map with a bracketed scalar root finder
(:func:`scipy.optimize.brentq`). The two sides are *different code* — the pricer is
the independent oracle for the solver — so recovering a known vol via
price-then-invert is a real test, not a tautology.

Two layers:

* :func:`solve_implied_vol_scalar` is the engine-agnostic primitive. Given a target
  price, a ``price_fn`` from vol to price, and the price bounds (intrinsic and the
  ``F*DF`` ceiling), it returns a :class:`SolveOutcome`. It inverts *any* monotone
  pricer — Black-76 for European, a lattice or Bjerksund-Stensland for American — so
  the American inversion is "via the chosen pricer" simply by passing that pricer's
  ``price_fn``.
* :func:`solve_iv` is the European convenience: it builds the Black-76 ``price_fn``
  and the bounds, runs the scalar solve, and packages the result with the
  log-moneyness ``k = ln(K/F)`` (Eq 6) and total variance ``w = sigma**2 * T``
  (Eq 7) into a rich :class:`IvResult`.

Purity holds throughout: no I/O, no clock, no randomness; ``calc_ts`` is injected at
the contract-projection step (:func:`iv_point`), never read here.

A failed inversion is never a bare ``NaN``. Every outcome carries a status —
``converged``, ``below_intrinsic``, ``above_max``, or ``non_convergence`` — with the
iteration count, the final price residual, and the bracket, so a consumer can see
exactly why a contract has no implied vol. Such a result is *not* projected to an
``IvPoint`` (the contract requires a finite ``iv >= 0``); the diagnostic stands on
its own.
"""

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

# Bump only on a real change to the solver logic, never on config.
SOLVER_VERSION = "iv-brentq-1.0.0"

# The vol search bracket now lives in SolverConfig (vol_min/vol_max), authored in
# pricing.yaml — it is an economic input, not a code constant (C7 / ADR 0028).

# Price-space tolerance for the bound checks, relative to the price ceiling plus a tiny
# absolute floor, so "below intrinsic" / "above max" is judged at the scale of the data.
# These are machine-precision float-comparison epsilons, not tunable economics — they
# stay code constants per the standard's "tolerances for float comparison" carve-out.
_PRICE_RTOL = 1e-9
_PRICE_ATOL = 1e-12

# Status codes. Every terminal state is one of these, never blank, never NaN.
STATUS_CONVERGED = "converged"
STATUS_BELOW_INTRINSIC = "below_intrinsic"
STATUS_ABOVE_MAX = "above_max"
STATUS_NON_CONVERGENCE = "non_convergence"

PriceFn = Callable[[float], float]


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """The bare result of one scalar inversion, engine-agnostic.

    ``iv`` is ``None`` for every non-converged status. ``bracket`` is the
    ``(vol_low, vol_high)`` the solver searched, retained for the diagnostic.
    """

    iv: float | None
    status: str
    iterations: int
    residual: float
    bracket_low: float
    bracket_high: float

    @property
    def converged(self) -> bool:
        """True only when a finite implied vol was found."""
        return self.status == STATUS_CONVERGED and self.iv is not None


@dataclass(frozen=True, slots=True)
class IvResult:
    """A solved implied-vol point with its full diagnostics, before persistence.

    Richer than A's :class:`~contracts.IvDiagnostics`: it keeps the log-moneyness,
    the total variance, the search bracket, and the pricing model, so a debugging
    consumer sees the whole inversion, not just the summary. ``iv``,
    ``total_variance`` are ``None`` unless ``status == "converged"``.
    """

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
        """True only when a finite implied vol was found."""
        return self.status == STATUS_CONVERGED and self.iv is not None


def european_price_bounds(
    forward: float, strike: float, discount_factor: float, option_right: str
) -> tuple[float, float]:
    """Intrinsic (lower) and the ``F*DF`` ceiling (upper) for a European price.

    A call is worth at least ``DF*max(F-K, 0)`` and at most ``DF*F`` (the vol → ∞
    limit); a put at least ``DF*max(K-F, 0)`` and at most ``DF*K``. A market price
    outside ``[intrinsic, ceiling]`` cannot be produced by any non-negative vol, so
    it is a labeled failure, not a number to invert.
    """
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
    """Invert ``price_fn`` for the vol that reprices ``target_price``; the primitive.

    ``price_fn`` must be (weakly) increasing in vol — every Black/lattice pricer is.
    Bound checks first: a target below intrinsic or above the ceiling is impossible
    and returns the matching status with no vol. A target at the intrinsic floor is
    a zero-time-value price, so the vol is ``0`` exactly. Otherwise a bracketed
    Brent solve runs on the configured ``[vol_min, vol_max]`` bracket; if even
    ``vol_max`` underprices the target the vol is beyond the resolvable range and is
    reported as ``above_max``.
    """
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
        # At (or below) the zero-vol price: no time value, so the implied vol is zero.
        return SolveOutcome(0.0, STATUS_CONVERGED, 0, abs(target_price - price_at_floor),
                            vol_min, vol_max)

    price_at_ceiling = price_fn(vol_max)
    if target_price >= price_at_ceiling - price_tolerance:
        # Needs a vol beyond the search ceiling: unresolvable, treat as at the max.
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
        # With disp=False brentq reports non-convergence here (e.g. an iteration
        # budget too small for the tolerance) rather than raising — labeled, not a crash.
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
    """Solve the European implied vol for one option price; the common entry point.

    Builds the Black-76 ``price_fn`` and bounds, runs the scalar solve, and packages
    the result with ``k = ln(K/F)`` (Eq 6) and ``w = sigma**2 * T`` (Eq 7). Total
    return type: always an :class:`IvResult`; a failed solve is labeled, never raised.
    """
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
    """One option's inputs for a batch solve."""

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
    """Solve a batch of European implied vols, one independent scalar solve each.

    A thin, order-preserving wrapper over :func:`solve_iv`. Each contract is solved
    on its own (the scalar path is the readable, well-tested core), so one
    pathological quote yields its own labeled failure without sinking the batch.
    """
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
    """Project a converged result into A's stamped ``IvPoint`` contract.

    Raises :class:`ValueError` if the solve did not converge — a labeled failure is
    never emitted as an ``IvPoint`` (the contract requires a finite ``iv >= 0``).
    The stamp names the contract's own market-state snapshot as the source.
    """
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
