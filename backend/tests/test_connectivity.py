"""Connectivity: backoff schedule, client-id convention, resilient stream, event id.

These are behaviour tests driven against an injected clock and the in-memory fake
session — no live broker, no wall-clock sleeps. Each names a specific guarantee from
``tasks/02-market-data-plane.md`` and asserts the bound, not just that the code runs.
The backoff expected values are derived independently from the documented formula
``min(cap, base * factor**attempt)``, not read back from the code under test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from connectivity import (
    BackoffSchedule,
    BrokerSession,
    BrokerTick,
    ClientIdError,
    FakeBrokerSession,
    ManualClock,
    ScriptedDrop,
    ScriptItem,
    SessionSupervisor,
    UnknownServiceError,
    client_id_for,
    content_event_id,
)

_SRC = Path(__file__).resolve().parents[1] / "src"


def _tick(sequence: int, value: float, *, field: str = "bid") -> BrokerTick:
    return BrokerTick(
        broker_contract_id="o-AAPL-C-100",
        field_name=field,
        value=value,
        sequence=sequence,
        exchange_ts=datetime(2026, 6, 1, 13, 30, sequence, tzinfo=UTC),
    )


# -- backoff schedule -------------------------------------------------------


def test_backoff_schedule_matches_the_documented_formula() -> None:
    # Independent oracle: the documented sequence for base=1, factor=2, cap=30.
    schedule = BackoffSchedule()
    expected = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    assert [schedule.delay_for(attempt) for attempt in range(8)] == expected


def test_backoff_rejects_a_negative_attempt() -> None:
    with pytest.raises(ValueError, match="attempt"):
        BackoffSchedule().delay_for(-1)


def test_backoff_never_overflows_on_a_very_long_outage() -> None:
    # A huge attempt count must stay pinned at the cap, not raise OverflowError.
    assert BackoffSchedule().delay_for(100_000) == 30.0


def test_reconnect_waits_the_documented_backoff_sequence() -> None:
    # Five failed connects then success: the supervisor must sleep 1, 2, 4, 8, 16s,
    # asserted against the injected clock's recorded sleeps — no real waiting.
    clock = ManualClock()
    session = FakeBrokerSession(connect_failures=5)
    supervisor = SessionSupervisor(session, client_id=2000, clock=clock)

    supervisor.connect()

    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert session.is_connected()


def test_a_clean_first_connect_sleeps_nothing() -> None:
    clock = ManualClock()
    supervisor = SessionSupervisor(FakeBrokerSession(), client_id=2000, clock=clock)
    supervisor.connect()
    assert clock.sleeps == []


# -- client-id convention ---------------------------------------------------


def test_client_ids_differ_between_services() -> None:
    # Two services connecting to the same gateway must never request the same id.
    assert client_id_for("universe") != client_id_for("collector")


def test_client_ids_differ_between_instances_of_one_service() -> None:
    assert client_id_for("collector", 0) != client_id_for("collector", 1)


def test_unknown_service_is_refused_with_diagnostics() -> None:
    with pytest.raises(UnknownServiceError) as info:
        client_id_for("does-not-exist")
    assert info.value.service == "does-not-exist"
    assert "collector" in info.value.known


def test_instance_outside_its_band_is_refused() -> None:
    # An index at the band width would collide with the next service's band.
    with pytest.raises(ClientIdError) as info:
        client_id_for("collector", 1000)
    assert info.value.instance == 1000


# -- resilient stream across a drop -----------------------------------------


def test_stream_resumes_across_a_drop_without_losing_ticks() -> None:
    # Ticks straddle a mid-session drop; the supervisor must reconnect and deliver
    # every scripted tick in order, with no tick lost or duplicated by the transport.
    clock = ManualClock()
    script: list[ScriptItem] = [_tick(1, 10.0), _tick(2, 11.0), ScriptedDrop(), _tick(3, 12.0)]
    session = FakeBrokerSession(script=script)
    supervisor = SessionSupervisor(session, client_id=2000, clock=clock)
    supervisor.connect()
    supervisor.subscribe("o-AAPL-C-100")

    delivered = list(supervisor.stream())

    assert [item.tick.value for item in delivered] == [10.0, 11.0, 12.0]
    assert supervisor.reconnect_count == 1


def test_a_drop_records_the_outage_and_resubscribes() -> None:
    # The drop is followed by a flaky reconnect that fails twice before succeeding, so
    # the supervisor backs off 1s then 2s. The recorded outage therefore spans exactly
    # 3.0s — derived by hand from the documented schedule, not read from the code.
    clock = ManualClock()
    script: list[ScriptItem] = [
        _tick(1, 10.0),
        ScriptedDrop(connect_failures_after=2),
        _tick(2, 11.0),
    ]
    session = FakeBrokerSession(script=script)
    supervisor = SessionSupervisor(session, client_id=2000, clock=clock)
    supervisor.connect()
    supervisor.subscribe("o-AAPL-C-100")

    delivered = list(supervisor.stream())

    assert supervisor.reconnect_count == 1
    assert clock.sleeps == [1.0, 2.0]
    assert supervisor.reconnects[0].duration_seconds() == 3.0
    # The first tick after the drop is tagged with the gap that just ended.
    after_drop = delivered[1]
    assert after_drop.tick.value == 11.0
    assert after_drop.gap_before == supervisor.reconnects[0]
    # The instrument was re-subscribed on reconnect: subscribed once, then again.
    assert session.subscribe_calls == ("o-AAPL-C-100", "o-AAPL-C-100")
    assert session.connect_count == 2  # initial + one reconnect


def test_stream_without_a_drop_records_no_reconnect() -> None:
    clock = ManualClock()
    session = FakeBrokerSession(script=[_tick(1, 10.0), _tick(2, 11.0)])
    supervisor = SessionSupervisor(session, client_id=2000, clock=clock)
    supervisor.connect()
    delivered = list(supervisor.stream())
    assert [item.gap_before for item in delivered] == [None, None]
    assert supervisor.reconnect_count == 0


# -- the fake and replay sessions satisfy the broker-agnostic protocol ------


def test_fake_session_satisfies_the_broker_session_protocol() -> None:
    assert isinstance(FakeBrokerSession(), BrokerSession)


# -- deterministic event id -------------------------------------------------


def test_event_id_is_deterministic_for_the_same_observation() -> None:
    first = content_event_id("AAPL|STK|SMART|USD|1|u-AAPL|||", "bid", 7)
    again = content_event_id("AAPL|STK|SMART|USD|1|u-AAPL|||", "bid", 7)
    assert first == again


@pytest.mark.parametrize(
    ("instrument_key", "field_name", "sequence"),
    [
        ("AAPL|STK|SMART|USD|1|u-AAPL|||", "ask", 7),  # different field
        ("AAPL|STK|SMART|USD|1|u-AAPL|||", "bid", 8),  # different sequence
        ("MSFT|STK|SMART|USD|1|u-MSFT|||", "bid", 7),  # different instrument
    ],
)
def test_event_id_distinguishes_different_observations(
    instrument_key: str, field_name: str, sequence: int
) -> None:
    baseline = content_event_id("AAPL|STK|SMART|USD|1|u-AAPL|||", "bid", 7)
    assert content_event_id(instrument_key, field_name, sequence) != baseline


def test_event_id_is_identical_in_a_separate_process() -> None:
    # The classic determinism trap: a hash that depends on PYTHONHASHSEED is stable
    # within a process and drifts between runs. Compute the id in a fresh interpreter
    # (its own random hash seed) and require byte equality with the in-process value.
    code = (
        "from connectivity import content_event_id;"
        "print(content_event_id('AAPL|STK|SMART|USD|1|u-AAPL|||', 'bid', 7))"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONHASHSEED"}
    env["PYTHONPATH"] = str(_SRC)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == content_event_id("AAPL|STK|SMART|USD|1|u-AAPL|||", "bid", 7)
