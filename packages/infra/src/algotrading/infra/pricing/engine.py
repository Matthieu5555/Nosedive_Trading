"""Public pricing entry points: style dispatch, and the A-contract adapter.

``price`` is the single call that turns a state vector into price and Greeks,
dispatching on exercise style. ``pricing_result`` projects a :class:`PriceGreeks`
into Workstream A's ``PricingResult`` contract, adding the monetized (cash) Greeks
and attaching a caller-supplied provenance stamp. The stamp is built by the caller
(with an injected ``calc_ts``), never here, so the pricer stays a pure function of
its inputs with no wall-clock read.

Cash-Greek conventions, per unit of underlying (the risk engine multiplies by the
contract multiplier and the held quantity). The canonical definitions and the two
convention forks live in :mod:`dollar_greeks` (ADR 0036), and this adapter fills the
``PricingResult`` dollar layer by calling that one home with the pinned-default
:class:`~algotrading.core.config.MonetizationConfig` — so this emission path and the
surface-projection path (which calls the same :func:`pricing.dollar_greeks`) cannot
disagree on a monetized number for the same option. Under the pinned defaults
(``gamma_normalisation="one_pct"``, ``theta_day_count=365``):

* ``dollar_delta = delta * spot`` — dollar value change for a 1.0 move in spot.
* ``dollar_gamma = gamma * spot**2 / 100`` — Gamma\\$ per **1% move** (the data-dictionary
  / blueprint default ``Γ·S²/100``), not the per-\\$1 ``Γ·S²``.
* ``dollar_vega = vega * 0.01`` — dollar value change for a one-vol-point (1%) move.
* ``dollar_theta = theta / 365`` — per calendar day (the pinned default day-count).
* ``dollar_rho = rho * 0.01`` — per 1% rate.
* ``dollar_vanna = vanna * spot * 0.01`` — change in Delta\\$ per 1 vol point.
* ``dollar_volga = volga * 0.01**2`` — change in Vega\\$ per 1 vol point.
* ``dollar_charm = charm * spot / 365`` — change in Delta\\$ per calendar day.
"""

from __future__ import annotations

from datetime import datetime

from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import PricingResult

from .american import price_american
from .black76 import price_european
from .dollar_greeks import dollar_greeks
from .state import PriceGreeks, PricingState

# The pinned-default $-Greek conventions (ADR 0036): gamma per 1% move (Γ·S²/100),
# theta per 365-day calendar. Bound once here so the projection path and this
# ``PricingResult`` adapter monetize the same option identically; a deployment that
# wants the trading-day / per-$1 forks builds its own config and projects through
# :func:`pricing.dollar_greeks` directly.
_PRICING_RESULT_MONETIZATION = MonetizationConfig(version="monetization-default")

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
    """Project price and Greeks into A's ``PricingResult`` contract with dollar Greeks.

    The dollar layer is derived through the single canonical home
    :func:`pricing.dollar_greeks` under the pinned-default
    :data:`_PRICING_RESULT_MONETIZATION`, so it agrees by construction with the
    surface-projection path that monetizes the same option through the same function.
    """
    monetized = dollar_greeks(
        delta=greeks.delta,
        gamma=greeks.gamma,
        vega=greeks.vega,
        theta=greeks.theta,
        rho=greeks.rho,
        spot=state.spot,
        vanna=greeks.vanna,
        volga=greeks.volga,
        charm=greeks.charm,
        multiplier=1.0,
        quantity=1.0,
        config=_PRICING_RESULT_MONETIZATION,
    )
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
        dollar_delta=monetized.dollar_delta,
        dollar_gamma=monetized.dollar_gamma,
        dollar_vega=monetized.dollar_vega,
        dollar_theta=monetized.dollar_theta,
        dollar_rho=monetized.dollar_rho,
        vanna=greeks.vanna,
        volga=greeks.volga,
        charm=greeks.charm,
        dollar_vanna=monetized.dollar_vanna,
        dollar_volga=monetized.dollar_volga,
        dollar_charm=monetized.dollar_charm,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
