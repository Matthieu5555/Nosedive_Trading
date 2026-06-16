from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import structlog
from algotrading.core.provenance import ProvenanceStamp, code_version, source_ref, stamp
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import IndexRegistry
from algotrading.infra.universe.membership import members

from .collectors.cp_rest_discovery import CpRestDiscovery
from .collectors.cp_rest_history import CpRestHistoryCollector, HistoryRequest
from .collectors.cp_rest_index import resolve_index
from .config import IbkrHistoryConfig, load_ibkr_history_config
from .session_factory import build_credentialed_session

_LOGGER = structlog.get_logger("ibkr.history_backfill")

_DISTRIBUTION = "algotrading-infra-ibkr"
_PROVIDER = "IBKR"


def build_history_collector(
    *,
    store: ParquetStore,
    calc_ts: datetime,
    env: dict[str, str] | None = None,
    transport: Any | None = None,
    session: Any | None = None,
    config: IbkrHistoryConfig | None = None,
) -> CpRestHistoryCollector | None:
    if transport is None:
        built = build_credentialed_session(env)
        if built is None:
            return None
        transport, session = built

    cfg = config if config is not None else load_ibkr_history_config()
    is_established = session.established if session is not None else (lambda: True)

    def provenance_for(underlying: str, trade_date: date) -> ProvenanceStamp:
        return stamp(
            calc_ts=calc_ts,
            code_version=code_version(_DISTRIBUTION),
            config_hashes={"ibkr_history": cfg.config_hash},
            source_records=(
                source_ref("raw_market_events", "ibkr-history", f"{underlying}-{trade_date}"),
            ),
            source_timestamps=(calc_ts,),
        )

    _LOGGER.info("ibkr.history_backfill.collector_bound", provider=_PROVIDER)
    return CpRestHistoryCollector(
        transport=transport,
        store=store,
        config=cfg,
        provider=_PROVIDER,
        is_established=is_established,
        provenance_for=provenance_for,
        sleep=time.sleep,
    )


def history_requests_for(
    *,
    store: ParquetStore,
    registry: IndexRegistry,
    transport: Any,
    period: str,
    as_of_date: date,
    index: str | None = None,
    include_constituents: bool = True,
) -> list[HistoryRequest]:
    entries = [registry.get(index)] if index is not None else list(registry.enabled_indices())
    requests: list[HistoryRequest] = []
    seen: set[str] = set()
    for entry in entries:
        index_conid = resolve_index(
            transport, symbol=entry.ibkr_search_symbol, exchange=entry.ibkr.exchange
        ).conid
        if entry.symbol not in seen:
            requests.append(HistoryRequest(entry.symbol, index_conid, period))
            seen.add(entry.symbol)
        if not include_constituents:
            continue
        for label, conid in entry.ibkr.constituent_conids:
            if label in seen:
                continue
            seen.add(label)
            requests.append(HistoryRequest(label, conid, period))
        discovery = CpRestDiscovery(transport, currency=entry.currency)
        for member in members(store, entry.symbol, as_of_date):
            if member.constituent in seen:
                continue
            seen.add(member.constituent)
            try:
                conid = discovery.underlying_conid(member.constituent)
            except Exception as exc:  # noqa: BLE001 — one unresolved constituent is non-fatal
                _LOGGER.info(
                    "ibkr.history_backfill.constituent_unresolved",
                    index=entry.symbol,
                    constituent=member.constituent,
                    error=str(exc),
                )
                continue
            requests.append(HistoryRequest(member.constituent, conid, period))
    _LOGGER.info(
        "ibkr.history_backfill.requests_resolved",
        index_count=len(entries),
        ticker_count=len(requests),
        include_constituents=include_constituents,
    )
    return requests
