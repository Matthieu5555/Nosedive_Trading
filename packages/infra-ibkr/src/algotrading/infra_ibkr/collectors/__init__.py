"""IBKR market-data collection — two paths into one ``RawMarketEvent`` (ADR 0023/0024/0025).

- **Client Portal REST/WS** (ADR 0024, preferred): :class:`CpRestMarketDataAdapter` +
  :class:`CpRestDiscovery`, normalizing via :func:`snapshot_to_events`.
- **Nautilus TWS** (ADR 0025, fallback): :func:`quote_tick_to_events` /
  :func:`trade_tick_to_events`.

Both build events through the shared ``market_fields`` helper, so they emit identical rows for the
same observation (ADR 0024's equivalence bar).
"""

from .cp_rest_adapter import CpInstrument, CpRestMarketDataAdapter
from .cp_rest_close_capture import (
    CaptureTarget,
    CloseCaptureError,
    collect_live_basket,
    collect_target_basket,
)
from .cp_rest_constituent_capture import collect_index_and_constituents_basket
from .cp_rest_discovery import CpRestDiscovery, DiscoveryError
from .cp_rest_discovery_cache import (
    CachedChain,
    DiscoveryCache,
    DiscoveryCacheRow,
    revalidate_conids,
)
from .cp_rest_history import (
    BackfillResult,
    CpRestHistoryCollector,
    HistoryFetchError,
    HistoryRequest,
)
from .cp_rest_history_normalize import (
    HistoryNormalizeError,
    history_to_daily_bars,
    trade_date_of_bar,
)
from .cp_rest_index import (
    IndexConidError,
    ResolvedIndex,
    parse_index_conid,
    parse_option_months,
    resolve_index,
    resolve_index_conid,
)
from .cp_rest_normalize import snapshot_to_events
from .nautilus_normalize import (
    quote_tick_to_events,
    quote_ticks_to_events,
    trade_tick_to_events,
)

__all__ = [
    # Nautilus-TWS path (ADR 0025)
    "quote_tick_to_events",
    "quote_ticks_to_events",
    "trade_tick_to_events",
    # Client Portal REST path (ADR 0024)
    "CpInstrument",
    "CpRestMarketDataAdapter",
    "CpRestDiscovery",
    "DiscoveryError",
    "snapshot_to_events",
    # Runtime index conid resolution + the live EOD close basket source (WS 1C)
    "resolve_index",
    "resolve_index_conid",
    "parse_index_conid",
    "parse_option_months",
    "ResolvedIndex",
    "IndexConidError",
    "collect_live_basket",
    "collect_target_basket",
    "CaptureTarget",
    "collect_index_and_constituents_basket",
    "CloseCaptureError",
    # Discovery → conid cache (speed lever B/C)
    "DiscoveryCache",
    "DiscoveryCacheRow",
    "CachedChain",
    "revalidate_conids",
    # Historical OHLC backfill (ADR 0031)
    "CpRestHistoryCollector",
    "HistoryRequest",
    "BackfillResult",
    "HistoryFetchError",
    "history_to_daily_bars",
    "trade_date_of_bar",
    "HistoryNormalizeError",
]
