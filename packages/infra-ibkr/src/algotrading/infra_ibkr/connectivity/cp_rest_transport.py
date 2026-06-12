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

# The transport seam protocols are defined once in infra (audit M40); re-exported here so
# every existing ``from .cp_rest_transport import SupportsRestGet`` site keeps working.
from algotrading.infra.collectors.transport_seam import (
    SupportsRest as SupportsRest,
)
from algotrading.infra.collectors.transport_seam import (
    SupportsRestGet as SupportsRestGet,
)
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

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


def _is_retryable_status_error(exc: BaseException) -> bool:
    """Only a 429/503 response re-enters the retry loop; everything else fails fast."""
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code in _RETRYABLE_STATUS
    )

# An OAuth signer: given (method, full_url, query_params) it returns the request headers to
# add (the ``Authorization: OAuth …`` header). Injected so the transport stays unaware of the
# OAuth crypto and tests drive a fake signer (ADR 0031). ``None`` is the unsigned local-Gateway
# path — the cookie-session transport from ADR 0024 — left exactly as it was.
OAuthSigner = Callable[[str, str, Mapping[str, object] | None], dict[str, str]]


class CpRestTransportError(Exception):
    """A Client Portal REST/WS call failed (transport-level — connection, timeout, non-2xx).

    ``status_code`` carries the HTTP status when the failure had one (``None`` for a
    connect error or timeout), so callers branch on it directly instead of reaching into
    ``__cause__`` for the wrapped httpx error (audit M20).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        # The retry engine is tenacity (audit M20 — one library instead of four hand loops);
        # the cadence is unchanged: max_retries retries after the initial attempt, waiting
        # the server's Retry-After when sane, else backoff_base * 2**retry_index. Only a
        # retryable status (429/503) re-enters; every other failure propagates immediately.
        # ``sleep`` stays injected, so deterministic tests never really wait.
        retrying = Retrying(
            retry=retry_if_exception(_is_retryable_status_error),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._retry_wait,
            sleep=self._sleep,
            reraise=True,
        )
        try:
            response = retrying(self._send_once, method, url, _query, kwargs)
        except httpx.HTTPStatusError as exc:
            raise CpRestTransportError(
                f"{method} {path} failed: {exc}", status_code=exc.response.status_code
            ) from exc
        except httpx.HTTPError as exc:
            raise CpRestTransportError(f"{method} {path} failed: {exc}") from exc
        if not response.content:
            return None
        return response.json()

    def _send_once(
        self,
        method: str,
        url: str,
        query: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> httpx.Response:
        """One signed request attempt. Signing happens here, inside the retry body, because an
        OAuth signer (ADR 0031) needs a fresh nonce/timestamp on every retry."""
        call_kwargs = dict(kwargs)
        if self._oauth_signer is not None:
            headers = self._oauth_signer(method, url, query)
            existing = call_kwargs.get("headers") or {}
            call_kwargs["headers"] = {**existing, **headers}
        response = self._client.request(method, url, **call_kwargs)
        response.raise_for_status()
        return response

    def _retry_wait(self, retry_state: RetryCallState) -> float:
        """Seconds to wait before retrying: the server's ``Retry-After`` if sane, else backoff."""
        outcome = retry_state.outcome
        exception = outcome.exception() if outcome is not None else None
        if not isinstance(exception, httpx.HTTPStatusError):  # pragma: no cover — predicate gates
            return self._backoff_base_s
        # attempt_number is 1-based and names the attempt that just failed, so the first
        # retry waits backoff_base * 2**0 — the exact cadence of the old hand-rolled loop.
        return self._retry_delay(exception.response, retry_state.attempt_number - 1)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """The server's ``Retry-After`` if sane, else ``backoff_base * 2**attempt`` (0-based)."""
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
