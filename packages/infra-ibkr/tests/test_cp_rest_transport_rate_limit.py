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

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _CountingClient:

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

    assert clock.slept == []
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

    steady_interval = 1.0 / rate

    free_burst = math.floor(rate)
    tail = client.send_times[free_burst + 1 :]
    gaps = [b - a for a, b in zip(tail, tail[1:], strict=False)]
    assert gaps, "expected the run to outlast the initial burst"
    assert all(gap == pytest.approx(steady_interval) for gap in gaps)
    assert all(gap >= steady_interval - 1e-9 for gap in gaps)


def test_idle_refills_the_bucket_so_a_later_request_is_free() -> None:
    rate = 8.5
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=rate)

    transport.get("/some/path")
    clock.now += 5.0
    transport.get("/some/path")

    assert clock.slept == []
    assert client.send_times == [0.0, 5.0]


def test_jitter_is_added_only_to_a_nonzero_wait() -> None:
    rate = 8.5
    jitter = 0.02
    clock = _FakeClock()
    client = _CountingClient(clock)
    transport = _transport(clock, client, rate=rate, jitter=jitter)

    capacity = min(rate, _DEFAULT_MAX_BURST_TOKENS)
    free_burst = math.floor(capacity)
    for _ in range(free_burst):
        transport.get("/some/path")
    assert clock.slept == []

    transport.get("/some/path")
    assert len(clock.slept) == 1
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

    assert clock.slept == []
    assert client.send_times == [0.0] * 50


def test_pacing_also_governs_retries() -> None:
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
    assert client.send_times[0] == 0.0
    assert clock.slept == [2.0]
    assert client.send_times[1] == 2.0
