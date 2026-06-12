"""IBKR broker adapter — IBKR on Nautilus's runtime (ADR 0023/0025).

The live market-data path is Nautilus's shipped InteractiveBrokers adapter: build its
data-client config with :func:`build_data_client_config`, and normalize the
``QuoteTick``/``TradeTick`` it delivers into our immutable ``RawMarketEvent`` with the
``quote_tick_to_events`` / ``trade_tick_to_events`` seam.
"""

from algotrading.infra_ibkr.collectors import (
    quote_tick_to_events,
    quote_ticks_to_events,
    trade_tick_to_events,
)
from algotrading.infra_ibkr.connectivity import (
    IbkrExtraNotInstalled,
    build_data_client_config,
)
from algotrading.infra_ibkr.live_capture import gateway_basket_source, live_basket_source

__all__ = [
    "IbkrExtraNotInstalled",
    "build_data_client_config",
    "quote_tick_to_events",
    "quote_ticks_to_events",
    "trade_tick_to_events",
    # The live EOD close-capture BasketSource wiring (WS 1C): hosted OAuth + local CP Gateway
    "live_basket_source",
    "gateway_basket_source",
]
