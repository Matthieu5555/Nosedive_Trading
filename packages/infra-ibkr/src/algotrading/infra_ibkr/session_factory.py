"""Build a credentialed, established CP REST session from ``.env`` (ADR 0024/0031).

Both IBKR paths that touch a live gateway — the EOD live basket source (:mod:`live_capture`) and
the historical-OHLC backfill (:mod:`history_backfill`) — need the same thing: turn the IBKR CP
OAuth artifacts in the environment into an authenticated, OAuth-signed, established CP REST
session. This module is that single place, so the two paths cannot drift on auth, base URL, or the
established-session wait.

:func:`build_credentialed_session` returns ``(transport, session)`` when every required artifact is
present (:func:`credentials_present`) and ``None`` otherwise — the caller then runs its own no-op
path (an empty no-capture day for live, a skipped backfill for history). The only socket is the real
``httpx`` ``post`` inside the signed transport; it is injectable, so the gate exercises the callers
against fakes with no network and no secrets, and this builder is reached only on a real fire.
"""

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

_LOGGER = structlog.get_logger("ibkr.session_factory")

# The hosted CP Web API base the OAuth path targets (api.ibkr.com, not localhost:5000). A single
# internal default; the operator overrides it via IBKR_CP_BASE_URL when pointing at a test host.
_DEFAULT_BASE_URL = "https://api.ibkr.com/v1/api"
ENV_BASE_URL = "IBKR_CP_BASE_URL"

# The established-session wait budget for the live brokerage session (ssodh/init -> established).
_ESTABLISH_MAX_POLLS = 30
_ESTABLISH_POLL_SECONDS = 1.0


def build_credentialed_session(
    env: Mapping[str, str] | None = None, *, establish: bool = True
) -> tuple[Any, CpRestSession] | None:
    """Acquire the LST, build the signed transport, open the brokerage session; or ``None``.

    Returns ``(transport, session)`` when the environment carries every required IBKR CP OAuth
    artifact, else ``None`` (logged) so the caller falls back to its no-op path. When ``establish``
    is true the brokerage session is opened and waited on (``ssodh/init`` -> established) before the
    pair is returned, so the caller can fetch immediately; pass ``establish=False`` to defer that.
    Reads only the base-URL override from ``env``; the credential artifacts are read by
    :func:`load_lst_consumer`. Reached only on a real fire — every test injects a transport.
    """
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
