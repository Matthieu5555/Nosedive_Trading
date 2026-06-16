"""IBKR Client Portal REST + WebSocket transport (ADR 0024).

A thin, stateless transport over the local Client Portal Gateway. The Gateway (a browser-login
process on ``https://localhost:5000``) holds the session cookie, so there is no auth header to
carry — we only own keepalive (see :mod:`.cp_rest_session`). ``httpx``/``websockets`` are real
deps; the live socket is never opened in the gate (tests inject a fake client / fake transport).
The CP Gateway serves a self-signed cert on localhost, hence ``verify_tls`` defaults off.
"""

import threading
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
# So the transport is now PROACTIVE. A client-side, LOCK-SERIALISED token bucket (see
# ``_TokenBucket``) paces every real HTTP send to under the ceiling — default ~7 req/s with a small
# burst headroom and a little jitter. The lock matters: the close capture drives the transport from
# a ThreadPoolExecutor, and an unsynchronised bucket let several threads each see a token free and
# fire at once — an aggregate ~8.5 req/s that still burst past 10 in a sub-second window and drew
# 429s. Serialising the token accounting (and capping the post-idle burst) makes the pace actually
# hold across the pool, so a 429 is genuinely rare. If one still slips through it is handled by its
# own signal: honour a sane ``Retry-After`` in full (a declared box), else — far more likely just
# transient burst contention, NOT a 10-minute box — back off a BOUNDED, jittered exponential
# (capped well under a minute) and retry. The old code waited the full 600s documented box on a
# no-header 429, which froze each pooled worker for 10 minutes on the first routine burst — a hang,
# not a safeguard. Sub-second retry storms (the actual ban risk) stay ruled out by the backoff
# floor + the bucket. Every other status fails fast.
_RETRYABLE_STATUS = frozenset({429, 503})
# With the bucket pacing us under the ceiling a 429/503 is a rare anomaly; a couple of bounded
# retries is enough — we are not trying to grind through a wall of violations.
_DEFAULT_MAX_RETRIES = 2
# Proactive pace, with margin under the documented 10 req/s ceiling: at this rate plus the bounded
# post-idle burst (below) the worst-case 1-second window stays under 10, even back-to-back.
_DEFAULT_MAX_REQUESTS_PER_SECOND = 7.0
# Post-idle burst cap (tokens). The bucket refills to at most this many tokens, so after an idle
# gap it releases a bounded burst rather than a full second's worth — chosen with the rate so
# ``burst + rate`` stays under the 10 req/s ceiling in any rolling second.
_DEFAULT_MAX_BURST_TOKENS = 2.0
# A no-``Retry-After`` 429/503 is treated as transient burst contention: bounded exponential
# backoff capped here (NOT the full documented box), so a routine burst costs seconds, never a
# 10-minute per-worker hang. A genuine box still announces itself via ``Retry-After``.
_NO_RETRY_AFTER_BACKOFF_CAP_S = 20.0
# A sane ``Retry-After`` is honoured in full but bounded by this documented ~10-minute box, so a
# pathological header value cannot wedge the walk. This is the ONLY path that can wait minutes, and
# only when the server explicitly asked us to.
_DEFAULT_PENALTY_BOX_S = 600.0
# Default per-request jitter ceiling (seconds) ADDED to the bucket's computed wait, so a fleet of
# clients does not phase-lock onto identical send instants. Deterministic in tests: the jitter
# source is injectable and defaults below to a constant-yielding generator.
_DEFAULT_JITTER_S = 0.05
# Base of the bounded exponential backoff for a no-``Retry-After`` 429/503
# (``base * 2**(attempt-1)``, capped by ``_NO_RETRY_AFTER_BACKOFF_CAP_S``). Retained as a
# construction knob — see ``_retry_delay``.
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

    Capacity is ``burst_tokens`` (a SMALL post-idle burst, not a full second's worth), so an idle
    gap releases at most that burst and ``burst + rate`` stays under the hard ceiling in any rolling
    second; the *sustained* rate can never exceed ``rate``. ``acquire`` returns the seconds the
    caller must sleep before its token is available (0.0 when one is ready now); it deducts the
    token immediately, so callers that honour the returned wait are paced even back-to-back.

    ``acquire`` is LOCK-SERIALISED: the close capture drives this from a thread pool, and without
    the lock concurrent callers each read the same token count and fire together — the burst that
    defeats the whole point and draws 429s. The lock makes the token accounting atomic so the pace
    holds across threads. The clock and the jitter source are injected, so tests advance time by
    hand and never really wait.
    """

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Callable[[], float],
        jitter: JitterSource,
        burst_tokens: float = _DEFAULT_MAX_BURST_TOKENS,
    ) -> None:
        self._rate = rate
        # Cap the post-idle burst (never more than the rate itself for a slow pace).
        self._capacity = min(rate, burst_tokens)
        self._monotonic = monotonic
        self._jitter = jitter
        self._tokens = self._capacity  # start at the burst cap, not a full second
        self._updated = monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Seconds to wait before the next send; deducts one token (may drive the count < 0).

        Serialised under ``self._lock`` so concurrent pool threads cannot each see a free token and
        fire at once — the accounting is atomic, so the sustained pace holds across the pool.
        """
        with self._lock:
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

        ``max_requests_per_second`` pins the proactive, lock-serialised token bucket under the
        Gateway's documented 10 req/s ceiling (default ~7, with a small post-idle burst). Pass
        ``None`` or ``0`` to DISABLE pacing — the unsigned fast unit tests that inject a fake client
        do this so they never sleep. ``jitter`` is an injectable, deterministic-in-test source of
        the extra seconds added both to a non-zero bucket wait and to the backoff (so a fleet does
        not phase-lock); it defaults to a small constant. ``penalty_box_s`` bounds a *server-sent*
        ``Retry-After`` (a declared box) — it is no longer the wait for a no-header 429, which now
        takes the bounded exponential backoff (see :meth:`_retry_delay`) so a routine burst never
        freezes a worker for minutes. ``backoff_base_s`` is the base of that exponential.
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
        self._jitter = jitter_source  # also seeds the no-Retry-After backoff jitter
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
        """Seconds to wait before retrying a 429/503 — see :meth:`_retry_delay`."""
        outcome = retry_state.outcome
        exception = outcome.exception() if outcome is not None else None
        if not isinstance(exception, httpx.HTTPStatusError):  # pragma: no cover — predicate gates
            return self._backoff_base_s
        return self._retry_delay(exception.response, retry_state.attempt_number)

    def _retry_delay(self, response: httpx.Response, attempt_number: int) -> float:
        """How long to wait before retrying a 429/503.

        Two cases, by what the server told us:

        - **A sane ``Retry-After``** is honoured in full — a declared penalty box — but bounded by
          ``penalty_box_s`` so a pathological header value cannot wedge the walk. This is the only
          path that can wait minutes, and only when the server explicitly asked.
        - **No ``Retry-After``** is treated as transient burst contention (far more likely than a
          silent 10-minute box now the lock-serialised bucket paces us under the ceiling): a
          BOUNDED, jittered exponential backoff — ``backoff_base_s * 2**(attempt-1)``, capped at
          ``_NO_RETRY_AFTER_BACKOFF_CAP_S`` — so a routine burst costs seconds, not the 10-minute
          per-worker hang the old full-box wait caused. The cap + ``backoff_base_s`` floor still
          rule out the sub-second retry storm that risks an IP ban.
        """
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return min(self._penalty_box_s, max(0.0, float(header)))
            except ValueError:
                pass  # an HTTP-date Retry-After — fall back to the bounded backoff below
        backoff = self._backoff_base_s * (2.0 ** max(0, attempt_number - 1))
        return min(_NO_RETRY_AFTER_BACKOFF_CAP_S, backoff) + self._jitter()

    def streaming_url(self) -> str:
        """The WebSocket endpoint for live market data, derived from the REST base URL."""
        scheme_swapped = self._base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        return f"{scheme_swapped}/ws"

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
