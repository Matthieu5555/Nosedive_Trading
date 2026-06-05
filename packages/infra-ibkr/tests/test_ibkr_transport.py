"""Unit tests for the IBKR transport's round-trip guard, with a stub ib_async client so the
timeout-to-degradation contract is verified without a live broker. Skipped when the optional
ib_async dependency is absent (it is kept out of the connectivity package import on purpose)."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("ib_async")

from algotrading.infra.connectivity.session import TransportError  # noqa: E402 - after importorskip
from algotrading.infra_ibkr.connectivity.ibkr_transport import (
    IbkrTransport,  # noqa: E402 - after importorskip
)

_BROKER_TIME = datetime(2026, 1, 16, 15, 0, tzinfo=UTC)


class _StubIB:
    """Stands in for the ib_async client: ``run`` returns a fixed time or raises a scripted error,
    mirroring how ``util.run`` surfaces a timeout as ``TimeoutError``."""

    def __init__(self, *, time_value=None, raises=None):
        self._time_value = time_value
        self._raises = raises

    def reqCurrentTimeAsync(self):  # noqa: N802 - mirrors the ib_async method name
        return "pending-round-trip"  # opaque awaitable; the stub ``run`` ignores it

    def run(self, *_awaitables, timeout=None):
        if self._raises is not None:
            raise self._raises
        return self._time_value


def _transport(stub: _StubIB) -> IbkrTransport:
    transport = IbkrTransport()
    transport._ib = stub  # swap the real client for the stub
    return transport


def test_current_time_returns_broker_clock():
    assert _transport(_StubIB(time_value=_BROKER_TIME)).current_time() == _BROKER_TIME


def test_current_time_raises_transport_error_on_timeout():
    transport = _transport(_StubIB(raises=TimeoutError()))
    with pytest.raises(TransportError, match="did not complete"):
        transport.current_time()
