from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import structlog

from .connectivity.cp_rest_credentials import (
    credentials_present,
    load_lst_consumer,
    make_lst_http_post,
)
from .connectivity.cp_rest_lst import build_signed_cp_rest_transport
from .connectivity.cp_rest_session import CpRestSession
from .connectivity.cp_rest_transport import CpRestTransport

_LOGGER = structlog.get_logger("ibkr.session_factory")

_DEFAULT_BASE_URL = "https://api.ibkr.com/v1/api"
ENV_BASE_URL = "IBKR_CP_BASE_URL"

ENV_GATEWAY = "IBKR_CP_GATEWAY"
ENV_GATEWAY_URL = "IBKR_CP_GATEWAY_URL"
_GATEWAY_DEFAULT_BASE_URL = "https://localhost:5000/v1/api"
_GATEWAY_TRUTHY = frozenset({"1", "true", "yes", "on"})

_ESTABLISH_MAX_POLLS = 30
_ESTABLISH_POLL_SECONDS = 1.0


def gateway_requested(env: Mapping[str, str] | None = None) -> bool:
    resolved = os.environ if env is None else env
    return resolved.get(ENV_GATEWAY, "").strip().lower() in _GATEWAY_TRUTHY


def build_gateway_session(
    env: Mapping[str, str] | None = None,
    *,
    establish: bool = True,
    transport: Any | None = None,
) -> tuple[Any, CpRestSession]:
    resolved = os.environ if env is None else env
    base_url = resolved.get(ENV_GATEWAY_URL, "").strip() or _GATEWAY_DEFAULT_BASE_URL
    if transport is None:
        transport = CpRestTransport(base_url=base_url, verify_tls=False)
    session = CpRestSession(transport)
    _LOGGER.info("ibkr.session_factory.gateway", base_url=base_url, reason="local CP Gateway path")
    if establish:
        session.wait_until_established(
            max_polls=_ESTABLISH_MAX_POLLS, poll_seconds=_ESTABLISH_POLL_SECONDS
        )
    return transport, session


def build_credentialed_session(
    env: Mapping[str, str] | None = None, *, establish: bool = True
) -> tuple[Any, CpRestSession] | None:
    resolved = os.environ if env is None else env
    if not credentials_present(resolved):
        _LOGGER.info(
            "ibkr.session_factory.not_credentialed",
            reason="no IBKR CP OAuth artifacts in environment",
        )
        return None
    consumer = load_lst_consumer(resolved)
    if consumer is None:  # pragma: no cover — guarded by credentials_present just above
        raise RuntimeError("credentials_present was true but no LstConsumer could be loaded")
    base_url = resolved.get(ENV_BASE_URL, "").strip() or _DEFAULT_BASE_URL
    post = make_lst_http_post(base_url)
    transport = build_signed_cp_rest_transport(consumer, base_url=base_url, post=post)
    session = CpRestSession(transport)
    if establish:
        session.wait_until_established(
            max_polls=_ESTABLISH_MAX_POLLS, poll_seconds=_ESTABLISH_POLL_SECONDS
        )
    return transport, session
