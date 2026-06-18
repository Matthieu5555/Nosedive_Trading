"""IBKR Client-Portal session control for the Operations page.

Two read/act endpoints over the same machinery `scripts/ibkr_login.py --status` uses, but never
the selenium browser login: a web request must stay cheap and non-interactive. The browser login
(and any SMS challenge) is the CLI script's job; this router only reports the honest session state
and, when the SSO layer is already authenticated, opens the brokerage session (ssodh/init).

"configured" is a fact, not a flag: it means a gateway actually answers on the base URL. We probe
the base URL regardless of the `IBKR_CP_GATEWAY` env var, so a live, authenticated gateway is
reported as such even when the var is unset. Only when NO gateway is reachable (a connection-level
error, not a 401) do we fall back to the not-configured payload. Every transport error degrades to a
clear status string, never a 500, the project's honest-status ethos.
"""

from __future__ import annotations

import contextlib
import os
import subprocess

from algotrading.core.paths import repo_root
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession
from algotrading.infra_ibkr.connectivity.cp_rest_transport import (
    CpRestTransport,
    CpRestTransportError,
)
from algotrading.infra_ibkr.session_factory import (
    _GATEWAY_DEFAULT_BASE_URL,
    ENV_GATEWAY_URL,
)
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/ibkr", tags=["ibkr"])

# The one-line operator instruction for the not-authenticated path. The leading `!` is the shell
# hint the rest of the codebase uses for "run this from a shell"; the script is idempotent.
_LOGIN_HINT = "! scripts/ibkr_login.py"

# The canonical, idempotent "make the gateway ready for data" entrypoint. The web Log-in button
# runs THIS, the same machinery the shell hint points at: status check, headless login only if
# needed, ssodh/init, stale-session retry, verification. We shell out (rather than import) because
# the script pulls Selenium in ephemerally via `uv run --with selenium` and runs a real browser; we
# do NOT want that driving inside the BFF event loop, and `uv run` gives us the dependency injection
# and process isolation for free. AUTH ONLY: no 2FA code provider is passed, so if a challenge fires
# the script fails fast (it never blocks) and we report the unauthenticated status honestly. The
# script only logs in + opens the brokerage session; it places NO trades and transmits NO orders.
_LOGIN_SCRIPT = "scripts/ibkr_login.py"
# Hard ceiling on the login subprocess so a stuck browser / SMS challenge can never hang the BFF.
# The script's own establish poll is ~30s; a clean no-2FA login lands well inside this window.
_LOGIN_TIMEOUT_SECONDS = 120

_NOT_CONFIGURED_DETAIL = (
    "IBKR gateway is not configured. Set IBKR_CP_GATEWAY=1 (plus IBKR_USERID/IBKR_PASSWORD) in the "
    f"repo .env and bring up clientportal.gw, then run {_LOGIN_HINT} from a shell to authenticate."
)


def _base_url() -> str:
    return os.environ.get(ENV_GATEWAY_URL, "").strip() or _GATEWAY_DEFAULT_BASE_URL


def _build_session() -> tuple[CpRestTransport, CpRestSession]:
    # No establish poll, no keepalive thread: we only probe and (on connect) one ssodh/init call.
    transport = CpRestTransport(base_url=_base_url(), verify_tls=False)
    return transport, CpRestSession(transport)


def _account(transport: CpRestTransport) -> str | None:
    try:
        payload = transport.get("/iserver/accounts")
    except CpRestTransportError:
        return None
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    return accounts[0] if accounts else None


def _status_payload() -> dict[str, object]:
    """Honest gateway/session state, never raising. Mirrors `ibkr_login.py` classification.

    Always probes the base URL: "configured" means a gateway answered, not that an env var is set.
    """
    transport, session = _build_session()
    try:
        try:
            authenticated = session.authenticated()
        except CpRestTransportError as exc:
            # 401 => gateway up but no SSO session; no status code => connect error (gateway down).
            if getattr(exc, "status_code", None) == 401:
                return {
                    "configured": True,
                    "authenticated": False,
                    "established": False,
                    "competing": False,
                    "account": None,
                    "detail": (
                        "Gateway is up but there is no SSO session. Run "
                        f"{_LOGIN_HINT} from a shell to log in."
                    ),
                }
            # No gateway answers on the base URL: report not-configured (the honest fact here), with
            # the operator instruction to bring one up.
            return {
                "configured": False,
                "authenticated": False,
                "established": False,
                "competing": False,
                "account": None,
                "detail": _NOT_CONFIGURED_DETAIL,
            }

        if not authenticated:
            return {
                "configured": True,
                "authenticated": False,
                "established": False,
                "competing": False,
                "account": None,
                "detail": (
                    "Gateway is up but not authenticated (no SSO session). Run "
                    f"{_LOGIN_HINT} from a shell to log in."
                ),
            }

        try:
            established = session.established()
        except CpRestTransportError:
            established = False

        account = _account(transport)
        if established:
            detail = "Session ready: authenticated and brokerage session established."
        else:
            detail = (
                "Authenticated, but the brokerage session is not established yet. Click Open "
                "brokerage session, or run "
                f"{_LOGIN_HINT} from a shell."
            )
        return {
            "configured": True,
            "authenticated": True,
            "established": established,
            "competing": False,
            "account": account,
            "detail": detail,
        }
    finally:
        transport.close()


@router.get("/status")
def ibkr_status() -> JSONResponse:
    return JSONResponse(_status_payload())


@router.post("/connect")
def ibkr_connect() -> JSONResponse:
    """Open the brokerage session IF already authenticated at the SSO layer. Never logs in.

    409 on every not-ready path with an honest detail telling the operator to run the CLI script;
    200 with the resulting status object once the brokerage session is established. Probes the base
    URL directly, so a live gateway works regardless of the `IBKR_CP_GATEWAY` env var.
    """
    transport, session = _build_session()
    try:
        try:
            authenticated = session.authenticated()
        except CpRestTransportError as exc:
            # 401 => gateway up but no SSO session; no status code => no gateway reachable at all.
            if getattr(exc, "status_code", None) == 401:
                return JSONResponse(
                    {
                        "error": "ibkr_not_authenticated",
                        "detail": (
                            "Gateway is up but not authenticated (no SSO session). A browser login "
                            f"does not run from the web app, run {_LOGIN_HINT} from a shell to "
                            "authenticate."
                        ),
                        "login_hint": _LOGIN_HINT,
                    },
                    status_code=409,
                )
            return JSONResponse(
                {
                    "error": "ibkr_not_configured",
                    "detail": _NOT_CONFIGURED_DETAIL,
                    "login_hint": _LOGIN_HINT,
                },
                status_code=409,
            )

        if not authenticated:
            return JSONResponse(
                {
                    "error": "ibkr_not_authenticated",
                    "detail": (
                        "Gateway is up but not authenticated at the SSO layer. A browser login "
                        f"does not run from the web app, run {_LOGIN_HINT} from a shell to log in."
                    ),
                    "login_hint": _LOGIN_HINT,
                },
                status_code=409,
            )

        # Authenticated at SSO: open the brokerage session (ssodh/init). One call, no polling loop.
        # A transport hiccup is swallowed here; the status probe below reports the real resulting
        # state, so connect never 500s on an ssodh/init blip.
        with contextlib.suppress(CpRestTransportError):
            session.open_brokerage_session()
    finally:
        transport.close()

    return JSONResponse(_status_payload())


def _run_login_script() -> subprocess.CompletedProcess[str]:
    """Run the idempotent CLI login (`scripts/ibkr_login.py`) once, non-interactively, with a hard
    timeout. Selenium is injected ephemerally (`uv run --with selenium`); no 2FA code provider is
    passed, so a challenge fails fast instead of blocking. AUTH ONLY: the script logs in and opens
    the brokerage session, never trades. Lets `subprocess.TimeoutExpired` propagate to the caller.
    """
    root = repo_root()
    # `uv run` resolves the project's interpreter and injects Selenium ephemerally for this one
    # process; the script itself is a no-op (idempotent) when the session is already live.
    cmd = ["uv", "run", "--with", "selenium", "python", _LOGIN_SCRIPT]
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, repo-local script
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=_LOGIN_TIMEOUT_SECONDS,
        check=False,
    )


@router.post("/login")
def ibkr_login() -> JSONResponse:
    """Run the idempotent IBKR login from the web app, then report the resulting session status.

    This is the button behind the operator's complaint that there was no frontend way to run
    `scripts/ibkr_login.py`. It shells out to that exact script (status check + headless login only
    if needed + ssodh/init + verification), with a hard timeout so it can never hang the server.

    AUTH ONLY: no order is ever placed or transmitted. If a 2FA/SMS challenge fires (risk-based,
    often it does not), the script cannot complete a code-less login and exits non-zero; we then
    return the honest, still-unauthenticated status with a clear message so the operator knows to
    run the CLI with a code, rather than the UI hanging. On success we return the fresh IbkrStatus
    (the same probe `/status` uses), so the panel can update immediately.
    """
    try:
        result = _run_login_script()
    except subprocess.TimeoutExpired:
        # The login took too long (stuck browser, or an SMS challenge nobody can answer headless).
        # Report the real current status with a clear message, never a hung request or a 500.
        status = _status_payload()
        status["detail"] = (
            "Login timed out from the web app after "
            f"{_LOGIN_TIMEOUT_SECONDS}s (a 2FA/SMS challenge may have fired, which cannot complete "
            f"headless). Run {_LOGIN_HINT} from a shell to finish logging in."
        )
        return JSONResponse(
            {"error": "ibkr_login_timeout", "login_hint": _LOGIN_HINT, **status},
            status_code=504,
        )
    except (OSError, ValueError) as exc:
        # Could not even launch the login (e.g. uv missing). Honest, never a silent failure.
        status = _status_payload()
        status["detail"] = (
            f"Could not start the IBKR login from the web app: {exc}. Run {_LOGIN_HINT} from a "
            "shell instead."
        )
        return JSONResponse(
            {"error": "ibkr_login_failed", "login_hint": _LOGIN_HINT, **status},
            status_code=502,
        )

    status = _status_payload()
    if result.returncode == 0 and status["authenticated"]:
        return JSONResponse(status)

    # The script ran but did not land an authenticated session. Surface the script's own tail so the
    # operator sees why (e.g. a 2FA challenge, rejected credentials, gateway down), and fall back to
    # the honest status payload, never a 500.
    tail = (result.stderr or result.stdout or "").strip().splitlines()
    reason = tail[-1] if tail else "login did not complete; no output from the script."
    status["detail"] = (
        f"IBKR login did not complete from the web app: {reason} Run {_LOGIN_HINT} from a shell "
        "(you can pass a 2FA code there if a challenge fired)."
    )
    return JSONResponse(
        {"error": "ibkr_login_incomplete", "login_hint": _LOGIN_HINT, **status},
        status_code=409,
    )
