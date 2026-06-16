import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from algotrading.infra.collectors.transport_seam import (
    SupportsRest as SupportsRest,
)
from algotrading.infra.collectors.transport_seam import (
    SupportsRestGet as SupportsRestGet,
)
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt

_DEFAULT_BASE_URL = "https://localhost:5000/v1/api"
_DEFAULT_TIMEOUT_S = 15.0

_RETRYABLE_STATUS = frozenset({429, 503})
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_MAX_REQUESTS_PER_SECOND = 7.0
_DEFAULT_MAX_BURST_TOKENS = 2.0
_NO_RETRY_AFTER_BACKOFF_CAP_S = 20.0
_DEFAULT_PENALTY_BOX_S = 600.0
_DEFAULT_JITTER_S = 0.05
_DEFAULT_BACKOFF_BASE_S = 0.5


def _is_retryable_status_error(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code in _RETRYABLE_STATUS
    )


JitterSource = Callable[[], float]


class _TokenBucket:

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Callable[[], float],
        jitter: JitterSource,
        burst_tokens: float = _DEFAULT_MAX_BURST_TOKENS,
    ) -> None:
        self._rate = rate
        self._capacity = min(rate, burst_tokens)
        self._monotonic = monotonic
        self._jitter = jitter
        self._tokens = self._capacity
        self._updated = monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        with self._lock:
            now = self._monotonic()
            elapsed = now - self._updated
            self._updated = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            wait = 0.0 if self._tokens >= 1.0 else (1.0 - self._tokens) / self._rate
            self._tokens -= 1.0
            return wait + (self._jitter() if wait > 0.0 else 0.0)

OAuthSigner = Callable[[str, str, Mapping[str, object] | None], dict[str, str]]


class CpRestTransportError(Exception):

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CpRestTransport:

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
        self._base_url = base_url.rstrip("/")
        self._oauth_signer = oauth_signer
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._penalty_box_s = penalty_box_s
        self._sleep = sleep
        jitter_source: JitterSource = (
            jitter if jitter is not None else (lambda: _DEFAULT_JITTER_S)
        )
        self._jitter = jitter_source
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
        return self._request("GET", path, params=params, _query=params)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=body)

    def _request(
        self, method: str, path: str, *, _query: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
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
        outcome = retry_state.outcome
        exception = outcome.exception() if outcome is not None else None
        if not isinstance(exception, httpx.HTTPStatusError):  # pragma: no cover — predicate gates
            return self._backoff_base_s
        return self._retry_delay(exception.response, retry_state.attempt_number)

    def _retry_delay(self, response: httpx.Response, attempt_number: int) -> float:
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return min(self._penalty_box_s, max(0.0, float(header)))
            except ValueError:
                pass
        backoff = self._backoff_base_s * (2.0 ** max(0, attempt_number - 1))
        return min(_NO_RETRY_AFTER_BACKOFF_CAP_S, backoff) + self._jitter()

    def streaming_url(self) -> str:
        scheme_swapped = self._base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        return f"{scheme_swapped}/ws"

    def close(self) -> None:
        self._client.close()


class _BoundedRestTransport:
    def __init__(self, inner: SupportsRestGet, semaphore: threading.BoundedSemaphore) -> None:
        self._inner = inner
        self._semaphore = semaphore

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._semaphore:
            return self._inner.get(path, params)


def bounded_transport(inner: SupportsRestGet, *, width: int) -> SupportsRestGet:
    if width < 1:
        raise ValueError(f"bounded_transport width must be >= 1, got {width}")
    return _BoundedRestTransport(inner, threading.BoundedSemaphore(width))
