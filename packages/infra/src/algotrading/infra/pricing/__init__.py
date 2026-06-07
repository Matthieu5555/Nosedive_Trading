"""The pricing engine — the only module that turns a state vector into a price.

This is the frozen pricing interface the IV solver (M2) and the risk engine (M3)
build against. Import the typed state vector, the price/Greeks result, the
``price*`` functions, and the ``pricing_result`` contract adapter from here.

    from algotrading.infra.pricing import PricingState, PriceGreeks, price, pricing_result

Unit and Greek conventions live on :mod:`pricing.state`; the American lattice and
its Bjerksund-Stensland fast path live in :mod:`pricing.american`. The pricer label
is ``PRICER_VERSION = "black76-lr-1.0.0"`` — closed-form Black-76 for the European
leg, a QuantLib Leisen-Reimer lattice for the American leg.
"""

from __future__ import annotations

from .american import bjerksund_stensland_price, price_american
from .black76 import price_european
from .dollar_greeks import (
    UNIT_STRINGS,
    DollarGreeks,
    dollar_greeks,
    gamma_unit_string,
    theta_unit_string,
)
from .engine import PRICER_VERSION, price, pricing_result
from .state import (
    EXERCISE_STYLES,
    PriceGreeks,
    PricingError,
    PricingState,
    from_forward,
    from_spot,
)

__all__ = [
    "EXERCISE_STYLES",
    "PRICER_VERSION",
    "UNIT_STRINGS",
    "DollarGreeks",
    "PriceGreeks",
    "PricingError",
    "PricingState",
    "bjerksund_stensland_price",
    "dollar_greeks",
    "from_forward",
    "from_spot",
    "gamma_unit_string",
    "price",
    "price_american",
    "price_european",
    "pricing_result",
    "theta_unit_string",
]
