"""The session supervisor: the one reconnect home beneath the adapter (ADR 0027).

The supervisor owns connect/reconnect-with-backoff, the client-id convention, and loss-aware
gap recording — and nothing else (no tick type, no pull loop). These pin the named behaviours:
the backoff delay sequence is exactly the documented formula, client ids come from disjoint
bands, a recover() reconnects/re-subscribes and records the outage as a GapInterval, and connect
retries on the backoff schedule. Expected values are derived from the documented formula and
band constants (independent oracles), not read back from the code.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.infra.connectivity import (
    BackoffSchedule,
    BrokerConfig,
    ClientIdError,
    ConnectionFailed,
    ManualClock,
    SessionSupervisor,
    UnknownServiceError,
    load_broker_config,
)

_T0 = datetime(2026, 6, 1, 13, 30, tzinfo=UTC)

# The operational broker policy loaded from the shipped configs/broker.yaml — the same
# bands and backoff production runs, so these tests prove the YAML actually flows.
_BROKER: BrokerConfig = load_broker_config(Path(__file__).resolve().parents[3] / "configs")


class _FakeSession:
    """A minimal SupervisedSession: connect/disconnect/subscribe, with scripted connect failures."""

    def __init__(self, *, connect_failures: int = 0) -> None:
        self._connected = False
        self._remaining_failures = connect_failures
        self.connect_count = 0
        self.subscribe_calls: list[str] = []
        self.client_ids: list[int] = []

    def connect(self, client_id: int) -> None:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ConnectionFailed(f"synthetic failure ({self._remaining_failures} left)")
        self._connected = True
        self.connect_count += 1
        self.client_ids.append(client_id)

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def request_option_chain(self, symbol: str) -> tuple[Mapping[str, object], ...]:
        return ({"symbol": symbol},)

    def subscribe(self, broker_contract_id: str) -> None:
        self.subscribe_calls.append(broker_contract_id)


# -- backoff schedule (the deterministic formula) ---------------------------


def test_backoff_delay_sequence_matches_the_documented_formula() -> None:
    schedule = BackoffSchedule(base_seconds=1.0, factor=2.0, cap_seconds=30.0)
    # Hand-derived from delay_for(n) = min(cap, base * factor**n): 1,2,4,8,16,30,30,...
    assert [schedule.delay_for(n) for n in range(7)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_backoff_rejects_a_negative_attempt() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 0"):
        BackoffSchedule().delay_for(-1)


# -- client-id bands --------------------------------------------------------


def test_client_id_bands_are_disjoint_across_services() -> None:
    # Each service draws from its own band; instances offset within it. Hand-derived from the
    # band table in configs/broker.yaml (collector=2000, replay=3000, smoke=9000) — disjoint
    # by construction, and loaded from YAML rather than a code constant (C7).
    assert _BROKER.client_id_for("collector") == 2000
    assert _BROKER.client_id_for("collector", 5) == 2005
    assert _BROKER.client_id_for("replay") == 3000
    assert _BROKER.client_id_for("smoke") == 9000


def test_unknown_service_and_out_of_band_instance_are_refused() -> None:
    with pytest.raises(UnknownServiceError):
        _BROKER.client_id_for("nope")
    with pytest.raises(ClientIdError):
        _BROKER.client_id_for("collector", 1000)  # one past the band width


def test_broker_config_backoff_comes_from_yaml() -> None:
    # The reconnect backoff is loaded from broker.yaml too, not a code default.
    assert _BROKER.backoff.delay_for(0) == 1.0   # base_seconds
    assert _BROKER.backoff.delay_for(5) == 30.0  # capped at cap_seconds


# -- connect / recover ------------------------------------------------------


def test_connect_retries_on_the_backoff_schedule_until_it_succeeds() -> None:
    session = _FakeSession(connect_failures=2)
    clock = ManualClock(start=_T0)
    supervisor = SessionSupervisor(
        session, client_id=2000, clock=clock,
        backoff=BackoffSchedule(base_seconds=1.0, factor=2.0, cap_seconds=30.0),
    )
    supervisor.connect()
    assert supervisor.is_healthy()
    assert session.connect_count == 1  # one success after two failures
    # Backoff slept 1s then 2s between the two failed attempts (the documented delays).
    assert clock.now() == _T0 + timedelta(seconds=3)


def test_recover_reconnects_resubscribes_and_records_the_outage() -> None:
    session = _FakeSession()
    clock = ManualClock(start=_T0)
    supervisor = SessionSupervisor(session, client_id=2000, clock=clock)
    supervisor.connect()
    supervisor.subscribe("con-1")
    supervisor.subscribe("con-2")

    dropped_at = clock.now()
    gap = supervisor.recover(dropped_at)

    # Re-subscribed both instruments on reconnect (initial 2 + 2 on recover).
    assert session.subscribe_calls == ["con-1", "con-2", "con-1", "con-2"]
    assert supervisor.reconnect_count == 1
    assert gap.started_at == dropped_at
    assert supervisor.reconnects == [gap]


def test_connect_gives_up_after_max_attempts() -> None:
    session = _FakeSession(connect_failures=5)
    supervisor = SessionSupervisor(
        session, client_id=2000, clock=ManualClock(start=_T0), max_reconnect_attempts=2
    )
    with pytest.raises(ConnectionFailed):
        supervisor.connect()
