"""Round-trip the frozen `BrokerSession` seam against a trivial in-memory fake.

M0 freezes the seam; M5's three brokers satisfy it and M4's actor drives it. This
proves the contract is satisfiable broker-agnostically, and pins the deterministic,
idempotent event id (`content_event_id`) the collector relies on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator, Mapping

from algotrading.infra.contracts import BrokerSession, BrokerTick, content_event_id


class FakeSession:
    """A deterministic in-memory broker, broker-agnostic in every type it exposes."""

    def __init__(self, ticks: list[BrokerTick]) -> None:
        self._ticks = ticks
        self._connected = False
        self._subscribed: list[str] = []

    def connect(self, client_id: int) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        return ({"symbol": symbol, "strike": 5000.0, "right": "C"},)

    def subscribe(self, broker_contract_id: str) -> None:
        self._subscribed.append(broker_contract_id)

    def ticks(self) -> Iterator[BrokerTick]:
        yield from self._ticks


def test_fake_satisfies_the_broker_session_port() -> None:
    assert isinstance(FakeSession([]), BrokerSession)


def test_fake_session_drives_connect_subscribe_ticks() -> None:
    ticks = [
        BrokerTick(broker_contract_id="con-1", field_name="last", value=5000.0, sequence=1),
        BrokerTick(broker_contract_id="con-1", field_name="bid", value=4999.0, sequence=2),
    ]
    session: BrokerSession = FakeSession(ticks)
    session.connect(client_id=7)
    assert session.is_connected()
    session.subscribe("con-1")
    assert list(session.ticks()) == ticks
    session.disconnect()
    assert not session.is_connected()


def test_content_event_id_is_idempotent_for_a_redelivered_tick() -> None:
    # Same instrument/field/sequence -> same id: a reconnect re-delivery dedups.
    first = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    again = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    assert first == again


def test_content_event_id_distinguishes_distinct_observations() -> None:
    base = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    assert content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 2) != base
    assert content_event_id("SPX|IND|CBOE|USD|1|con-1||", "bid", 1) != base


def test_content_event_id_is_stable_across_processes() -> None:
    expected = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    code = (
        "from algotrading.infra.contracts import content_event_id;"
        "print(content_event_id('SPX|IND|CBOE|USD|1|con-1||','last',1))"
    )
    for seed in ("0", "3", "55555"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected
