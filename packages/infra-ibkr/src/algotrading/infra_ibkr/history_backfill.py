"""Wire the IBKR historical-OHLC backfill from ``.env`` credentials (ADR 0024/0031, WS 1C).

The live basket source (:mod:`live_capture`) captures *today's* option chain; it cannot reconstruct
a past session (CP REST has no historical option-quote endpoint). Past underlying history is the
**daily-OHLC** backfill: :class:`~.collectors.cp_rest_history.CpRestHistoryCollector` already
fetches, normalizes, persists, and resumes — what it lacked was a wiring that turns ``.env``
credentials into a collector and the index registry into the per-ticker requests. This module is
that wiring; the thin ``scripts/ohlc_backfill.py`` shim is its entrypoint.

Two seams, both injectable so the gate drives them against a fake transport with no network and no
secrets:

* :func:`build_history_collector` — credentialed collector or ``None`` (the no-op path), via the
  shared :func:`~.session_factory.build_credentialed_session`;
* :func:`history_requests_for` — resolve each enabled index's underlying conid (never the registry's
  ``conid: 0`` placeholder) and, optionally, its as-of constituents' equity conids, into the
  per-ticker :class:`~.collectors.cp_rest_history.HistoryRequest` list.
"""

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

# The workspace distribution this capture runs from — stamped onto every bar's provenance so a
# backfilled DailyBar is reproducible to the code that fetched it (ADR 0028).
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
    """Build the credentialed history collector, or ``None`` when the env is not configured.

    When ``transport`` is given it is used directly (the gate's already-authenticated fake gateway)
    and ``session`` supplies the established predicate (defaulting to always-established for a
    fake); otherwise the shared :func:`build_credentialed_session` acquires the LST, signs it,
    and opens the brokerage session, returning ``None`` if the environment carries no IBKR CP OAuth
    artifacts. ``calc_ts`` stamps every bar's provenance (the capture instant); ``config`` defaults
    to the loaded IBKR history config and supplies the per-bar ``config_hash``.
    """
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
    """Resolve the per-ticker history requests for the enabled indices (and their constituents).

    For each in-scope index, resolves the index underlying's conid at fetch time
    (:func:`resolve_index` — never the registry's ``conid: 0`` placeholder) and, when
    ``include_constituents``, its as-of basket (1A :func:`members`) with each constituent's equity
    conid via ``/iserver/secdef/search`` (:meth:`CpRestDiscovery.underlying_conid`). Each ticker
    appears once; ``period`` is IBKR's window string (e.g. ``"5y"``). ``as_of_date`` is the
    membership knowledge/effective date — point-in-time, never the latest-applied basket.
    """
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
        # Verified conid pins first, so a pinned label wins over a same-label search result. These
        # are the constituents the `/secdef/search` door cannot resolve unambiguously (a ticker two
        # listings share, e.g. Euronext-Paris SAN=Sanofi vs BM SAN=Santander) — fetched straight by
        # their unique conid, no search. The pin's label is the underlying key the bars store under.
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
            # Resolve the constituent's equity conid; a name IBKR does not list (a non-US ticker
            # under a different symbol, a delisted member) must not abort the whole sweep — log it
            # and move on. The ticker simply gets no history this run.
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
