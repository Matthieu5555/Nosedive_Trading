"""1G — the EOD daily-close runner: the cases TESTING.md names for the cron one-shot.

The runner (``orchestration.eod_runner``) is the importable core behind ``scripts/eod_run.py``:
the systemd timer fires it once per market day, it reads the 1J registry's enabled indices,
skips holidays via the calendar resolver, captures each index at its own ``session_close``,
binds one ``correlation_id``, calls ``run_end_of_day``, and freezes a per-run manifest. These
tests pin, per the 1G spec's Test surface:

* ``test_eod_run_builds_and_invokes`` — ``main`` calls ``run_end_of_day`` with a bound
  correlation id, the resolved trade date, an injected clock and a full ``EodStages``; no
  wall-clock read.
* ``test_eod_run_idempotent_refire`` — two fires for one date; the second RE-RUNS every stage
  (overwrite-by-re-run, ADR 0032 refined) and converges to the same store state, no duplicate
  derived rows. Oracle: the idempotent stage writes (replace-derived, append-dedup raw).
* ``test_eod_run_missed_day_catchup`` — fire D-2, then D (skip D-1), then D-1; the ledger shows
  D-1 filled and no gap (the Persistent=true catch-up the timer drives).
* ``test_eod_run_midrun_kill_restart_converges`` — a stage raises → ``main`` non-zero, the
  failed stage not recorded; re-fire clean → the ledger is gap-free for the date.
* ``test_eod_run_failure_exit_code`` — a raising stage yields a non-zero process exit.
* ``test_eod_run_registry_driven_index_set`` — exactly the enabled indices in the fired
  calendar are captured; per-index ``as_of`` equals the resolver's ``session_close``; no
  hardcoded list.
* ``test_eod_run_skips_exchange_holiday`` — a non-session date is a clean no-op.
* edge cases: empty ledger / first run; a future ``--trade-date`` rejected; an all-clean date a
  no-op; an empty enabled set a clean no-op.
* ``test_eod_capture_units_carry_the_adr_0032_obligations`` — the committed unit files carry
  ``Persistent=true`` / ``Restart=on-failure`` / ``OnFailure=`` / ``Type=oneshot`` / an
  explicit-timezone ``OnCalendar=``.

The expected values are derived independently of the runner: the fired-index set and per-index
close instants come from a hand-written registry + a fake resolver (the oracle), and the
gap/backlog expectations come from ``run_state.py``'s public functions, never from the runner's
own return.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    config_hashes,
)
from algotrading.core.manifest import validate_manifest
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.orchestration import (
    EOD_STAGES,
    EodStages,
    RunnerDeps,
    backlog_stages,
    completed_stages,
    last_healthy_trade_date,
    read_stage_runs,
)
from algotrading.infra.orchestration.eod_runner import (
    EOD_JOB_NAME,
    EodRunError,
    FiredIndex,
    main,
    plan_fire,
    run_fire,
)
from algotrading.infra.storage import ParquetStore, RunRegistry, RunStatus
from algotrading.infra.universe import IndexRegistry, parse_index_registry
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[3]
CONNECTIVITY = REPO_ROOT / "documentation" / "connectivity"

# A fixed clock instant: the fire's "today". The trade dates the tests use are on/before it so
# nothing is rejected as future. Eurex/NYSE close instants below are hand-encoded, not read back.
CLOCK_DAY = date(2026, 6, 5)
CLOCK_NOW = datetime(2026, 6, 5, 22, 0, tzinfo=UTC)

# Hand-encoded close instants per index (the independent oracle for the per-index as_of). The
# values themselves do not need to match the real exchange — the fake resolver is authoritative
# here; what matters is each index gets its own distinct instant and the runner injects it.
SX5E_CLOSE = datetime(2026, 6, 5, 15, 30, tzinfo=UTC)  # Eurex 17:30 CEST
SPX_CLOSE = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)  # NYSE 16:00 EDT


# --------------------------------------------------------------------------- #
# Hand-written registry + a fake calendar resolver (the oracle).               #
# --------------------------------------------------------------------------- #
def _registry(*, sx5e_enabled: bool = True, spx_enabled: bool = True) -> IndexRegistry:
    """A two-index registry (SX5E on XEUR, SPX on XNYS) with toggleable enabled flags.

    XEUR and XNYS are real exchange_calendars codes, so the block passes the parser's
    unknown-calendar-code rejection. The enabled flags drive the enabled-filter test.
    """
    return parse_index_registry(
        {
            "SX5E": {
                "name": "EURO STOXX 50",
                "calendar": "XEUR",
                "currency": "EUR",
                "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
                "enabled": sx5e_enabled,
            },
            "SPX": {
                "name": "S&P 500",
                "calendar": "XNYS",
                "currency": "USD",
                "ibkr": {"conid": 2, "secType": "IND", "exchange": "CBOE"},
                "enabled": spx_enabled,
            },
        }
    )


class _FakeResolver:
    """A deterministic stand-in for the 1J ``CalendarResolver`` (no library, no wall clock).

    ``holidays`` is the set of (index, date) pairs the index treats as a non-session; every
    other date is a session. ``closes`` maps an index symbol to its session-close instant.
    This is the independent oracle: the test states the calendar facts here and asserts the
    runner consumes exactly them.
    """

    def __init__(
        self,
        closes: dict[str, datetime],
        holidays: set[tuple[str, date]] | None = None,
    ) -> None:
        self._closes = closes
        self._holidays = holidays or set()

    def is_session(self, index: str, on_date: date) -> bool:
        return (index, on_date) not in self._holidays

    def session_close(self, index: str, on_date: date) -> datetime:
        if (index, on_date) in self._holidays:
            raise AssertionError(f"session_close called on a holiday: {index} {on_date}")
        return self._closes[index].replace(
            year=on_date.year, month=on_date.month, day=on_date.day
        )

    def next_session_open(self, index: str, on_date: date) -> datetime:
        if (index, on_date) in self._holidays:
            raise AssertionError(f"next_session_open called on a holiday: {index} {on_date}")
        # The next date that is a session for this index (skip the index's holidays); the open
        # time-of-day reuses the close instant's clock — the oracle only needs a deterministic
        # instant strictly after the close, which this is (next day ≥ close day).
        nxt = on_date + timedelta(days=1)
        while (index, nxt) in self._holidays:
            nxt += timedelta(days=1)
        return self._closes[index].replace(year=nxt.year, month=nxt.month, day=nxt.day)


def _config() -> PlatformConfig:
    """A minimal, valid economic config (mirrors test_orchestration::_config)."""
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


# --------------------------------------------------------------------------- #
# A recording stages builder: clean no-op stages, with optional one-stage raise.#
# --------------------------------------------------------------------------- #
class _StagesRecorder:
    """Builds clean no-op ``EodStages`` and records the args the runner passed in.

    Each stage returns a tiny stand-in result (the pipeline records a clean completion in the
    ledger). When ``explode`` names a stage, that stage raises to simulate a mid-run kill. The
    builder captures the store/config/hashes/clock/correlation_id/fired-set the runner handed
    it, so a test asserts the runner built the wiring with the resolved trade date, the bound
    correlation id, the injected clock, and a full five-stage ``EodStages``.
    """

    def __init__(self, *, explode: str | None = None) -> None:
        self.explode = explode
        self.calls: list[dict[str, object]] = []
        self.built_stages: list[EodStages] = []

    def __call__(
        self,
        store: ParquetStore,
        config: PlatformConfig,
        hashes: dict[str, str],
        clock: ManualClock,
        correlation_id: str,
        fired: tuple[FiredIndex, ...],
    ) -> EodStages:
        self.calls.append(
            {
                "store": store,
                "config": config,
                "hashes": dict(hashes),
                "clock": clock,
                "correlation_id": correlation_id,
                "fired": tuple(fired),
            }
        )

        def _stage(name: str):  # type: ignore[no-untyped-def]
            def _run():  # type: ignore[no-untyped-def]
                if self.explode == name:
                    raise RuntimeError(f"simulated kill mid-{name}")
                return _StageResult(name=name, correlation_id=correlation_id)

            return _run

        stages = EodStages(
            universe_refresh=_stage("universe_refresh"),
            collection=_stage("collection"),
            analytics=_stage("analytics"),
            reconciliation=_CleanRecon(self.explode == "reconciliation"),
            qc=_CleanQc(self.explode == "qc"),
        )
        self.built_stages.append(stages)
        return stages


class _StageResult:
    """A stand-in stage result for the stages the pipeline does not introspect."""

    def __init__(self, *, name: str, correlation_id: str) -> None:
        self.name = name
        self.correlation_id = correlation_id


class _CleanRecon:
    """Reconciliation stage callable: clean (is_clean True) unless it should explode."""

    def __init__(self, explode: bool) -> None:
        self.explode = explode

    def __call__(self):  # type: ignore[no-untyped-def]
        if self.explode:
            raise RuntimeError("simulated kill mid-reconciliation")
        return _ReconResult()


class _ReconResult:
    is_clean = True


class _CleanQc:
    """QC stage callable: a passing report unless it should explode."""

    def __init__(self, explode: bool) -> None:
        self.explode = explode

    def __call__(self):  # type: ignore[no-untyped-def]
        if self.explode:
            raise RuntimeError("simulated kill mid-qc")
        return _QcResult()


class _QcReport:
    overall_status = "pass"


class _QcResult:
    report = _QcReport()
    escalation = "none"


def _deps(
    tmp_path: Path,
    *,
    registry: IndexRegistry | None = None,
    resolver: _FakeResolver | None = None,
    builder: _StagesRecorder | None = None,
    clock: ManualClock | None = None,
) -> tuple[RunnerDeps, _StagesRecorder]:
    """Assemble a fully-faked ``RunnerDeps`` (real store + run registry, fake resolver/wiring)."""
    rec = builder or _StagesRecorder()
    deps = RunnerDeps(
        store=ParquetStore(tmp_path / "data"),
        config=_config(),
        registry=registry or _registry(),
        resolver=resolver
        or _FakeResolver({"SX5E": SX5E_CLOSE, "SPX": SPX_CLOSE}),
        run_repository=RunRegistry(tmp_path / "runs"),
        stages_builder=rec,
        clock=clock or ManualClock(start=CLOCK_NOW),
        code_identity="deadbeef",
        environment="test",
    )
    return deps, rec


def _store_root(deps: RunnerDeps) -> Path:
    return Path(deps.store.root)


# =========================================================================== #
# 1. main() builds the full wiring and invokes run_end_of_day                  #
# =========================================================================== #
def test_eod_run_builds_and_invokes(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path)
    rc = main(["--index", "SX5E"], deps=deps)
    assert rc == 0

    # Exactly one fire built exactly one EodStages with all five stage callables.
    assert len(rec.calls) == 1
    stages = rec.built_stages[0]
    assert isinstance(stages, EodStages)
    for attr in ("universe_refresh", "collection", "analytics", "reconciliation", "qc"):
        assert callable(getattr(stages, attr))

    call = rec.calls[0]
    # The injected clock is the one threaded in — no wall clock substituted.
    assert call["clock"] is deps.clock
    # A bound correlation id (a UUID hex; non-empty, single value).
    corr = call["correlation_id"]
    assert isinstance(corr, str) and len(corr) >= 16

    # The ledger recorded the resolved trade date (the clock's market day) under that id.
    root = _store_root(deps)
    runs = read_stage_runs(root)
    assert runs, "the pipeline must have recorded stage completions"
    assert {r.trade_date for r in runs} == {CLOCK_DAY}
    assert {r.run_id for r in runs} == {corr}
    # The whole day is clean and gap-free.
    assert backlog_stages(root, CLOCK_DAY) == []
    assert last_healthy_trade_date(root) == CLOCK_DAY


def test_eod_run_no_wall_clock_read(tmp_path: Path) -> None:
    """The default trade date is the INJECTED clock's day, never the real wall-clock today.

    A wall clock cannot be monkeypatched (``datetime.date`` is immutable), so the discipline is
    proven structurally: the ManualClock is pinned to a day far from any plausible real "today",
    and the resolved default trade date equals the clock's day. If the runner read a real clock,
    this would resolve to the actual current date and the assertion would fail.
    """
    far_day = date(2001, 1, 9)
    far_clock = ManualClock(start=datetime(2001, 1, 9, 22, 0, tzinfo=UTC))
    deps, _ = _deps(
        tmp_path,
        resolver=_FakeResolver({"SX5E": SX5E_CLOSE, "SPX": SPX_CLOSE}),
        clock=far_clock,
    )
    plan = plan_fire(deps, trade_date=None, calendar=None, index="SX5E")
    assert plan.trade_date == far_day  # from the injected ManualClock, not the real today()
    assert far_day != date.today()  # the pin is genuinely not the real wall-clock day


# =========================================================================== #
# 2. Idempotent re-fire: second fire skips clean stages, no duplicate output    #
# =========================================================================== #
def test_eod_run_idempotent_refire(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)

    # First fire: every stage runs and records cleanly (oracle: run_state.completed_stages).
    assert main(["--index", "SX5E"], deps=deps) == 0
    clean_after_first = completed_stages(root, CLOCK_DAY)
    runs_after_first = read_stage_runs(root)
    # All five canonical stages are clean and there is no backlog.
    assert backlog_stages(root, CLOCK_DAY) == []

    # Second fire for the same date, against the SAME store/runs root: every already-clean stage
    # is skipped.
    rec2 = _StagesRecorder()
    deps2 = RunnerDeps(
        store=deps.store,
        config=deps.config,
        registry=deps.registry,
        resolver=deps.resolver,
        run_repository=deps.run_repository,
        stages_builder=rec2,
        clock=ManualClock(start=CLOCK_NOW),
        code_identity="deadbeef",
        environment="test",
    )
    result = run_fire(deps2, trade_date=CLOCK_DAY, index="SX5E")
    assert result is not None
    # Overwrite-by-re-run (ADR 0032 refined): the second fire re-runs every clean stage rather than
    # skipping it. The stage set is unchanged, but fresh ledger rows record the re-run.
    assert set(result.ran) == clean_after_first
    assert completed_stages(root, CLOCK_DAY) == clean_after_first
    assert len(read_stage_runs(root)) > len(runs_after_first)


def test_refire_clean_date_reruns_overwriting(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    assert main(["--index", "SX5E"], deps=deps) == 0
    n = len(read_stage_runs(root))
    # A second fire for the already-clean date re-runs every stage (overwrite), not a no-op skip.
    res = run_fire(deps, trade_date=CLOCK_DAY, index="SX5E")
    assert res is not None and set(res.ran) == set(EOD_STAGES)
    assert len(read_stage_runs(root)) > n


# =========================================================================== #
# 3. Missed-day catch-up: D-2, then D (skip D-1), then D-1 → gap-free          #
# =========================================================================== #
def test_eod_run_missed_day_catchup(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    d_2 = date(2026, 6, 3)
    d_1 = date(2026, 6, 4)
    d = date(2026, 6, 5)

    # Fire D-2 and D — D-1 is the missed day the box was down for.
    assert run_fire(deps, trade_date=d_2, index="SX5E") is not None
    assert run_fire(deps, trade_date=d, index="SX5E") is not None
    # Before the catch-up, the last *healthy* trade date is D (D-2 and D are clean), and D-1 has
    # a full backlog because nothing ran for it (oracle: run_state functions).
    assert last_healthy_trade_date(root) == d
    assert backlog_stages(root, d_1) == [
        "universe_refresh",
        "collection",
        "analytics",
        "reconciliation",
        "qc",
    ]

    # The Persistent=true catch-up fire reconstructs the missed day.
    assert run_fire(deps, trade_date=d_1, index="SX5E") is not None
    # No gap remains: every one of D-2, D-1, D is fully clean.
    for day in (d_2, d_1, d):
        assert backlog_stages(root, day) == []
    assert last_healthy_trade_date(root) == d


# =========================================================================== #
# 4. Mid-run kill + restart converges; failed stage not recorded               #
# =========================================================================== #
def test_eod_run_midrun_kill_restart_converges(tmp_path: Path) -> None:
    # First fire: the analytics stage raises (a documented mid-run kill).
    exploding = _StagesRecorder(explode="analytics")
    deps, _ = _deps(tmp_path, builder=exploding)
    root = _store_root(deps)

    rc = main(["--index", "SX5E"], deps=deps)
    assert rc == 1  # a stage failure exits non-zero so Restart=on-failure engages

    # The failed stage (and everything after) is NOT recorded — it is backlog on restart.
    done = completed_stages(root, CLOCK_DAY)
    assert "universe_refresh" in done
    assert "collection" in done
    assert "analytics" not in done
    assert backlog_stages(root, CLOCK_DAY) == ["analytics", "reconciliation", "qc"]
    assert last_healthy_trade_date(root) is None

    # The failed fire still recorded a per-run manifest, marked failed (reproducible failure).
    failed = _read_manifests(deps.run_repository)
    assert any(m.status == RunStatus.FAILED for m in failed)

    # Restart with a clean stage set against the SAME roots — overwrite re-runs every stage (not
    # only the failed tail); the day converges gap-free.
    healthy = _StagesRecorder()
    deps2 = RunnerDeps(
        store=deps.store,
        config=deps.config,
        registry=deps.registry,
        resolver=deps.resolver,
        run_repository=deps.run_repository,
        stages_builder=healthy,
        clock=ManualClock(start=CLOCK_NOW),
        code_identity="deadbeef",
        environment="test",
    )
    result = run_fire(deps2, trade_date=CLOCK_DAY, index="SX5E")
    assert result is not None
    assert set(result.ran) == set(EOD_STAGES)
    # Convergence: the day is now gap-free.
    assert backlog_stages(root, CLOCK_DAY) == []
    assert last_healthy_trade_date(root) == CLOCK_DAY


# =========================================================================== #
# 5. Failure exit code (subprocess-level, so Restart=on-failure truly fires)   #
# =========================================================================== #
def test_eod_run_failure_exit_code(tmp_path: Path) -> None:
    """A raising stage → main() returns non-zero; the shim turns it into a process exit."""
    exploding = _StagesRecorder(explode="collection")
    deps, _ = _deps(tmp_path, builder=exploding)
    assert main(["--index", "SX5E"], deps=deps) == 1


def test_eod_run_script_shim_exits_nonzero_in_a_real_process(tmp_path: Path) -> None:
    """The committed scripts/eod_run.py exits non-zero on a failing fire (real subprocess).

    Drives the shim through a tiny harness that injects a deps whose collection stage raises,
    proving the SystemExit(main()) path actually yields a non-zero process return code — the
    exact precondition Restart=on-failure / OnFailure= depend on.
    """
    harness = tmp_path / "harness.py"
    harness.write_text(
        "import sys\n"
        "from datetime import UTC, datetime, date\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'packages' / 'infra' / 'tests')!r})\n"
        "from algotrading.infra.orchestration.eod_runner import main\n"
        "import test_eod_run as t\n"
        f"tmp = {str(tmp_path / 'sub')!r}\n"
        "from pathlib import Path\n"
        "rec = t._StagesRecorder(explode='collection')\n"
        "deps, _ = t._deps(Path(tmp), builder=rec)\n"
        "raise SystemExit(main(['--index', 'SX5E'], deps=deps))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(harness)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, proc.stderr


# =========================================================================== #
# 6. Registry-driven index set: enabled-only, per-calendar, per-index as_of     #
# =========================================================================== #
def test_eod_run_registry_driven_index_set(tmp_path: Path) -> None:
    # SPX disabled in the registry; a default fire (all calendars) captures only SX5E.
    registry = _registry(sx5e_enabled=True, spx_enabled=False)
    deps, rec = _deps(tmp_path, registry=registry)
    assert main([], deps=deps) == 0

    fired = rec.calls[0]["fired"]
    assert isinstance(fired, tuple)
    fired_symbols = [f.entry.symbol for f in fired]
    # The disabled index is never captured; only the enabled one is.
    assert fired_symbols == ["SX5E"]
    # The per-index as_of equals the resolver's session_close for that index (the oracle).
    by_symbol = {f.entry.symbol: f.as_of for f in fired}
    assert by_symbol["SX5E"] == SX5E_CLOSE.replace(
        year=CLOCK_DAY.year, month=CLOCK_DAY.month, day=CLOCK_DAY.day
    )


def test_eod_run_calendar_scope_excludes_other_calendars(tmp_path: Path) -> None:
    # Both enabled; a --calendar XEUR fire captures only the Eurex index, not the NYSE one.
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main(["--calendar", "XEUR"], deps=deps) == 0
    fired_symbols = [f.entry.symbol for f in rec.calls[0]["fired"]]
    assert fired_symbols == ["SX5E"]  # SPX (XNYS) excluded by the calendar scope


def test_eod_run_per_index_as_of_differs_by_exchange(tmp_path: Path) -> None:
    # A whole-universe fire captures both, each at its own (different) close instant.
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main([], deps=deps) == 0
    by_symbol = {f.entry.symbol: f.as_of for f in rec.calls[0]["fired"]}
    assert set(by_symbol) == {"SX5E", "SPX"}
    # Eurex close ≠ NYSE close — per-index, not one global close.
    assert by_symbol["SX5E"] != by_symbol["SPX"]


# =========================================================================== #
# 7. Holiday: a non-session date is a clean no-op (no failure, no garbage set)  #
# =========================================================================== #
def test_eod_run_skips_exchange_holiday(tmp_path: Path) -> None:
    holiday = date(2026, 6, 4)
    resolver = _FakeResolver(
        {"SX5E": SX5E_CLOSE, "SPX": SPX_CLOSE},
        holidays={("SX5E", holiday)},  # SX5E closed; this fire scopes to SX5E only
    )
    deps, rec = _deps(tmp_path, resolver=resolver)
    root = _store_root(deps)

    rc = main(["--index", "SX5E", "--trade-date", holiday.isoformat()], deps=deps)
    # Clean no-op: zero exit, the pipeline was never built, the ledger is untouched.
    assert rc == 0
    assert rec.calls == []
    assert read_stage_runs(root) == []
    # The no-op still recorded a clean manifest (the fire happened, captured nothing).
    manifests = _read_manifests(deps.run_repository)
    assert len(manifests) == 1 and manifests[0].status == RunStatus.OK

    # Contrast: a real session day on the same index DOES build and capture.
    deps2, rec2 = _deps(tmp_path, resolver=resolver)
    deps2 = RunnerDeps(
        store=deps.store, config=deps.config, registry=deps.registry, resolver=resolver,
        run_repository=deps.run_repository, stages_builder=rec2,
        clock=ManualClock(start=CLOCK_NOW), code_identity="deadbeef", environment="test",
    )
    assert main(["--index", "SX5E", "--trade-date", CLOCK_DAY.isoformat()], deps=deps2) == 0
    assert len(rec2.calls) == 1
    assert backlog_stages(root, CLOCK_DAY) == []


# =========================================================================== #
# 8. Edge cases (TESTING.md floor)                                             #
# =========================================================================== #
def test_first_ever_run_against_empty_ledger(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    assert read_stage_runs(root) == []  # empty ledger / first-ever run
    assert main(["--index", "SX5E"], deps=deps) == 0
    assert last_healthy_trade_date(root) == CLOCK_DAY


def test_future_trade_date_is_rejected(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path)
    future = date(2026, 6, 6)  # one day after the clock's market day
    with pytest.raises(EodRunError, match="future"):
        plan_fire(deps, trade_date=future, calendar=None, index="SX5E")
    # main turns the labeled error into a distinct non-zero exit (2), and never builds wiring.
    assert main(["--index", "SX5E", "--trade-date", future.isoformat()], deps=deps) == 2
    assert rec.calls == []


def test_empty_enabled_set_for_the_fired_calendar_is_a_clean_noop(tmp_path: Path) -> None:
    # No enabled index on XNYS (SPX disabled): a --calendar XNYS fire is a clean no-op.
    registry = _registry(sx5e_enabled=True, spx_enabled=False)
    deps, rec = _deps(tmp_path, registry=registry)
    root = _store_root(deps)
    assert main(["--calendar", "XNYS"], deps=deps) == 0
    assert rec.calls == []  # nothing captured
    assert read_stage_runs(root) == []  # ledger untouched, not a crash


def test_unknown_calendar_scope_is_a_clean_noop(tmp_path: Path) -> None:
    # A --calendar code no enabled index uses is a harmless empty fire, not an error.
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main(["--calendar", "XLON"], deps=deps) == 0
    assert rec.calls == []


# =========================================================================== #
# 9. Per-run manifest: frozen config + hashes + code identity, validates        #
# =========================================================================== #
def test_each_fire_freezes_a_reproducible_manifest(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    assert main(["--index", "SX5E"], deps=deps) == 0
    manifests = _read_manifests(deps.run_repository)
    assert len(manifests) == 1
    m = manifests[0]
    # Code identity (commit SHA + dirty flag) is the injected value, recorded verbatim.
    assert m.code_identity == "deadbeef"
    # The per-bundle config hashes match a fresh recompute from the same config (the oracle).
    assert dict(m.config_hashes) == config_hashes(deps.config)
    # The frozen snapshot validates against its hashes (recompute-and-reject gate, ADR 0028).
    validate_manifest(m)
    # The manifest's correlation id is the fire's bound trace id, shared with the ledger.
    runs = read_stage_runs(_store_root(deps))
    assert {r.run_id for r in runs} == {m.correlation_id}


def _read_manifests(repo: RunRegistry):  # type: ignore[no-untyped-def]
    return [r.manifest for r in repo.list_runs(EOD_JOB_NAME)]


# =========================================================================== #
# 10. Artifact sanity: the committed unit files carry the ADR 0032 obligations  #
# =========================================================================== #
def test_eod_capture_units_carry_the_adr_0032_obligations() -> None:
    service = (CONNECTIVITY / "eod-capture@.service").read_text(encoding="utf-8")
    alert = (CONNECTIVITY / "eod-capture-alert.service").read_text(encoding="utf-8")
    timers = sorted(CONNECTIVITY.glob("eod-capture*.timer"))
    assert timers, "at least one eod-capture timer unit must be committed"

    # The oneshot service: Type=oneshot, retry, and the OnFailure route to the alert unit.
    assert "Type=oneshot" in service
    assert "Restart=on-failure" in service
    assert "RestartSec=" in service
    assert "OnFailure=eod-capture-alert.service" in service
    assert "scripts/eod_run.py" in service  # it invokes the runner

    # The alert unit is itself a oneshot (the failure notification target).
    assert "Type=oneshot" in alert

    # Each timer: Persistent=true (missed-run catch-up) and an explicit-timezone OnCalendar.
    for timer in timers:
        text = timer.read_text(encoding="utf-8")
        assert "Persistent=true" in text, f"{timer.name} must set Persistent=true"
        oncal = [
            ln for ln in text.splitlines() if ln.strip().startswith("OnCalendar=")
        ]
        assert oncal, f"{timer.name} must carry an OnCalendar="
        # The close timezone must be stated explicitly (Eurex Europe/Berlin, NYSE America/...).
        assert any(
            "Europe/" in ln or "America/" in ln for ln in oncal
        ), f"{timer.name} OnCalendar must state the exchange timezone explicitly"
