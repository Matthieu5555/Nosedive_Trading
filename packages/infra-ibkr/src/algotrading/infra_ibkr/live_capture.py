"""Wire the live EOD ``BasketSource`` from IBKR CP REST credentials (ADR 0024/0031, WS 1C).

The EOD runner (``algotrading.infra.orchestration.eod_runner``) sits *below* this broker leaf in
the layering, so it cannot import IBKR: it exposes the transport-agnostic ``BasketSource`` seam
and a ``basket_source`` parameter on ``build_default_deps`` / ``default_stages_builder`` instead.
This module is the IBKR side of that seam — the one place that turns ``.env`` credentials into a
live, authenticated, OAuth-signed CP REST session and binds it into a ``BasketSource`` the runner
threads through unchanged.

The production selection lives here, explicit and logged: :func:`live_basket_source` returns a
real ``collect_live``-backed source when the environment is credentialed, and ``None`` when it is
not — the caller (the ``scripts/eod_run.py`` shim, the only place that legitimately sees both the
runner and this broker leaf) then uses the source if present, or falls back to the runner's own
``_empty_basket_source`` (a clean no-capture day, exit 0). A non-secret runner and the gate stay
green; a credentialed live gateway captures a real basket.

Transport stays on CP REST (ADR 0024/0031): the LST exchange + the per-request HMAC signer, never
a Nautilus ``TradingNode`` (REP7, blocked). The only socket is the real ``httpx`` ``post`` and the
signed transport; both are injectable, so the gate exercises the *selection* and the *binding*
against fakes with no network and no secrets.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.orchestration.eod_runner import FiredIndex
from algotrading.infra.universe import ChainSelection

from .collectors.cp_rest_close_capture import collect_live_basket
from .session_factory import (
    build_credentialed_session,
    build_gateway_session,
    gateway_requested,
)

_LOGGER = structlog.get_logger("ibkr.live_capture")


def _utc_today() -> date:
    """The current UTC calendar day — the default 'now' the no-look-ahead guard compares to."""
    return datetime.now(UTC).date()


def _supports_get(obj: object) -> bool:
    return callable(getattr(obj, "get", None))


def live_basket_source(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any | None = None,
    config: PlatformConfig | None = None,
    selection: ChainSelection | None = None,
    now: Callable[[], date] | None = None,
) -> Callable[[FiredIndex, date], IndexBasket | None] | None:
    """Build the credentialed live ``BasketSource``, or ``None`` when not configured.

    The production live-vs-empty selection, in one place and logged. When the environment carries
    every required IBKR CP OAuth artifact (:func:`credentials_present`), this acquires a Live
    Session Token, builds the OAuth-signed CP REST transport, opens + waits for the brokerage
    session, and returns a ``(FiredIndex, trade_date) -> IndexBasket | None`` source that captures
    each fired index's EOD close basket (:func:`collect_live_basket`). When the environment is not
    credentialed it returns ``None`` — the caller falls back to the runner's empty no-capture
    source, so the gate and any non-secret runner stay green.

    **No look-ahead.** The CP REST path is a *snapshot of current quotes*; it cannot reconstruct a
    past session's chain (CP REST has no historical option-quote endpoint). So the bound source
    captures only when ``trade_date`` is the current session day (``now()``): a past ``trade_date``
    (a catch-up/backfill fire, or a fire that slipped past UTC midnight) returns ``None`` rather
    than stamping today's quotes at a past close — the staleness that would violate the as-of
    invariant. Past-date underlying history is served by the ``/iserver/marketdata/history`` OHLC
    backfill (``CpRestHistoryCollector``), not this source. ``now`` defaults to the UTC clock and is
    injectable so a fixed-date fire can be tested without the wall clock.

    ``transport`` is injectable so the gate drives an already-authenticated fake CP REST gateway
    (the live LST/socket path is bypassed): when a transport is supplied the credential gate is
    skipped and the source is bound directly over it. ``config`` defaults to the loaded platform
    config; ``selection`` to the capture selection derived from it.
    """
    resolved_env = os.environ if env is None else env

    if transport is None:
        built = build_credentialed_session(resolved_env)
        if built is None:  # not credentialed (logged by the factory) — the empty no-capture path
            return None
        transport, _session = built

    if not _supports_get(transport):
        raise TypeError(f"live capture transport must support .get(...), got {transport!r}")

    resolved_config = config if config is not None else _load_config()
    today = now or _utc_today

    def source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
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
        return collect_live_basket(
            transport,
            index=fired.entry,
            as_of=fired.as_of,
            config=resolved_config,
            selection=selection,
        )

    _LOGGER.info("ibkr.live_capture.credentialed", reason="collect_live basket source bound")
    return source


def gateway_basket_source(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any | None = None,
    config: PlatformConfig | None = None,
    selection: ChainSelection | None = None,
    now: Callable[[], date] | None = None,
) -> Callable[[FiredIndex, date], IndexBasket | None] | None:
    """Build the live ``BasketSource`` over the local CP Gateway, or ``None`` when not requested.

    The local-Gateway counterpart of :func:`live_basket_source`: instead of keying on the
    ``IBKR_CP_*`` OAuth artifacts it keys on the explicit ``IBKR_CP_GATEWAY`` opt-in flag
    (:func:`gateway_requested`). When set, it builds + establishes a cookie-session CP REST
    transport against the running ``clientportal.gw`` (:func:`build_gateway_session`) and binds the
    same ``collect_live`` capture over it — the path that needs **no** Self-Service OAuth enrolment,
    only a browser-logged-in Gateway. When the flag is not set it returns ``None`` so the caller
    falls through to the OAuth path (or the empty no-capture default).

    Binding, the no-look-ahead guard, and the ``config``/``selection``/``now`` defaults are
    :func:`live_basket_source`'s — this function only swaps the *authentication* (Gateway cookie vs
    LST signer) and then delegates. ``transport`` is injectable so the gate drives an
    already-established fake Gateway without the establish handshake or a socket.
    """
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
        env=resolved_env, transport=transport, config=config, selection=selection, now=now
    )


def _load_config() -> PlatformConfig:
    """Load the platform config from the repo ``configs/`` (live path only)."""
    from pathlib import Path

    from algotrading.core.config.loader import load_platform_config

    # This file: packages/infra-ibkr/src/algotrading/infra_ibkr/live_capture.py
    # parents[3] == packages/infra-ibkr ; parents[5] == the repo root that holds configs/.
    repo_root = Path(__file__).resolve().parents[5]
    return load_platform_config(repo_root / "configs")
