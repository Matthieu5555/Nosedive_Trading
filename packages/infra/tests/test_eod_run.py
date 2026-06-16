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
CONNECTIVITY = REPO_ROOT / "scripts" / "systemd"

CLOCK_DAY = date(2026, 6, 5)
CLOCK_NOW = datetime(2026, 6, 5, 22, 0, tzinfo=UTC)

SX5E_CLOSE = datetime(2026, 6, 5, 15, 30, tzinfo=UTC)
SPX_CLOSE = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)


def _registry(*, sx5e_enabled: bool = True, spx_enabled: bool = True) -> IndexRegistry:
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
        nxt = on_date + timedelta(days=1)
        while (index, nxt) in self._holidays:
            nxt += timedelta(days=1)
        return self._closes[index].replace(year=nxt.year, month=nxt.month, day=nxt.day)


def _config() -> PlatformConfig:
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


class _StagesRecorder:

    def __init__(self, *, explode: str | None = None, qc_escalation: str = "none") -> None:
        self.explode = explode
        self.qc_escalation = qc_escalation
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
            qc=_CleanQc(self.explode == "qc", escalation=self.qc_escalation),
        )
        self.built_stages.append(stages)
        return stages


class _StageResult:

    def __init__(self, *, name: str, correlation_id: str) -> None:
        self.name = name
        self.correlation_id = correlation_id


class _CleanRecon:

    def __init__(self, explode: bool) -> None:
        self.explode = explode

    def __call__(self):  # type: ignore[no-untyped-def]
        if self.explode:
            raise RuntimeError("simulated kill mid-reconciliation")
        return _ReconResult()


class _ReconResult:
    is_clean = True


class _CleanQc:

    def __init__(self, explode: bool, *, escalation: str = "none") -> None:
        self.explode = explode
        self.escalation = escalation

    def __call__(self):  # type: ignore[no-untyped-def]
        if self.explode:
            raise RuntimeError("simulated kill mid-qc")
        return _QcResult(self.escalation)


class _QcReport:
    def __init__(self, escalation: str) -> None:
        # A page rides a non-passing report; none/notice are a clean pass for this stand-in.
        self.overall_status = "fail" if escalation == "page" else "pass"
        self.fail_count = 1 if escalation == "page" else 0


class _QcResult:
    def __init__(self, escalation: str = "none") -> None:
        self.report = _QcReport(escalation)
        self.escalation = escalation


def _deps(
    tmp_path: Path,
    *,
    registry: IndexRegistry | None = None,
    resolver: _FakeResolver | None = None,
    builder: _StagesRecorder | None = None,
    clock: ManualClock | None = None,
) -> tuple[RunnerDeps, _StagesRecorder]:
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


def test_eod_run_builds_and_invokes(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path)
    rc = main(["--index", "SX5E"], deps=deps)
    assert rc == 0

    assert len(rec.calls) == 1
    stages = rec.built_stages[0]
    assert isinstance(stages, EodStages)
    for attr in ("universe_refresh", "collection", "analytics", "reconciliation", "qc"):
        assert callable(getattr(stages, attr))

    call = rec.calls[0]
    assert call["clock"] is deps.clock
    corr = call["correlation_id"]
    assert isinstance(corr, str) and len(corr) >= 16

    root = _store_root(deps)
    runs = read_stage_runs(root)
    assert runs, "the pipeline must have recorded stage completions"
    assert {r.trade_date for r in runs} == {CLOCK_DAY}
    assert {r.run_id for r in runs} == {corr}
    assert backlog_stages(root, CLOCK_DAY) == []
    assert last_healthy_trade_date(root) == CLOCK_DAY


def test_eod_run_no_wall_clock_read(tmp_path: Path) -> None:
    far_day = date(2001, 1, 9)
    far_clock = ManualClock(start=datetime(2001, 1, 9, 22, 0, tzinfo=UTC))
    deps, _ = _deps(
        tmp_path,
        resolver=_FakeResolver({"SX5E": SX5E_CLOSE, "SPX": SPX_CLOSE}),
        clock=far_clock,
    )
    plan = plan_fire(deps, trade_date=None, calendar=None, index="SX5E")
    assert plan.trade_date == far_day
    assert far_day != date.today()


def test_eod_run_idempotent_refire(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)

    assert main(["--index", "SX5E"], deps=deps) == 0
    clean_after_first = completed_stages(root, CLOCK_DAY)
    runs_after_first = read_stage_runs(root)
    assert backlog_stages(root, CLOCK_DAY) == []

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
    assert set(result.ran) == clean_after_first
    assert completed_stages(root, CLOCK_DAY) == clean_after_first
    assert len(read_stage_runs(root)) > len(runs_after_first)


def test_refire_clean_date_reruns_overwriting(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    assert main(["--index", "SX5E"], deps=deps) == 0
    n = len(read_stage_runs(root))
    res = run_fire(deps, trade_date=CLOCK_DAY, index="SX5E")
    assert res is not None and set(res.ran) == set(EOD_STAGES)
    assert len(read_stage_runs(root)) > n


def test_eod_run_missed_day_catchup(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    d_2 = date(2026, 6, 3)
    d_1 = date(2026, 6, 4)
    d = date(2026, 6, 5)

    assert run_fire(deps, trade_date=d_2, index="SX5E") is not None
    assert run_fire(deps, trade_date=d, index="SX5E") is not None
    assert last_healthy_trade_date(root) == d
    assert backlog_stages(root, d_1) == [
        "universe_refresh",
        "collection",
        "analytics",
        "reconciliation",
        "qc",
    ]

    assert run_fire(deps, trade_date=d_1, index="SX5E") is not None
    for day in (d_2, d_1, d):
        assert backlog_stages(root, day) == []
    assert last_healthy_trade_date(root) == d


def test_eod_run_midrun_kill_restart_converges(tmp_path: Path) -> None:
    exploding = _StagesRecorder(explode="analytics")
    deps, _ = _deps(tmp_path, builder=exploding)
    root = _store_root(deps)

    rc = main(["--index", "SX5E"], deps=deps)
    assert rc == 1

    done = completed_stages(root, CLOCK_DAY)
    assert "universe_refresh" in done
    assert "collection" in done
    assert "analytics" not in done
    assert backlog_stages(root, CLOCK_DAY) == ["analytics", "reconciliation", "qc"]
    assert last_healthy_trade_date(root) is None

    failed = _read_manifests(deps.run_repository)
    assert any(m.status == RunStatus.FAILED for m in failed)

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
    assert backlog_stages(root, CLOCK_DAY) == []
    assert last_healthy_trade_date(root) == CLOCK_DAY


def test_eod_run_failure_exit_code(tmp_path: Path) -> None:
    exploding = _StagesRecorder(explode="collection")
    deps, _ = _deps(tmp_path, builder=exploding)
    assert main(["--index", "SX5E"], deps=deps) == 1


def test_eod_run_qc_page_escalation_exits_nonzero_and_records_failed(tmp_path: Path) -> None:
    """A critical (page) QC escalation fails the fire LOUD, closing the silent-exit-0 gap.

    A close that escalates QC to ``page`` runs every stage and persists (the data is on disk) but
    is not a clean close — so ``main`` returns non-zero (Restart=on-failure / OnFailure= engage,
    the close-capture alert fires) and the per-run manifest records the fire as FAILED. The page is
    *reported* after a full pipeline, never an abort mid-run: the ledger shows the stages ran.
    Found by the 2026-06-15 ingestion audit (a QC-critical close used to exit 0 with no alert).
    """
    deps, _ = _deps(tmp_path, builder=_StagesRecorder(qc_escalation="page"))
    root = _store_root(deps)

    assert main(["--index", "SX5E"], deps=deps) == 1

    # The pipeline ran to completion before the page was reported: the non-QC stages are recorded
    # clean (QC itself commits as a non-pass, so it is correctly absent from the clean set).
    done = completed_stages(root, CLOCK_DAY)
    assert {"universe_refresh", "collection", "analytics", "reconciliation"} <= set(done)
    # The manifest marks the page as a FAILED fire — reproducible and auditable, not a silent OK.
    assert any(m.status == RunStatus.FAILED for m in _read_manifests(deps.run_repository))


def test_eod_run_clean_fire_exits_zero_with_an_ok_manifest(tmp_path: Path) -> None:
    """The control case: a clean (no escalation) fire exits 0 and records an OK manifest — so the
    page path above is the *difference*, not a blanket failure."""
    deps, _ = _deps(tmp_path, builder=_StagesRecorder())
    assert main(["--index", "SX5E"], deps=deps) == 0
    assert all(m.status == RunStatus.OK for m in _read_manifests(deps.run_repository))


def test_eod_run_script_shim_exits_nonzero_in_a_real_process(tmp_path: Path) -> None:
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


def test_eod_run_registry_driven_index_set(tmp_path: Path) -> None:
    registry = _registry(sx5e_enabled=True, spx_enabled=False)
    deps, rec = _deps(tmp_path, registry=registry)
    assert main([], deps=deps) == 0

    fired = rec.calls[0]["fired"]
    assert isinstance(fired, tuple)
    fired_symbols = [f.entry.symbol for f in fired]
    assert fired_symbols == ["SX5E"]
    by_symbol = {f.entry.symbol: f.as_of for f in fired}
    assert by_symbol["SX5E"] == SX5E_CLOSE.replace(
        year=CLOCK_DAY.year, month=CLOCK_DAY.month, day=CLOCK_DAY.day
    )


def test_eod_run_calendar_scope_excludes_other_calendars(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main(["--calendar", "XEUR"], deps=deps) == 0
    fired_symbols = [f.entry.symbol for f in rec.calls[0]["fired"]]
    assert fired_symbols == ["SX5E"]


def test_eod_run_per_index_as_of_differs_by_exchange(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main([], deps=deps) == 0
    by_symbol = {f.entry.symbol: f.as_of for f in rec.calls[0]["fired"]}
    assert set(by_symbol) == {"SX5E", "SPX"}
    assert by_symbol["SX5E"] != by_symbol["SPX"]


def test_eod_run_skips_exchange_holiday(tmp_path: Path) -> None:
    holiday = date(2026, 6, 4)
    resolver = _FakeResolver(
        {"SX5E": SX5E_CLOSE, "SPX": SPX_CLOSE},
        holidays={("SX5E", holiday)},
    )
    deps, rec = _deps(tmp_path, resolver=resolver)
    root = _store_root(deps)

    rc = main(["--index", "SX5E", "--trade-date", holiday.isoformat()], deps=deps)
    assert rc == 0
    assert rec.calls == []
    assert read_stage_runs(root) == []
    manifests = _read_manifests(deps.run_repository)
    assert len(manifests) == 1 and manifests[0].status == RunStatus.OK

    deps2, rec2 = _deps(tmp_path, resolver=resolver)
    deps2 = RunnerDeps(
        store=deps.store, config=deps.config, registry=deps.registry, resolver=resolver,
        run_repository=deps.run_repository, stages_builder=rec2,
        clock=ManualClock(start=CLOCK_NOW), code_identity="deadbeef", environment="test",
    )
    assert main(["--index", "SX5E", "--trade-date", CLOCK_DAY.isoformat()], deps=deps2) == 0
    assert len(rec2.calls) == 1
    assert backlog_stages(root, CLOCK_DAY) == []


def test_first_ever_run_against_empty_ledger(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    root = _store_root(deps)
    assert read_stage_runs(root) == []
    assert main(["--index", "SX5E"], deps=deps) == 0
    assert last_healthy_trade_date(root) == CLOCK_DAY


def test_future_trade_date_is_rejected(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path)
    future = date(2026, 6, 6)
    with pytest.raises(EodRunError, match="future"):
        plan_fire(deps, trade_date=future, calendar=None, index="SX5E")
    assert main(["--index", "SX5E", "--trade-date", future.isoformat()], deps=deps) == 2
    assert rec.calls == []


def test_empty_enabled_set_for_the_fired_calendar_is_a_clean_noop(tmp_path: Path) -> None:
    registry = _registry(sx5e_enabled=True, spx_enabled=False)
    deps, rec = _deps(tmp_path, registry=registry)
    root = _store_root(deps)
    assert main(["--calendar", "XNYS"], deps=deps) == 0
    assert rec.calls == []
    assert read_stage_runs(root) == []


def test_unknown_calendar_scope_is_a_clean_noop(tmp_path: Path) -> None:
    deps, rec = _deps(tmp_path, registry=_registry())
    assert main(["--calendar", "XLON"], deps=deps) == 0
    assert rec.calls == []


def test_each_fire_freezes_a_reproducible_manifest(tmp_path: Path) -> None:
    deps, _ = _deps(tmp_path)
    assert main(["--index", "SX5E"], deps=deps) == 0
    manifests = _read_manifests(deps.run_repository)
    assert len(manifests) == 1
    m = manifests[0]
    assert m.code_identity == "deadbeef"
    assert dict(m.config_hashes) == config_hashes(deps.config)
    validate_manifest(m)
    runs = read_stage_runs(_store_root(deps))
    assert {r.run_id for r in runs} == {m.correlation_id}


def _read_manifests(repo: RunRegistry):  # type: ignore[no-untyped-def]
    return [r.manifest for r in repo.list_runs(EOD_JOB_NAME)]


def test_eod_capture_units_carry_the_adr_0032_obligations() -> None:
    service = (CONNECTIVITY / "eod-capture@.service").read_text(encoding="utf-8")
    alert = (CONNECTIVITY / "eod-capture-alert.service").read_text(encoding="utf-8")
    timers = sorted(CONNECTIVITY.glob("eod-capture*.timer"))
    assert timers, "at least one eod-capture timer unit must be committed"

    assert "Type=oneshot" in service
    assert "Restart=on-failure" in service
    assert "RestartSec=" in service
    assert "OnFailure=eod-capture-alert.service" in service
    assert "scripts/eod_run.py" in service

    assert "Type=oneshot" in alert

    for timer in timers:
        text = timer.read_text(encoding="utf-8")
        assert "Persistent=true" in text, f"{timer.name} must set Persistent=true"
        oncal = [
            ln for ln in text.splitlines() if ln.strip().startswith("OnCalendar=")
        ]
        assert oncal, f"{timer.name} must carry an OnCalendar="
        assert any(
            "Europe/" in ln or "America/" in ln for ln in oncal
        ), f"{timer.name} OnCalendar must state the exchange timezone explicitly"
