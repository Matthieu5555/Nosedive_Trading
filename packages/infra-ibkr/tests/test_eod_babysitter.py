"""The babysitter fire-loop exit code, driven by an injected clock — no wall clock, no subprocess.

The babysitter is the manual/headless fallback for the systemd timer: it fires each enabled
index's close-capture at its slot and must report an HONEST exit code so an unattended run is not
mistaken for a success when a capture was missed or errored (the silent-failure gap the 2026-06-15
ingestion audit flagged). The loop used to be untestable (wall-clock ``datetime.now`` + a
``subprocess`` fire); it now injects ``now`` / ``sleep`` / ``fire`` / ``heartbeat`` /
``planned_fires``, so these pin the exit-code contract deterministically.

Oracle: hand-stated fire times + a fixed clock and a fake ``fire`` whose success/failure the test
chooses — the expected exit code follows from the contract (0 iff every planned fire ran AND each
succeeded), derived independently of the loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from algotrading.infra_ibkr.babysitter import _babysit
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession

# A fire instant and a clock pinned to it: at exactly the fire time the slot is due and the loop's
# `while now() <= end` admits one pass, so a due fire runs. No wall clock is read.
FIRE_TS = datetime(2026, 6, 16, 22, 20, tzinfo=UTC)
AFTER_END = datetime(2026, 6, 16, 23, 30, tzinfo=UTC)  # strictly after the last fire's slot

_SESSION = cast(CpRestSession, object())  # heartbeat is injected as a no-op, so it is never touched


def _noop_heartbeat(_session: object, *, alarmed: bool, sink: object = None) -> bool:
    return alarmed


def _at(ts: datetime):  # type: ignore[no-untyped-def]
    return lambda: ts


def test_all_fires_succeed_exits_zero() -> None:
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", FIRE_TS)],
        fire=lambda _name: True,
        heartbeat=_noop_heartbeat,
        now=_at(FIRE_TS),
        sleep=lambda _s: None,
    )
    assert rc == 0


def test_a_failed_fire_exits_nonzero() -> None:
    # The capture ran but returned non-zero (e.g. a QC page escalation) — the run is NOT a success.
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", FIRE_TS)],
        fire=lambda _name: False,
        heartbeat=_noop_heartbeat,
        now=_at(FIRE_TS),
        sleep=lambda _s: None,
    )
    assert rc == 1


def test_one_of_two_fires_failing_exits_nonzero() -> None:
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", FIRE_TS), ("SPX", FIRE_TS)],
        fire=lambda name: name != "SPX",  # SPX fails
        heartbeat=_noop_heartbeat,
        now=_at(FIRE_TS),
        sleep=lambda _s: None,
    )
    assert rc == 1


def test_missed_slot_exits_nonzero() -> None:
    # The babysitter started after every fire's slot had passed: nothing fires, and a missed close
    # must not look like a clean run.
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", FIRE_TS)],
        fire=lambda _name: True,
        heartbeat=_noop_heartbeat,
        now=_at(AFTER_END),
        sleep=lambda _s: None,
    )
    assert rc == 1


def test_no_session_today_is_a_clean_zero() -> None:
    # No enabled index trades today (holiday/weekend) — a clean no-op, not a failure.
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [],
        fire=lambda _name: True,
        heartbeat=_noop_heartbeat,
        now=_at(FIRE_TS),
        sleep=lambda _s: None,
    )
    assert rc == 0
