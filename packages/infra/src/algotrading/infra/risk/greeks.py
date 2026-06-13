"""Per-position price, Greeks, and monetized sensitivities (roadmap step 11).

The production path takes Greeks straight from the frozen pricer — analytic for
European, the lattice for American — so risk never re-derives a sensitivity the
pricer already exposes. :func:`central_difference_greeks` is the independent
cross-check: it differences the *pricer's price* using the shared
:data:`bumps.DEFAULT_BUMPS`, and the test that analytic and central-difference
Greeks agree is what catches a sign or unit error even where analytic Greeks are
used (``documentation/blueprint/05-math-notes.md`` §4: "Finite-difference validation
should still exist even when analytic Greeks are used"; ``tasks/TESTING.md``). Both
this and the scenario engine's local approximation draw their bump from one versioned
source, so they cannot diverge.

Dollar (monetized) Greeks are **not** computed here. The single canonical home is
``pricing/dollar_greeks.py`` (per-1% gamma ``Γ·S²/100`` / per-365 theta, config-flagged),
which the projection and every Phase-2 aggregation consume — so there is one $-convention
and it cannot drift. This module emits only the per-unit Greeks and the contract-level
position sensitivities below.

Contract-level (un-dollarized) position sensitivities are ``per_unit * multiplier *
quantity`` — share/contract-equivalent, so contracts with different multipliers
sum coherently into the aggregate.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass

from algotrading.infra.pricing import PriceGreeks, price

from .bumps import DEFAULT_BUMPS, BumpSpec
from .valuation import ContractValuationInput, ValuationError, pricing_state_for


@dataclass(frozen=True, slots=True)
class PositionRisk:
    """One position's full risk line: its inputs, per-unit Greeks, and scalings.

    The valuation input is carried verbatim so a line is self-describing — every
    monetized number can be traced back to the spot, vol, and multiplier that
    produced it without re-joining against anything. Position-level and dollar
    sensitivities are derived properties, so they cannot drift from the per-unit
    Greeks they scale.
    """

    portfolio_id: str
    quantity: float
    valuation: ContractValuationInput
    greeks: PriceGreeks

    @property
    def contract_key(self) -> str:
        return self.valuation.contract_key

    @property
    def underlying(self) -> str:
        return self.valuation.underlying

    @property
    def scale(self) -> float:
        """Contract multiplier times signed held quantity — the line's scale factor."""
        return self.valuation.multiplier * self.quantity

    @property
    def market_value(self) -> float:
        return self.greeks.price * self.scale

    @property
    def position_delta(self) -> float:
        return self.greeks.delta * self.scale

    @property
    def position_gamma(self) -> float:
        return self.greeks.gamma * self.scale

    @property
    def position_vega(self) -> float:
        return self.greeks.vega * self.scale

    @property
    def position_theta(self) -> float:
        return self.greeks.theta * self.scale


def position_risk(
    *,
    portfolio_id: str,
    quantity: float,
    valuation: ContractValuationInput,
    steps: int | None = None,
) -> PositionRisk:
    """Price one position and return its full risk line.

    ``steps`` is forwarded to the American lattice and ignored for European
    contracts, mirroring :func:`algotrading.infra.pricing.price`.

    ``quantity`` is guarded here at the public line-level entry point against a
    non-finite value: a NaN/inf quantity is malformed input that would propagate
    silently into ``scale`` and every Greek as NaN, so it is refused with a labeled
    :class:`ValuationError` carrying the offending value. A *zero* quantity is NOT
    refused — a net-flat line is a legitimate degenerate (scale-0) result that the
    attribution and aggregation paths rely on, so it is priced normally.
    """
    if not math.isfinite(quantity):
        raise ValuationError("quantity", quantity, "must be a finite number")
    state = pricing_state_for(valuation)
    greeks = price(state, steps=steps) if steps is not None else price(state)
    return PositionRisk(
        portfolio_id=portfolio_id, quantity=quantity, valuation=valuation, greeks=greeks
    )


class LotConsistencyError(Exception):
    """Two lots of one contract carry different market state — corrupt input.

    Lots of the same ``(portfolio_id, contract_key)`` are the same contract at one
    snapshot, so their resolved :class:`ContractValuationInput` must be identical
    (it is the contract's market state, independent of how much is held). A
    divergence means the join upstream mismatched, and netting them would silently
    pick one — so it is raised, not absorbed.
    """

    def __init__(self, portfolio_id: str, contract_key: str) -> None:
        self.portfolio_id = portfolio_id
        self.contract_key = contract_key
        super().__init__(
            f"lots of {contract_key!r} in portfolio {portfolio_id!r} disagree on market state"
        )


def net_lots(lines: Iterable[PositionRisk]) -> list[PositionRisk]:
    """Net same-contract lots within a portfolio into one canonical line per contract.

    The :class:`algotrading.infra.contracts.Position` contract carries a ``source``
    field, so the same ``(portfolio_id, contract_key)`` legitimately appears as several
    lots — a broker holding plus a hypothetical overlay, say. But the *derived* contracts
    have no lot dimension: ``RiskAggregate`` is net-per-group and ``ScenarioResult`` is
    keyed by ``(portfolio, scenario, contract)``. So the line is the contract, by
    construction. Netting here sums the signed quantities (the only thing that differs
    between lots), keeps the shared market state, and returns one line per
    ``(portfolio_id, contract_key)`` sorted by contract key — making every line-level and
    scenario-cell ordering a pure function of the input *set*, which byte-identical replay
    depends on.

    Raises :class:`LotConsistencyError` if two lots of one contract disagree on their
    valuation — that is a corrupt join, not something to silently collapse. A
    net-flat contract (quantities that cancel) is kept as a zero-quantity line, not
    dropped: a contract that is in the book net-flat is a fact, and dropping it would
    make the cell count depend on whether lots happen to cancel.
    """
    grouped: dict[tuple[str, str], list[PositionRisk]] = {}
    for line in lines:
        grouped.setdefault((line.portfolio_id, line.contract_key), []).append(line)
    netted: list[PositionRisk] = []
    for (portfolio_id, contract_key), lots in grouped.items():
        canonical = lots[0]
        if any(lot.valuation != canonical.valuation for lot in lots[1:]):
            raise LotConsistencyError(portfolio_id, contract_key)
        if len(lots) == 1:
            netted.append(canonical)
            continue
        # fsum so the netted quantity is independent of the order the lots arrived in.
        total_quantity = math.fsum(lot.quantity for lot in lots)
        netted.append(dataclasses.replace(canonical, quantity=total_quantity))
    netted.sort(key=lambda line: line.contract_key)
    return netted


def _price_of(valuation: ContractValuationInput) -> float:
    """The pricer's price for a valuation input (the function we finite-difference)."""
    return price(pricing_state_for(valuation)).price


def central_difference_greeks(
    valuation: ContractValuationInput, *, bumps: BumpSpec = DEFAULT_BUMPS
) -> PriceGreeks:
    """Greeks by central difference of the pricer's price, using the shared bumps.

    The independent cross-check for :func:`position_risk`'s analytic Greeks. Spot
    Greeks hold carry fixed (so the forward tracks spot, as it does in a real spot
    move); vega bumps vol; theta is ``-dPrice/dT`` at fixed spot with the discount
    factor tracking the implied rate, matching the pricer's per-year convention.
    ``rho`` is filled from the forward-fixed identity ``-T * price`` (it is not a
    finite-difference cross-check target), so the four named Greeks are the
    differenced quantities.
    """
    spot = valuation.spot
    h_first = bumps.spot_first(spot)
    h_second = bumps.spot_second(spot)

    def with_spot(new_spot: float) -> ContractValuationInput:
        return dataclasses.replace(valuation, spot=new_spot)

    base_price = _price_of(valuation)
    delta = (_price_of(with_spot(spot + h_first)) - _price_of(with_spot(spot - h_first))) / (
        2.0 * h_first
    )
    gamma = (
        _price_of(with_spot(spot + h_second))
        - 2.0 * base_price
        + _price_of(with_spot(spot - h_second))
    ) / (h_second * h_second)

    h_vol = bumps.vol_abs
    vega = (
        _price_of(dataclasses.replace(valuation, volatility=valuation.volatility + h_vol))
        - _price_of(dataclasses.replace(valuation, volatility=valuation.volatility - h_vol))
    ) / (2.0 * h_vol)

    rate = valuation.implied_rate
    h_t = bumps.time_abs

    def with_maturity(new_t: float) -> ContractValuationInput:
        return dataclasses.replace(
            valuation, maturity_years=new_t, discount_factor=math.exp(-rate * new_t)
        )

    theta = -(
        _price_of(with_maturity(valuation.maturity_years + h_t))
        - _price_of(with_maturity(valuation.maturity_years - h_t))
    ) / (2.0 * h_t)

    # This is the first-order cross-check (delta/gamma/vega/theta); it does not
    # difference the second-order set, so vanna/volga/charm stay explicitly 0.0 here.
    return PriceGreeks(
        price=base_price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=-valuation.maturity_years * base_price,
        vanna=0.0,
        volga=0.0,
        charm=0.0,
    )
