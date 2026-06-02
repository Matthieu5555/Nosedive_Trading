"""The pricing engine — the only module that turns a state vector into a price.

This is the frozen interface Workstream C's IV solver and Workstream D's risk
engine build against. Import the typed state vector, the price/Greeks result, the
``price*`` functions, and the ``pricing_result`` contract adapter from here.

    from pricing import PricingState, PriceGreeks, price, pricing_result

Unit and Greek conventions live on :mod:`pricing.state`; the American lattice and
its Bjerksund-Stensland fast path live in :mod:`pricing.american`.
"""

from __future__ import annotations

from .american import bjerksund_stensland_price, price_american
from .black76 import price_european
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
    "PriceGreeks",
    "PricingError",
    "PricingState",
    "bjerksund_stensland_price",
    "from_forward",
    "from_spot",
    "price",
    "price_american",
    "price_european",
    "pricing_result",
]
