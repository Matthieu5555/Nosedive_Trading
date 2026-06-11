"""Build an established CP REST session — hosted-OAuth or local-Gateway — from ``.env``
(ADR 0024/0031).

Both IBKR paths that touch a live gateway — the EOD live basket source (:mod:`live_capture`) and
the historical-OHLC backfill (:mod:`history_backfill`) — need the same thing: an authenticated,
established CP REST session. This module is that single place, so the two paths cannot drift on
auth, base URL, or the established-session wait. Two ways to authenticate it:

* :func:`build_credentialed_session` — the **unattended hosted path** (``api.ibkr.com`` + OAuth
  1.0a): turn the ``IBKR_CP_*`` registration artifacts into an LST-signed transport. Returns
  ``(transport, session)`` when every required artifact is present (:func:`credentials_present`)
  and ``None`` otherwise — the caller then runs its own no-op path (an empty no-capture day for
  live, a skipped backfill for history).
* :func:`build_gateway_session` — the **local Client Portal Gateway path** (``localhost:5000`` +
  browser-login cookie, ADR 0024). No OAuth registration: the operator runs IBKR's ``clientportal.
  gw`` and logs in once in a browser; the Gateway holds the session cookie, so the transport
  carries no auth header (``oauth_signer=None``). This is the manual-flip path that does **not**
  depend on the (flaky) Self-Service OAuth portal enrolment.

The only socket is the real ``httpx`` request inside the transport; it is injectable, so the gate
exercises the callers against fakes with no network and no secrets, and these builders are reached
only on a real fire.
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
from .connectivity.cp_rest_transport import CpRestTransport

_LOGGER = structlog.get_logger("ibkr.session_factory")

# The hosted CP Web API base the OAuth path targets (api.ibkr.com, not localhost:5000). A single
# internal default; the operator overrides it via IBKR_CP_BASE_URL when pointing at a test host.
_DEFAULT_BASE_URL = "https://api.ibkr.com/v1/api"
ENV_BASE_URL = "IBKR_CP_BASE_URL"

# The local CP Gateway path (cookie session, no OAuth). Opt-in flag + its base URL override; the
# default is the Gateway's standard localhost listener (self-signed TLS, so verify is off).
ENV_GATEWAY = "IBKR_CP_GATEWAY"
ENV_GATEWAY_URL = "IBKR_CP_GATEWAY_URL"
_GATEWAY_DEFAULT_BASE_URL = "https://localhost:5000/v1/api"
_GATEWAY_TRUTHY = frozenset({"1", "true", "yes", "on"})

# The established-session wait budget for the live brokerage session (ssodh/init -> established).
_ESTABLISH_MAX_POLLS = 30
_ESTABLISH_POLL_SECONDS = 1.0


def gateway_requested(env: Mapping[str, str] | None = None) -> bool:
    """True when the operator opted into the local CP Gateway path (``IBKR_CP_GATEWAY`` truthy)."""
    resolved = os.environ if env is None else env
    return resolved.get(ENV_GATEWAY, "").strip().lower() in _GATEWAY_TRUTHY


def build_gateway_session(
    env: Mapping[str, str] | None = None,
    *,
    establish: bool = True,
    transport: Any | None = None,
) -> tuple[Any, CpRestSession]:
    """Build a cookie-session CP REST transport over the local Gateway and establish it.

    The local-Gateway counterpart of :func:`build_credentialed_session`: no OAuth registration, no
    LST exchange. The running ``clientportal.gw`` (browser-logged-in) holds the session cookie, so
    the transport carries no ``Authorization`` header (``oauth_signer=None``, the ADR 0024 path);
    this is what lets the EOD capture run without the Self-Service OAuth portal. The base URL is
    ``IBKR_CP_GATEWAY_URL`` or the Gateway default (``https://localhost:5000/v1/api``); TLS verify
    is off because the Gateway serves a self-signed localhost cert.

    When ``establish`` is true the brokerage session is opened and waited on (``ssodh/init`` ->
    established) before returning, so the caller can fetch immediately; a Gateway that is down or
    not logged in never establishes and raises a labeled :class:`SessionNotEstablishedError` (a
    loud failure, not a silent no-capture — the operator explicitly asked for the Gateway path).
    ``transport`` is injectable so the gate drives establishment against a fake with no socket.
    """
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
