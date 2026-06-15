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

# The CP Gateway enforces a HARD documented ceiling of 10 requests/second per authenticated
# username (``/iserver/marketdata/snapshot`` is the same 10 req/s). Cross it and the IP lands in a
# documented ~10-minute penalty box; a repeat-violator IP can be PERMANENTLY banned. The old
# transport was reactive — fire as fast as the caller allowed, eat the 429, back off 0.5 * 2**n —
# which in a dense option-chain capture logged 270 backoffs in 14 minutes: every 429 a recorded
# violation, every sub-second retry an escalation toward a ban, and most wall-clock spent
# overshooting backoff rather than working.
#
# So the transport is now PROACTIVE. A client-side token bucket (see ``_TokenBucket``) paces every
# real HTTP send to just UNDER the ceiling — default ~8.5 req/s with a little jitter — so we almost
# never emit the 11th request in a second and thus almost never see a 429. Steady ~8.5 req/s beats
# thrash-and-stall, so this is both safer AND faster. A 429/503 should now be vanishingly rare; if
# one slips through it is treated as the penalty box it announces: honour ``Retry-After`` if the
# server sent one, else wait the full documented box (``penalty_box_s``, default 600s) — NEVER the
# old sub-second cadence, which is exactly what gets an IP banned. Every other status fails fast.
_RETRYABLE_STATUS = frozenset({429, 503})
# With the token bucket pacing us under the ceiling, a 429/503 is a rare anomaly; one retry after a
# full penalty box is enough — we are not trying to grind through a wall of violations.
_DEFAULT_MAX_RETRIES = 2
# Proactive pace, pinned just under the documented 10 req/s ceiling.
_DEFAULT_MAX_REQUESTS_PER_SECOND = 8.5
# The documented penalty box is ~10 minutes; if we ever 429 without a Retry-After, wait it out.
_DEFAULT_PENALTY_BOX_S = 600.0
# Default per-request jitter ceiling (seconds) ADDED to the bucket's computed wait, so a fleet of
# clients does not phase-lock onto identical send instants. Deterministic in tests: the jitter
# source is injectable and defaults below to a constant-yielding generator.
_DEFAULT_JITTER_S = 0.05
# Repurposed legacy knob: retained for backward-compatible construction (call sites and the LST
# factory still pass it). It now seeds the safety-net floor only — see ``_retry_delay``.
_DEFAULT_BACKOFF_BASE_S = 0.5


def _is_retryable_status_error(exc: BaseException) -> bool:
    """Only a 429/503 response re-enters the retry loop; everything else fails fast."""
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code in _RETRYABLE_STATUS
    )


# Yields the per-request jitter (seconds) to ADD to the bucket's computed wait. Injectable so
# tests stay deterministic; the production default returns a small constant.
JitterSource = Callable[[], float]


class _TokenBucket:
    """A monotonic-clock token bucket gating sends to ``rate`` requests/second.

    Capacity is one second's worth of tokens, so a short idle period lets a small burst through
    but the *sustained* rate can never exceed ``rate``. ``acquire`` returns the seconds the caller
    must sleep before its token is available (0.0 when one is ready now); it deducts the token
    immediately, so callers that honour the returned wait are paced even back-to-back. The clock
    and the jitter source are injected, so tests advance time by hand and never really wait.
    """

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Callable[[], float],
        jitter: JitterSource,
    ) -> None:
        self._rate = rate
        self._capacity = rate  # one second of tokens
        self._monotonic = monotonic
        self._jitter = jitter
        self._tokens = rate  # start full: the first request never waits
        self._updated = monotonic()

    def acquire(self) -> float:
        """Seconds to wait before the next send; deducts one token (may drive the count < 0)."""
        now = self._monotonic()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        wait = 0.0 if self._tokens >= 1.0 else (1.0 - self._tokens) / self._rate
        self._tokens -= 1.0
        return wait + (self._jitter() if wait > 0.0 else 0.0)

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
        max_requests_per_second: float | None = _DEFAULT_MAX_REQUESTS_PER_SECOND,
        penalty_box_s: float = _DEFAULT_PENALTY_BOX_S,
        jitter: JitterSource | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        _client: httpx.Client | None = None,
    ) -> None:
        """Construct the transport.

        ``max_requests_per_second`` pins the proactive token bucket just under the Gateway's
        documented 10 req/s ceiling (default ~8.5). Pass ``None`` or ``0`` to DISABLE pacing — the
        unsigned fast unit tests that inject a fake client do this so they never sleep. ``jitter``
        is an injectable, deterministic-in-test source of the extra seconds added to a non-zero
        bucket wait so a fleet does not phase-lock; it defaults to a small constant.
        ``penalty_box_s`` is how long a 429/503 *without* a ``Retry-After`` waits — the box, the
        safety net behind the bucket, never the old sub-second cadence. ``backoff_base_s`` is the
        retained legacy knob: it now floors that safety-net wait only.
        """
        self._base_url = base_url.rstrip("/")
        self._oauth_signer = oauth_signer
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._penalty_box_s = penalty_box_s
        self._sleep = sleep
        jitter_source: JitterSource = (
            jitter if jitter is not None else (lambda: _DEFAULT_JITTER_S)
        )
        self._bucket: _TokenBucket | None = (
            _TokenBucket(
                max_requests_per_second, monotonic=monotonic, jitter=jitter_source
            )
            if max_requests_per_second
            else None
        )
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
        OAuth signer (ADR 0031) needs a fresh nonce/timestamp on every retry.

        The token bucket gates EVERY real send — including tenacity's retries, since they route
        back through here — so the proactive pace holds across the whole call, not just the first
        attempt. ``_sleep`` is the injected sleep, so deterministic tests advance time instead of
        waiting."""
        if self._bucket is not None:
            wait = self._bucket.acquire()
            if wait > 0.0:
                self._sleep(wait)
        call_kwargs = dict(kwargs)
        if self._oauth_signer is not None:
            headers = self._oauth_signer(method, url, query)
            existing = call_kwargs.get("headers") or {}
            call_kwargs["headers"] = {**existing, **headers}
        response = self._client.request(method, url, **call_kwargs)
        response.raise_for_status()
        return response

    def _retry_wait(self, retry_state: RetryCallState) -> float:
        """Seconds to wait before retrying a 429/503: ``Retry-After`` if sane, else the box."""
        outcome = retry_state.outcome
        exception = outcome.exception() if outcome is not None else None
        if not isinstance(exception, httpx.HTTPStatusError):  # pragma: no cover — predicate gates
            return self._penalty_box_s
        return self._retry_delay(exception.response)

    def _retry_delay(self, response: httpx.Response) -> float:
        """Penalty-box wait for a 429/503.

        With the proactive token bucket pacing us under the ceiling a 429/503 should be vanishingly
        rare, so it is treated as the penalty box it announces, NOT the old ``0.5 * 2**n`` cadence
        (sub-second retries during a box are exactly what escalate an IP to a permanent ban). Honour
        the server's ``Retry-After`` when it is a sane number of seconds; otherwise wait the full
        documented box (``penalty_box_s``), floored by the legacy ``backoff_base_s`` knob.
        """
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass  # an HTTP-date Retry-After — fall back to the penalty box
        return max(self._backoff_base_s, self._penalty_box_s)

    def streaming_url(self) -> str:
        """The WebSocket endpoint for live market data, derived from the REST base URL."""
        scheme_swapped = self._base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        return f"{scheme_swapped}/ws"

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
