"""OAuth router: the Saxo authorization-code web flow (CSRF half wired here).

``start`` mints a single-use CSRF state token and returns the authorize URL the browser
should visit. ``callback`` validates the returned state (rejecting CSRF / replay) and then
reports that the Saxo broker backend is not configured — the token exchange and
live-session injection need ``packages/infra-saxo``. Everything that can be verified
without a live broker (state lifecycle, URL construction, CSRF rejection) is real here;
the rest fails closed with a typed payload.

The CSRF store and the Saxo endpoint settings are app-lifetime state: ``create_app``
hangs an :class:`~algotrading.frontend.oauth_state.OAuthStateStore` on
``app.state.oauth_states`` and a :class:`SaxoOAuthSettings` (read from the environment at
app construction, not at import) on ``app.state.saxo_oauth``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..oauth_state import OAuthStateStore

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# Saxo OAuth endpoints/config come from the environment so no secret is hard-coded or
# shipped to the browser. Absent config still lets the CSRF half be exercised.
_DEFAULT_AUTHORIZE_URL = "https://sim.logonvalidation.net/authorize"
_DEFAULT_REDIRECT_URI = "http://localhost:8000/api/oauth/saxo/callback"


@dataclass(frozen=True, slots=True)
class SaxoOAuthSettings:
    """Saxo OAuth endpoint configuration, read from the environment per application."""

    authorize_url: str
    client_id: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> SaxoOAuthSettings:
        """Read the ``SAXO_*`` env vars (called by ``create_app``, never at import)."""
        return cls(
            authorize_url=os.getenv("SAXO_AUTHORIZE_URL", _DEFAULT_AUTHORIZE_URL),
            client_id=os.getenv("SAXO_CLIENT_ID", ""),
            redirect_uri=os.getenv("SAXO_REDIRECT_URI", _DEFAULT_REDIRECT_URI),
        )


def _oauth_settings(request: Request) -> SaxoOAuthSettings:
    settings: SaxoOAuthSettings = request.app.state.saxo_oauth
    return settings


def _state_store(request: Request) -> OAuthStateStore:
    store: OAuthStateStore = request.app.state.oauth_states
    return store


SettingsDep = Annotated[SaxoOAuthSettings, Depends(_oauth_settings)]
StateStoreDep = Annotated[OAuthStateStore, Depends(_state_store)]


@router.post("/saxo/start")
def saxo_start(settings: SettingsDep, states: StateStoreDep) -> JSONResponse:
    """Mint a CSRF state token and return the Saxo authorize URL to visit."""
    state = states.generate()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "state": state,
        }
    )
    return JSONResponse({"authorize_url": f"{settings.authorize_url}?{query}", "state": state})


@router.get("/saxo/callback")
def saxo_callback(
    states: StateStoreDep, code: str | None = None, state: str | None = None
) -> JSONResponse:
    """Validate the returned CSRF state, then report the backend as not configured."""
    if not state or not states.consume(state):
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
