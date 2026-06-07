"""IBKR connectivity — two ingestion paths (ADR 0023/0024/0025).

- **Client Portal REST/WS** (ADR 0024, preferred): :class:`CpRestTransport` +
  :class:`CpRestSession` (the ``/tickle`` keepalive). The course-required REST path.
- **Nautilus TWS** (ADR 0025, manual-flip fallback): :func:`build_data_client_config`,
  import-guarded on the ``ibkr`` extra.

:func:`select_ibkr_transport` picks one by config. The hand-rolled ``ib_async`` ``IbkrTransport``
is **superseded** — retained as dead reference, reached only by direct import, not surfaced here.
"""

from .cp_rest_credentials import (
    credentials_present,
    load_lst_consumer,
    make_lst_http_post,
)
from .cp_rest_lst import (
    DiffieHellmanParams,
    LstConsumer,
    acquire_live_session_token,
    build_signed_cp_rest_transport,
    derive_live_session_token,
    validate_live_session_token,
)
from .cp_rest_oauth import (
    CpOAuthError,
    OAuthCredentials,
    authorization_header,
    make_oauth_signer,
    sign_hmac_sha256,
    sign_request,
    signature_base_string,
)
from .cp_rest_session import CpRestSession, SessionNotEstablishedError
from .cp_rest_transport import CpRestTransport, CpRestTransportError, OAuthSigner
from .ibkr_transport_choice import DEFAULT_IBKR_TRANSPORT, IbkrTransport, select_ibkr_transport
from .nautilus_ibkr import IbkrExtraNotInstalled, build_data_client_config

__all__ = [
    # Nautilus-TWS path (ADR 0025)
    "IbkrExtraNotInstalled",
    "build_data_client_config",
    # Client Portal REST path (ADR 0024)
    "CpRestTransport",
    "CpRestTransportError",
    "OAuthSigner",
    "CpRestSession",
    "SessionNotEstablishedError",
    # OAuth 1.0a signing (ADR 0031)
    "CpOAuthError",
    "OAuthCredentials",
    "authorization_header",
    "make_oauth_signer",
    "sign_request",
    "signature_base_string",
    "sign_hmac_sha256",
    # OAuth 1.0a Live Session Token acquisition (ADR 0031 §2)
    "DiffieHellmanParams",
    "LstConsumer",
    "acquire_live_session_token",
    "derive_live_session_token",
    "validate_live_session_token",
    "build_signed_cp_rest_transport",
    # Credential/env loader (ADR 0031 — the .env → LstConsumer seam)
    "credentials_present",
    "load_lst_consumer",
    "make_lst_http_post",
    # Path selector (ADR 0024 §2)
    "IbkrTransport",
    "DEFAULT_IBKR_TRANSPORT",
    "select_ibkr_transport",
]
