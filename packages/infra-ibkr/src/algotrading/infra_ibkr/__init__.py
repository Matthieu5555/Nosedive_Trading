"""IBKR broker adapter — IBKR on Nautilus's runtime (ADR 0023/0025).

The live market-data path is Nautilus's shipped InteractiveBrokers adapter: build its
data-client config with :func:`build_data_client_config`, and normalize the
``QuoteTick``/``TradeTick`` it delivers into our immutable ``RawMarketEvent`` with the
``quote_tick_to_events`` / ``trade_tick_to_events`` seam. The hand-rolled ``ib_async``
modules are superseded and kept only until C5 removes them.
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
from algotrading.infra_ibkr.live_capture import live_basket_source

__all__ = [
    "IbkrExtraNotInstalled",
    "build_data_client_config",
    "quote_tick_to_events",
    "quote_ticks_to_events",
    "trade_tick_to_events",
    # The live EOD close-capture BasketSource wiring (WS 1C)
    "live_basket_source",
]
