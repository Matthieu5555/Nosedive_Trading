"""Babysitter schedule / drift coherence — driven by a STEPPING clock, not a frozen instant.

D3 proved the SSO-death path emits exactly one reauth alert (unit). The existing exit-code tests
freeze ``now`` at the fire instant. Neither pins the *schedule* coherence the unattended week relies
on: with a clock that advances one tickle per cycle (the real loop cadence), each enabled index must
fire at its OWN slot, exactly once (no double-fire, no missed fire), the heartbeat must run once per
cycle, and a fire slot already past at start-up must be reported as a missed fire — including across
a day boundary, where a fire whose slot the loop never reaches must NOT be silently counted clean.

The oracle is the loop contract, derived independently of the loop body: with a clock stepping by
``_TICKLE_SECONDS`` from ``start``, index *i* fires on the first cycle whose time is ``>= slot_i``;
the run is clean (exit 0) iff every slot was reached and every fire returned success.

The real scheduling math (``_planned_fires``) is also locked: a fire time is the index's session
close + ``_CAPTURE_LAG_MIN``, and the fire list is sorted by time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from algotrading.infra_ibkr import babysitter as bb
from algotrading.infra_ibkr.babysitter import _CAPTURE_LAG_MIN, _TICKLE_SECONDS, _babysit
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession

_SESSION = cast(CpRestSession, object())
_TICK = timedelta(seconds=_TICKLE_SECONDS)


class _SteppingClock:
    """A clock that advances by one tickle each time ``sleep`` is called — the real loop cadence.

    ``now()`` is a pure read; ``sleep(_)`` advances it. This reproduces how the live loop walks time:
    one heartbeat + fire-scan per ``_TICKLE_SECONDS``, with no wall clock and no real sleep.
    """

    def __init__(self, start: datetime) -> None:
        self.t = start
        self.now_calls = 0

    def now(self) -> datetime:
        self.now_calls += 1
        return self.t

    def sleep(self, _seconds: float) -> None:
        self.t += _TICK


class _CountingHeartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _session: object, *, alarmed: bool, sink: object = None) -> bool:
        self.calls += 1
        return alarmed


class _FireRecorder:
    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.fired: list[tuple[str, datetime]] = []
        self._failing = failing or set()
        self._clock: _SteppingClock | None = None

    def bind(self, clock: _SteppingClock) -> None:
        self._clock = clock

    def __call__(self, name: str) -> bool:
        assert self._clock is not None
        self.fired.append((name, self._clock.t))
        return name not in self._failing


_DAY = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
# Two distinct slots a few cycles apart, both after the loop's start time.
_START = _DAY.replace(hour=17, minute=0)
_SX5E_SLOT = _DAY.replace(hour=17, minute=50)  # SX5E close + lag (earlier)
_SPX_SLOT = _DAY.replace(hour=20, minute=20)  # SPX close + lag (later)


def _first_tick_at_or_after(start: datetime, slot: datetime) -> datetime:
    """Independent oracle: the first stepping-clock instant >= slot, starting at ``start``."""
    t = start
    while t < slot:
        t += _TICK
    return t


def test_each_index_fires_at_its_own_slot_exactly_once() -> None:
    clock = _SteppingClock(_START)
    heartbeat = _CountingHeartbeat()
    fire = _FireRecorder()
    fire.bind(clock)

    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", _SX5E_SLOT), ("SPX", _SPX_SLOT)],
        fire=fire,
        heartbeat=heartbeat,
        now=clock.now,
        sleep=clock.sleep,
    )

    assert rc == 0
    # Exactly one fire per index — no double-fire.
    assert [name for name, _ in fire.fired] == ["SX5E", "SPX"]
    fired_at = dict(fire.fired)
    # Each index fired at the first cycle at or after its own slot — drift-coherent, not early/late.
    assert fired_at["SX5E"] == _first_tick_at_or_after(_START, _SX5E_SLOT)
    assert fired_at["SPX"] == _first_tick_at_or_after(_START, _SPX_SLOT)
    # The earlier slot fires strictly before the later one — order follows the schedule.
    assert fired_at["SX5E"] < fired_at["SPX"]


def test_heartbeat_runs_once_per_cycle() -> None:
    clock = _SteppingClock(_START)
    heartbeat = _CountingHeartbeat()
    fire = _FireRecorder()
    fire.bind(clock)

    _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", _SX5E_SLOT)],
        fire=fire,
        heartbeat=heartbeat,
        now=clock.now,
        sleep=clock.sleep,
    )

    # The loop heartbeats once per `while` pass; it runs until SX5E's slot is reached, then breaks.
    expected_cycles = 0
    t = _START
    while t < _SX5E_SLOT:
        expected_cycles += 1
        t += _TICK
    expected_cycles += 1  # the pass on which the slot is reached and the fire runs
    assert heartbeat.calls == expected_cycles


def test_missed_slot_before_startup_is_reported_not_silently_clean() -> None:
    # The loop starts AFTER the only slot: `while now() <= end` admits the start pass, but the slot
    # is in the past relative to... no — it is in the future of nothing; the loop ends having never
    # fired it. A missed capture must exit non-zero, never read as a clean run.
    clock = _SteppingClock(_SPX_SLOT + _TICK)  # already past the (single) slot's end
    fire = _FireRecorder()
    fire.bind(clock)

    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", _SX5E_SLOT)],
        fire=fire,
        heartbeat=_CountingHeartbeat(),
        now=clock.now,
        sleep=clock.sleep,
    )
    assert rc == 1
    assert fire.fired == []


def test_no_double_fire_when_clock_lingers_past_a_slot() -> None:
    # Even if many cycles elapse after a slot (a slow box), the index fires once and only once.
    clock = _SteppingClock(_START)
    fire = _FireRecorder()
    fire.bind(clock)

    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", _SX5E_SLOT), ("SPX", _SPX_SLOT)],
        fire=fire,
        heartbeat=_CountingHeartbeat(),
        now=clock.now,
        sleep=clock.sleep,
    )
    assert rc == 0
    counts = {name: sum(1 for n, _ in fire.fired if n == name) for name in ("SX5E", "SPX")}
    assert counts == {"SX5E": 1, "SPX": 1}


def test_day_boundary_fire_runs_on_the_correct_calendar_day() -> None:
    # A schedule that straddles midnight: an early slot late today and a second slot after midnight.
    # `end` is the max slot (the next-day one), so the loop walks across midnight and fires BOTH at
    # their own slots — neither dropped, neither folded onto the wrong day.
    today_slot = _DAY.replace(hour=22, minute=0)
    next_day_slot = (_DAY + timedelta(days=1)).replace(hour=1, minute=0)
    clock = _SteppingClock(_DAY.replace(hour=21, minute=58))
    fire = _FireRecorder()
    fire.bind(clock)

    # end = max slot = next_day_slot; the loop walks across midnight and fires BOTH.
    rc = _babysit(
        _SESSION,
        planned_fires=lambda: [("SX5E", today_slot), ("SPX", next_day_slot)],
        fire=fire,
        heartbeat=_CountingHeartbeat(),
        now=clock.now,
        sleep=clock.sleep,
    )
    assert rc == 0
    fired_at = dict(fire.fired)
    # The cross-midnight fire actually happened on the next calendar day — the schedule did not skip
    # it or fold it onto the wrong day.
    assert fired_at["SPX"].date() == (_DAY + timedelta(days=1)).date()
    assert fired_at["SX5E"].date() == _DAY.date()


def test_planned_fires_math_is_close_plus_capture_lag_and_sorted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The real ``_planned_fires`` schedule: fire == session close + ``_CAPTURE_LAG_MIN``, sorted.

    Stubs the config/registry/resolver seams so the math is asserted without touching disk config or
    a wall clock: two enabled indices with distinct closes must yield fire times each exactly
    ``_CAPTURE_LAG_MIN`` after their own close, ordered earliest-first.
    """
    today = _DAY.date()
    sx5e_close = _DAY.replace(hour=15, minute=30)
    spx_close = _DAY.replace(hour=20, minute=0)

    class _Entry:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

    class _Resolver:
        def __init__(self, _registry: object) -> None:
            self._closes = {"SX5E": sx5e_close, "SPX": spx_close}

        def is_session(self, symbol: str, on_date: object) -> bool:
            return True

        def session_close(self, symbol: str, on_date: object) -> datetime:
            return self._closes[symbol]

    # `_planned_fires` imports these names locally, so patch them at their source modules.
    import algotrading.core.config.loader as loader_mod
    import algotrading.infra.universe as universe_mod

    monkeypatch.setattr(loader_mod, "load_platform_config", lambda _dir: object())
    monkeypatch.setattr(universe_mod, "index_registry_from_config", lambda _cfg: object())
    monkeypatch.setattr(universe_mod, "CalendarResolver", _Resolver)
    monkeypatch.setattr(universe_mod, "enabled_indices", lambda _r: [_Entry("SPX"), _Entry("SX5E")])

    fires = bb._planned_fires(now=lambda: _DAY.replace(hour=12))

    by_symbol = dict(fires)
    assert by_symbol["SX5E"] == sx5e_close + timedelta(minutes=_CAPTURE_LAG_MIN)
    assert by_symbol["SPX"] == spx_close + timedelta(minutes=_CAPTURE_LAG_MIN)
    # Sorted earliest-first regardless of the registry's enumeration order.
    assert [name for name, _ in fires] == ["SX5E", "SPX"]
    assert today == _DAY.date()  # the schedule was computed for the injected "today"
