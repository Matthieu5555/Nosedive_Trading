"""IBKR Client Portal REST + WebSocket transport (ADR 0024).

A thin, stateless transport over the local Client Portal Gateway. The Gateway (a browser-login
process on ``https://localhost:5000``) holds the session cookie, so there is no auth header to
carry — we only own keepalive (see :mod:`.cp_rest_session`). ``httpx``/``websockets`` are real
deps; the live socket is never opened in the gate (tests inject a fake client / fake transport).
The CP Gateway serves a self-signed cert on localhost, hence ``verify_tls`` defaults off.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

_DEFAULT_BASE_URL = "https://localhost:5000/v1/api"
_DEFAULT_TIMEOUT_S = 15.0

# The CP Gateway rate-limits a burst of per-contract calls (the option-chain `/secdef/info`
# qualification fires one request per (month, strike, right)) with HTTP 429; a transient 503 is
# the same class. We retry those with exponential backoff (honouring a ``Retry-After`` header when
# present) so a dense chain completes — slower, but it completes — instead of aborting the whole
# close capture. Every other status fails fast.
_RETRYABLE_STATUS = frozenset({429, 503})
_DEFAULT_MAX_RETRIES = 6
_DEFAULT_BACKOFF_BASE_S = 0.5

# An OAuth signer: given (method, full_url, query_params) it returns the request headers to
# add (the ``Authorization: OAuth …`` header). Injected so the transport stays unaware of the
# OAuth crypto and tests drive a fake signer (ADR 0031). ``None`` is the unsigned local-Gateway
# path — the cookie-session transport from ADR 0024 — left exactly as it was.
OAuthSigner = Callable[[str, str, Mapping[str, object] | None], dict[str, str]]


class CpRestTransportError(Exception):
    """A Client Portal REST/WS call failed (transport-level — connection, timeout, non-2xx)."""


class CpRestTransport:
    """REST verbs + the WebSocket URL for the Client Portal Web API.

    ``_client`` is injectable so tests drive a fake without a live Gateway; ``base_url`` points at
    the local Gateway by default.

    ``oauth_signer`` is optional: when supplied, every request adds the OAuth 1.0a
    ``Authorization`` header it returns (the unattended hosted-endpoint path, ADR 0031); left
    ``None`` the transport is the unsigned local-Gateway cookie-session path of ADR 0024,
    unchanged. The signer is handed the method, the full URL, and the query parameters so it can
    fold them into the OAuth signature base string.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_S,
        verify_tls: bool = False,
        oauth_signer: OAuthSigner | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_s: float = _DEFAULT_BACKOFF_BASE_S,
        sleep: Callable[[float], None] = time.sleep,
        _client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._oauth_signer = oauth_signer
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._client = (
            _client if _client is not None else httpx.Client(timeout=timeout, verify=verify_tls)
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``path`` (relative to the base) and return the decoded JSON body."""
        return self._request("GET", path, params=params, _query=params)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        """POST ``body`` to ``path`` and return the decoded JSON body."""
        return self._request("POST", path, json=body)

    def _request(
        self, method: str, path: str, *, _query: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            # Sign per attempt: an OAuth signer (ADR 0031) needs a fresh nonce/timestamp on a retry.
            call_kwargs = dict(kwargs)
            if self._oauth_signer is not None:
                headers = self._oauth_signer(method, url, _query)
                existing = call_kwargs.get("headers") or {}
                call_kwargs["headers"] = {**existing, **headers}
            try:
                response = self._client.request(method, url, **call_kwargs)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    self._sleep(self._retry_delay(exc.response, attempt))
                    attempt += 1
                    continue
                raise CpRestTransportError(f"{method} {path} failed: {exc}") from exc
            except httpx.HTTPError as exc:
                raise CpRestTransportError(f"{method} {path} failed: {exc}") from exc
            if not response.content:
                return None
            return response.json()

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Seconds to wait before retrying: the server's ``Retry-After`` if sane, else backoff."""
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass  # an HTTP-date Retry-After — fall back to our own backoff
        return self._backoff_base_s * (2.0**attempt)

    def streaming_url(self) -> str:
        """The WebSocket endpoint for live market data, derived from the REST base URL."""
        scheme_swapped = self._base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        return f"{scheme_swapped}/ws"

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
