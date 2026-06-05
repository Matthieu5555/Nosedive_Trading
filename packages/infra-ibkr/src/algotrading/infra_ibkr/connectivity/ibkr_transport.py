"""Concrete IBKR transport backed by ib_async, plugged behind the BrokerTransport seam.

Kept out of the package ``__init__`` so importing the connectivity layer never drags in the
broker client: only code that actually talks to IBKR imports this module. Requires the optional
ib_async dependency (install the ``ibkr`` dependency group). The session lifecycle, backoff and
health logic live in ``session.py`` and stay broker-agnostic; this file is just the wire.
"""

from __future__ import annotations

from datetime import datetime

from algotrading.core.log import get_logger
from algotrading.infra.connectivity.session import TransportError
from ib_async import IB

_log = get_logger(__name__)


class IbkrTransport:
    """Drives a TWS / IB Gateway connection through ib_async, exposing the BrokerTransport API.

    A round-trip (``current_time``) is a current-time request: the cheapest call that proves the
    broker is actually answering, not just that a socket is open. It is bounded by a timeout so a
    frozen broker degrades the session into a logged reconnect instead of blocking forever —
    without that bound the lifecycle could never observe the failure.
    """

    def __init__(self, *, connect_timeout: float = 10.0, ping_timeout: float = 5.0) -> None:
        self._ib = IB()
        self._connect_timeout = connect_timeout
        self._ping_timeout = ping_timeout

    @property
    def ib(self) -> IB:
        """The underlying ib_async client, for contract resolution and market-data requests."""
        return self._ib

    def open(self, host: str, port: int, client_id: int) -> None:
        try:
            self._ib.connect(host, port, clientId=client_id, timeout=self._connect_timeout)
        except Exception as exc:  # noqa: BLE001 - ib_async surfaces heterogeneous vendor errors
            raise TransportError(f"IBKR connect failed: {exc}") from exc

    def close(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def current_time(self) -> datetime:
        """Broker clock via a timeout-bounded round-trip; raise TransportError on no answer."""
        try:
            return self._ib.run(self._ib.reqCurrentTimeAsync(), timeout=self._ping_timeout)
        except Exception as exc:  # noqa: BLE001 - timeout or any vendor failure means no round-trip
            _log.warning("IBKR round-trip failed: %s", exc)
            raise TransportError(f"IBKR round-trip did not complete: {exc}") from exc
