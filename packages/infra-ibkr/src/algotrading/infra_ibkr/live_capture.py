from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.orchestration.eod_runner import FiredIndex
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import ChainSelection

from .collectors.cp_rest_close_capture import collect_live_basket
from .collectors.cp_rest_constituent_capture import collect_index_and_constituents_basket
from .collectors.cp_rest_discovery_cache import DiscoveryCache
from .collectors.cp_rest_snapshot import WarmupConfig
from .connectivity.cp_rest_transport import SupportsRestGet
from .session_factory import (
    build_credentialed_session,
    build_gateway_session,
    gateway_requested,
)

_LOGGER = structlog.get_logger("ibkr.live_capture")


def _utc_today() -> date:
    return datetime.now(UTC).date()


def live_basket_source(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any | None = None,
    config: PlatformConfig | None = None,
    selection: ChainSelection | None = None,
    now: Callable[[], date] | None = None,
    store: ParquetStore | None = None,
    use_discovery_cache: bool = True,
) -> Callable[[FiredIndex, date], IndexBasket | None] | None:
    resolved_env = os.environ if env is None else env

    if transport is None:
        built = build_credentialed_session(resolved_env)
        if built is None:
            return None
        transport, _session = built

    if not isinstance(transport, SupportsRestGet):
        raise TypeError(f"live capture transport must support .get(...), got {transport!r}")

    resolved_config = config if config is not None else _load_config()
    today = now or _utc_today

    discovery_cache = (
        DiscoveryCache(store) if (store is not None and use_discovery_cache) else None
    )
    warmup = WarmupConfig() if use_discovery_cache else None

    capture_constituents = resolved_config.universe.capture_constituents
    widen = capture_constituents and store is not None

    def source(
        fired: FiredIndex, trade_date: date, correlation_id: str | None = None
    ) -> IndexBasket | None:
        current_day = today()
        if trade_date < current_day:
            _LOGGER.info(
                "ibkr.live_capture.skip_backfill_past_date",
                index=fired.entry.symbol,
                trade_date=trade_date.isoformat(),
                today=current_day.isoformat(),
                reason="live snapshot is current quotes; a past trade_date would be stale "
                "(no look-ahead) — option capture skipped, use the /history OHLC backfill",
            )
            return None
        if widen and store is not None:
            return collect_index_and_constituents_basket(
                transport,
                store=store,
                index=fired.entry,
                as_of=fired.as_of,
                next_open=fired.next_open,
                config=resolved_config,
                selection=selection,
                run_id=correlation_id,
                discovery_cache=discovery_cache,
                revalidate_cached_conids=discovery_cache is not None,
                warmup=warmup,
            )
        return collect_live_basket(
            transport,
            index=fired.entry,
            as_of=fired.as_of,
            next_open=fired.next_open,
            config=resolved_config,
            selection=selection,
            discovery_cache=discovery_cache,
            revalidate_cached_conids=discovery_cache is not None,
            warmup=warmup,
        )

    _LOGGER.info(
        "ibkr.live_capture.credentialed",
        reason="basket source bound",
        scope="index+constituents" if widen else "index-only",
    )
    return source


def gateway_basket_source(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any | None = None,
    config: PlatformConfig | None = None,
    selection: ChainSelection | None = None,
    now: Callable[[], date] | None = None,
    store: ParquetStore | None = None,
    use_discovery_cache: bool = True,
) -> Callable[[FiredIndex, date], IndexBasket | None] | None:
    resolved_env = os.environ if env is None else env
    if transport is None:
        if not gateway_requested(resolved_env):
            _LOGGER.info(
                "ibkr.live_capture.gateway_not_requested",
                reason="IBKR_CP_GATEWAY not set — not the local-Gateway path",
            )
            return None
        transport, _session = build_gateway_session(resolved_env)
    _LOGGER.info("ibkr.live_capture.gateway_requested", reason="local CP Gateway capture path")
    return live_basket_source(
        env=resolved_env,
        transport=transport,
        config=config,
        selection=selection,
        now=now,
        store=store,
        use_discovery_cache=use_discovery_cache,
    )


def _load_config() -> PlatformConfig:
    from pathlib import Path

    from algotrading.core.config.loader import load_platform_config

    repo_root = Path(__file__).resolve().parents[5]
    return load_platform_config(repo_root / "configs")
