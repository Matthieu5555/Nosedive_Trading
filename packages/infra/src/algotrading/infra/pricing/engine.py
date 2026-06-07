"""Public pricing entry points: style dispatch, and the A-contract adapter.

``price`` is the single call that turns a state vector into price and Greeks,
dispatching on exercise style. ``pricing_result`` projects a :class:`PriceGreeks`
into Workstream A's ``PricingResult`` contract, adding the monetized (cash) Greeks
and attaching a caller-supplied provenance stamp. The stamp is built by the caller
(with an injected ``calc_ts``), never here, so the pricer stays a pure function of
its inputs with no wall-clock read.

Cash-Greek conventions, per unit of underlying (the risk engine multiplies by the
contract multiplier and the held quantity). The canonical definitions and the two
convention forks live in :mod:`dollar_greeks` (ADR 0036); this adapter fills the
``PricingResult`` dollar layer with the pinned-default units:

* ``dollar_delta = delta * spot`` — dollar value change for a 1.0 move in spot.
* ``dollar_gamma = gamma * spot**2`` — dollar gamma; P&L of a move dS is about
  ``0.5 * dollar_gamma * (dS / spot)**2``.
* ``dollar_vega = vega * 0.01`` — dollar value change for a one-vol-point (1%) move.
* ``dollar_theta = theta / 365`` — per calendar day (the pinned default day-count).
* ``dollar_rho = rho * 0.01`` — per 1% rate.
"""

from __future__ import annotations

from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import PricingResult

from .american import price_american
from .black76 import price_european
from .state import PriceGreeks, PricingState

# Bump only on a real change to the price or Greek formulas, never on config. The
# "lr" tag names the American engine's lattice (Leisen-Reimer); the European leg is
# closed-form Black-76. (Was "black76-crr-1.0.0" — a misnomer; the engine has never
# been Cox-Ross-Rubinstein. Corrected 2026-06-02; see ADR 0004 and the release note.)
PRICER_VERSION = "black76-lr-1.0.0"


def price(state: PricingState, *, steps: int | None = None) -> PriceGreeks:
    """Price a state vector, dispatching European vs American on the exercise style.

    ``steps`` applies only to the American lattice; left unset, it delegates to
    :func:`pricing.american.price_american`'s own default, so dispatching through
    ``price`` is identical to calling that engine directly (the dispatch test pins
    this). The European engine is closed-form and ignores ``steps``.
    """
    if state.exercise_style == "american":
        if steps is None:
            return price_american(state)
        return price_american(state, steps=steps)
    return price_european(state)


def pricing_result(
    state: PricingState,
    greeks: PriceGreeks,
    *,
    snapshot_ts: datetime,
    contract_key: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> PricingResult:
    """Project price and Greeks into A's ``PricingResult`` contract with dollar Greeks."""
    return PricingResult(
        snapshot_ts=snapshot_ts,
        contract_key=contract_key,
        pricer_version=PRICER_VERSION,
        price=greeks.price,
        delta=greeks.delta,
        gamma=greeks.gamma,
        vega=greeks.vega,
        theta=greeks.theta,
        rho=greeks.rho,
        dollar_delta=greeks.delta * state.spot,
        dollar_gamma=greeks.gamma * state.spot * state.spot,
        dollar_vega=greeks.vega * 0.01,
        dollar_theta=greeks.theta / 365.0,
        dollar_rho=greeks.rho * 0.01,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
