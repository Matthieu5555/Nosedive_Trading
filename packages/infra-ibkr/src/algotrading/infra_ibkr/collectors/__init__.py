"""IBKR market-data collection (ADR 0023/0025).

The live path is Nautilus's InteractiveBrokers adapter; this package's seam is the pure
tick → :class:`RawMarketEvent` normalizer exported here. The hand-rolled ``ib_async`` push
adapter/discovery (``ibkr_adapter``/``ibkr_discovery``) are **superseded** — kept as files
until C5 removes them, reached only by direct import (they require the old SDK), and not
surfaced here so this package imports with no broker SDK present.
"""

from .nautilus_normalize import (
    quote_tick_to_events,
    quote_ticks_to_events,
    trade_tick_to_events,
)

__all__ = [
    "quote_tick_to_events",
    "quote_ticks_to_events",
    "trade_tick_to_events",
]
