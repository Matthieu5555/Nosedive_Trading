from __future__ import annotations

from datetime import datetime

from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import PricingResult

from .american import price_american
from .black76 import price_european
from .dollar_greeks import dollar_greeks
from .state import PriceGreeks, PricingState

_PRICING_RESULT_MONETIZATION = MonetizationConfig(version="monetization-default")

PRICER_VERSION = "black76-lr-1.0.0"


def price(state: PricingState, *, steps: int | None = None) -> PriceGreeks:
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
        rt_vega=greeks.rt_vega,
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
        rt_vega=greeks.rt_vega,
        dollar_rt_vega=monetized.dollar_rt_vega,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
