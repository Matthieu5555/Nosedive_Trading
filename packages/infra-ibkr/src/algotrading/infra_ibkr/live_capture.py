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
from datetime import date
from typing import Any

import structlog
from algotrading.core.config import PlatformConfig
from algotrading.infra.actor import IndexBasket
from algotrading.infra.orchestration.eod_runner import FiredIndex
from algotrading.infra.universe import ChainSelection

from .collectors.cp_rest_close_capture import collect_live_basket
from .connectivity.cp_rest_credentials import (
    credentials_present,
    load_lst_consumer,
    make_lst_http_post,
)
from .connectivity.cp_rest_lst import build_signed_cp_rest_transport
from .connectivity.cp_rest_session import CpRestSession

_LOGGER = structlog.get_logger("ibkr.live_capture")

# The hosted CP Web API base the OAuth path targets (api.ibkr.com, not localhost:5000). A single
# internal default; the operator overrides it via IBKR_CP_BASE_URL when pointing at a test host.
_DEFAULT_BASE_URL = "https://api.ibkr.com/v1/api"
ENV_BASE_URL = "IBKR_CP_BASE_URL"

# The established-session wait budget for the live brokerage session (ssodh/init → established).
_ESTABLISH_MAX_POLLS = 30
_ESTABLISH_POLL_SECONDS = 1.0


def _supports_get(obj: object) -> bool:
    return callable(getattr(obj, "get", None))


def live_basket_source(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any | None = None,
    config: PlatformConfig | None = None,
    selection: ChainSelection | None = None,
) -> Callable[[FiredIndex, date], IndexBasket | None] | None:
    """Build the credentialed live ``BasketSource``, or ``None`` when not configured.

    The production live-vs-empty selection, in one place and logged. When the environment carries
    every required IBKR CP OAuth artifact (:func:`credentials_present`), this acquires a Live
    Session Token, builds the OAuth-signed CP REST transport, opens + waits for the brokerage
    session, and returns a ``(FiredIndex, trade_date) -> IndexBasket | None`` source that captures
    each fired index's EOD close basket (:func:`collect_live_basket`). When the environment is not
    credentialed it returns ``None`` — the caller falls back to the runner's empty no-capture
    source, so the gate and any non-secret runner stay green.

    ``transport`` is injectable so the gate drives an already-authenticated fake CP REST gateway
    (the live LST/socket path is bypassed): when a transport is supplied the credential gate is
    skipped and the source is bound directly over it. ``config`` defaults to the loaded platform
    config; ``selection`` to the capture selection derived from it.
    """
    resolved_env = os.environ if env is None else env

    if transport is None:
        if not credentials_present(resolved_env):
            _LOGGER.info(
                "ibkr.live_capture.not_credentialed",
                reason="no IBKR CP OAuth artifacts in environment; empty no-capture path",
            )
            return None
        transport = _build_live_transport(resolved_env)

    if not _supports_get(transport):
        raise TypeError(f"live capture transport must support .get(...), got {transport!r}")

    resolved_config = config if config is not None else _load_config()

    def source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
        return collect_live_basket(
            transport,
            index=fired.entry,
            as_of=fired.as_of,
            config=resolved_config,
            selection=selection,
        )

    _LOGGER.info("ibkr.live_capture.credentialed", reason="collect_live basket source bound")
    return source


def _build_live_transport(env: Mapping[str, str]) -> Any:
    """Acquire the LST, build the signed transport, and open the brokerage session (live path).

    Reached only when the environment is credentialed and no transport was injected — i.e. a real
    fire against the hosted gateway. Never exercised under the gate (every test injects a
    transport). Reads only the base-URL override from ``env``; the credential artifacts are read
    by :func:`load_lst_consumer`.
    """
    consumer = load_lst_consumer(env)
    if consumer is None:  # pragma: no cover — guarded by credentials_present at the call site
        raise RuntimeError("credentials_present was true but no LstConsumer could be loaded")
    base_url = env.get(ENV_BASE_URL, "").strip() or _DEFAULT_BASE_URL
    post = make_lst_http_post(base_url)
    transport = build_signed_cp_rest_transport(consumer, base_url=base_url, post=post)
    session = CpRestSession(transport)
    session.wait_until_established(
        max_polls=_ESTABLISH_MAX_POLLS, poll_seconds=_ESTABLISH_POLL_SECONDS
    )
    return transport


def _load_config() -> PlatformConfig:
    """Load the platform config from the repo ``configs/`` (live path only)."""
    from pathlib import Path

    from algotrading.core.config.loader import load_platform_config

    # This file: packages/infra-ibkr/src/algotrading/infra_ibkr/live_capture.py
    # parents[4] == packages/infra-ibkr ; the repo root holds configs/ two more up.
    repo_root = Path(__file__).resolve().parents[6]
    return load_platform_config(repo_root / "configs")
