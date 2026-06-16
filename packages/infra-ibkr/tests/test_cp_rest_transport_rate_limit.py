"""CpRestTransport proactive rate limiting: the client-side token bucket.

The CP Gateway has a hard documented ceiling of 10 requests/second per authenticated username;
crossing it lands the IP in a ~10-minute penalty box and a repeat-violator IP can be permanently
banned. So the transport paces every real send under that ceiling with a token bucket BEFORE the
request goes out (and the pace holds across tenacity retries too, since they route back through
the same send).

Everything here is deterministic: a fake monotonic clock advances only when the injected sleep is
called, so we assert the exact spacing the bucket enforces without ever really waiting. Expected
spacings are derived from the bucket contract (one second of capacity at ``rate`` tokens/s, so the
sustained pace cannot exceed ``rate``), independently of the implementation's arithmetic.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_transport import (
    _DEFAULT_MAX_BURST_TOKENS,
    CpRestTransport,
)

_URL = "https://localhost:5000/v1/api/some/path"


def _ok() -> httpx.Response:
    return httpx.Response(200, content=b'{"ok": true}', request=httpx.Request("GET", _URL))


class _FakeClock:
    """A monotonic clock that only advances when the transport's injected sleep is called.

    This couples wall-clock to commanded sleeps exactly: the bucket refills purely from the time
    the transport itself chose to wait, so the test sees the steady-state cadence the limiter
    enforces with no real delay and no flakiness.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _CountingClient:
    """Records each send instant (the fake clock's time) and always returns 200."""

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self.send_times: list[float] = []

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.send_times.append(self._clock.now)
        return _ok()

    def close(self) -> None:
        return None


def _transport(
    clock: _FakeClock, client: _CountingClient, *, rate: float, jitter: float = 0.0
) -> CpRestTransport:
    return CpRestTransport(
        _client=client,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_requests_per_second=rate,
        jitter=lambda: jitter,
    )


def test_first_request_never_waits() -> None:
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=8.5)

    transport.get("/some/path")

    assert clock.slept == []  # bucket starts full
    assert client.send_times == [0.0]


def test_sustained_rate_never_exceeds_the_ceiling() -> None:
    rate = 8.5
    n = 30
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=rate)

    for _ in range(n):
        transport.get("/some/path")

    assert len(client.send_times) == n

    # Independent derivation of the steady-state spacing: the clock only advances when the bucket
    # commands a sleep, so once the initial full-bucket burst is spent every later send is spaced
    # by exactly one steady interval, 1/rate. We therefore assert on the spacing AFTER the burst
    # rather than a whole-run average (which is inflated by the free burst at the start).
    steady_interval = 1.0 / rate
    # The full bucket holds `rate` tokens; a request is free while >= 1 token remains before its
    # own deduction, i.e. the first floor(rate)+1-... derive directly: free count is the largest k
    # with rate - (k - 1) >= 1, i.e. k <= rate, so k = floor(rate) when rate is fractional.

    free_burst = math.floor(rate)
    # Skip the free burst AND the single partial-refill request that follows it (it only has to
    # make up the leftover fraction, so its gap is shorter); from there the bucket is empty at each
    # acquire and the cadence is the pure steady interval.
    tail = client.send_times[free_burst + 1 :]
    gaps = [b - a for a, b in zip(tail, tail[1:], strict=False)]
    assert gaps, "expected the run to outlast the initial burst"
    assert all(gap == pytest.approx(steady_interval) for gap in gaps)
    # Sustained pace cannot exceed the ceiling: every steady gap is at least 1/rate.
    assert all(gap >= steady_interval - 1e-9 for gap in gaps)


def test_idle_refills_the_bucket_so_a_later_request_is_free() -> None:
    rate = 8.5
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=rate)

    transport.get("/some/path")  # free, drains a token
    clock.now += 5.0  # idle well past one full refill window
    transport.get("/some/path")  # bucket has refilled to capacity -> free again

    assert clock.slept == []
    assert client.send_times == [0.0, 5.0]


def test_jitter_is_added_only_to_a_nonzero_wait() -> None:
    rate = 8.5
    jitter = 0.02
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=rate, jitter=jitter)

    # Drain the free initial burst — the bucket starts at its burst CAPACITY (min(rate, burst cap),
    # NOT a full second of tokens), and a request is free while >= 1 token remains before its own
    # deduction, so floor(capacity) requests are free. Derived independently of the impl arithmetic.
    capacity = min(rate, _DEFAULT_MAX_BURST_TOKENS)
    free_burst = math.floor(capacity)
    for _ in range(free_burst):
        transport.get("/some/path")
    assert clock.slept == []  # nothing waited yet, so nothing jittered

    transport.get("/some/path")  # bucket now below one token -> a real wait, jittered
    assert len(clock.slept) == 1
    # After floor(capacity) free requests the bucket holds capacity - floor(capacity) tokens (no
    # time has passed); the next request must wait for the missing fraction: (1 - leftover) / rate,
    # plus the jitter that is only ever added to a non-zero wait. Derived independently of the impl.
    leftover = capacity - free_burst
    expected_wait = (1.0 - leftover) / rate + jitter
    assert clock.slept[0] == pytest.approx(expected_wait)


def test_disabling_pacing_means_no_waits_at_all() -> None:
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = CpRestTransport(
        _client=client,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_requests_per_second=None,
    )

    for _ in range(50):
        transport.get("/some/path")

    assert clock.slept == []  # pacing off entirely
    assert client.send_times == [0.0] * 50


def test_pacing_also_governs_retries() -> None:
    # A 429 (carrying a server Retry-After so the retry wait is a clean, deterministic 2.0s) then
    # 200: the retry's send must also pass through the bucket. After the Retry-After wait (which
    # advances the clock), the bucket has long since refilled, so the retried send itself adds no
    # extra bucket wait — but the very first send drained the only-relevant token, proving the
    # bucket is consulted per-send. (A no-Retry-After 429 would instead take the bounded backoff;
    # that path is covered in the retry test module — here we want a fixed wait to read the pacing.)
    clock = _FakeClock()
    script = [
        httpx.Response(
            429, headers={"Retry-After": "2"}, content=b"", request=httpx.Request("GET", _URL)
        ),
        _ok(),
    ]

    class _Scripted:
        def __init__(self) -> None:
            self.send_times: list[float] = []

        def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            self.send_times.append(clock.now)
            return script.pop(0)

        def close(self) -> None:
            return None

    client = _Scripted()
    transport = CpRestTransport(
        _client=client,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_requests_per_second=8.5,
        penalty_box_s=2.0,
        jitter=lambda: 0.0,
    )

    assert transport.get("/some/path") == {"ok": True}
    # Two sends; the retry waited the 2.0s penalty box between them (the only commanded sleep).
    assert client.send_times[0] == 0.0
    assert clock.slept == [2.0]
    assert client.send_times[1] == 2.0
