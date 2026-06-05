"""OAuth router: the Saxo authorization-code web flow (CSRF half wired here).

``start`` mints a single-use CSRF state token and returns the authorize URL the browser
should visit. ``callback`` validates the returned state (rejecting CSRF / replay) and then
reports that the Saxo broker backend is not configured — the token exchange and
live-session injection need ``packages/infra-saxo``. Everything that can be verified
without a live broker (state lifecycle, URL construction, CSRF rejection) is real here;
the rest fails closed with a typed payload.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..oauth_state import consume_state, generate_state

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# Saxo OAuth endpoints/config come from the environment so no secret is hard-coded or
# shipped to the browser. Absent config still lets the CSRF half be exercised.
_AUTHORIZE_URL = os.getenv("SAXO_AUTHORIZE_URL", "https://sim.logonvalidation.net/authorize")
_CLIENT_ID = os.getenv("SAXO_CLIENT_ID", "")
_REDIRECT_URI = os.getenv("SAXO_REDIRECT_URI", "http://localhost:8000/api/oauth/saxo/callback")


@router.post("/saxo/start")
def saxo_start() -> JSONResponse:
    """Mint a CSRF state token and return the Saxo authorize URL to visit."""
    state = generate_state()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "state": state,
        }
    )
    return JSONResponse({"authorize_url": f"{_AUTHORIZE_URL}?{query}", "state": state})


@router.get("/saxo/callback")
def saxo_callback(
    request: Request, code: str | None = None, state: str | None = None
) -> JSONResponse:
    """Validate the returned CSRF state, then report the backend as not configured."""
    if not state or not consume_state(state):
        return JSONResponse({"error": "invalid_state"}, status_code=400)
    if not code:
        return JSONResponse({"error": "missing_code"}, status_code=400)
    # State is valid; the live exchange needs the Saxo backend (packages/infra-saxo).
    return JSONResponse(
        {
            "error": "saxo_backend_not_configured",
            "note": "CSRF state validated; token exchange requires packages/infra-saxo.",
        },
        status_code=501,
    )


@router.get("/saxo/status")
def saxo_status() -> JSONResponse:
    """Report the Saxo session status (not configured until packages/infra-saxo lands)."""
    return JSONResponse({"provider": "SAXO", "configured": False, "authenticated": False})


@router.delete("/saxo")
def saxo_revoke() -> JSONResponse:
    """Revoke the Saxo session (no-op until the backend is configured)."""
    return JSONResponse({"provider": "SAXO", "revoked": False, "configured": False})
