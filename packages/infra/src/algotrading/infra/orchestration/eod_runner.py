"""The EOD runner: bind one fire of the daily close-capture and call ``run_end_of_day``.

This is the importable core behind ``scripts/eod_run.py`` (WS 1G, ADR 0032). The systemd
timer is the scheduler; this is the one-shot it fires. The runner does exactly four things
and nothing the ledger already owns:

* **Resolve which day, which indices.** The trade date defaults to the clock's current
  market day; ``--trade-date`` overrides it for a catch-up/backfill fire. A *future* trade
  date is rejected — never capture a session that has not closed (look-ahead). The index set
  is read from the 1J registry's :func:`enabled_indices` (never a hardcoded list) and filtered
  to the fired calendar group (``--calendar XEUR`` / ``--index SX5E``; default = all enabled).
* **Skip a non-session cleanly.** A ``--trade-date`` the calendar marks a holiday/weekend for
  *every* fired index is a clean no-op — not a failed run, not an empty set written. The
  per-index session check uses the 1J resolver, so a half-day/holiday is handled by the
  calendar, never by the timer's fixed local time. Each captured index's ``as_of`` is its own
  :meth:`CalendarResolver.session_close` — the exact close instant 1C captures at.
* **Bind one trace and run.** One ``correlation_id`` is bound for the whole fire (a UUID,
  recorded in the start log line so journald and the ledger share it), the default
  :class:`EodStages` wiring is built over the store/config/clock/correlation_id, and
  :func:`run_end_of_day` is called. Idempotency, gap-tracking, and restart-convergence stay
  entirely in the existing run-state ledger; the runner adds no dedupe of its own.
* **Freeze a per-run manifest.** Every fire records its lineage manifest — resolved config
  snapshot + per-bundle ``config_hashes`` + code identity (commit SHA + dirty flag) — in the
  :class:`RunRepository`, so a scheduled run is reproducible *from its manifest*, not merely
  traceable through the JSONL ledger.

Determinism / no wall clock: the runner takes an injected :class:`Clock` (the same discipline
the ledger and resolver hold); ``main`` never reads ``date.today()`` directly. Every external
dependency (config, registry, resolver, the stage wiring, the run repository, the code-identity
probe) is injectable through :class:`RunnerDeps` so a test drives the whole path with fakes and
no broker, no git subprocess, and no real scheduler. The collection stage is the 1C seam: until
1C closes the broker→raw-event bridge the default wiring uses a replay/fixture collection stage,
swapped to ``collect_live`` when 1C lands — the timer path is fully exercisable today.

Exit code: any stage raising propagates out of :func:`run_end_of_day`; :func:`main` records the
fire as failed in the manifest and returns a non-zero code so ``Restart=on-failure`` and
``OnFailure=`` engage. A clean fire (including a clean holiday no-op) returns ``0``.
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog
from algotrading.core.config import PlatformConfig, config_hashes, config_snapshot
from algotrading.core.config.loader import load_platform_config
from algotrading.core.manifest import Manifest
from algotrading.core.provenance import code_identity as _git_code_identity
from algotrading.core.provenance import code_version
from algotrading.infra.connectivity import Clock, SystemClock
from algotrading.infra.storage import ParquetStore, RunRecord, RunRegistry, RunStatus
from algotrading.infra.universe import (
    CalendarResolver,
    IndexEntry,
    IndexRegistry,
    enabled_indices,
    index_registry_from_config,
)

from .pipeline import EodResult, EodStages, run_end_of_day

_LOGGER = structlog.get_logger("orchestration.eod_run")

# The job name the manifest/run-registry records this fire under.
EOD_JOB_NAME = "eod_capture"
# The distribution whose installed version is stamped on the manifest (best-effort).
_INFRA_DISTRIBUTION = "algotrading-infra"


class EodRunError(Exception):
    """A labeled runner error — a future trade date, an unknown calendar, a bad scope.

    Carries a plain-language reason so a misfire fails loudly with an operator-readable
    message rather than a bare traceback or a silent wrong-day capture.
    """


@runtime_checkable
class SessionResolver(Protocol):
    """The two calendar answers the runner needs — the 1J :class:`CalendarResolver` seam.

    Typed as a Protocol (not the concrete class) so the runner depends on the *signature*,
    not on ``exchange_calendars``: a test injects a fake resolver with controlled
    holiday/close behaviour, and 1C/1G consume the same two methods the real resolver exposes.
    """

    def is_session(self, index: str, on_date: date) -> bool: ...

    def session_close(self, index: str, on_date: date) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FiredIndex:
    """One enabled index this fire captures, paired with its own session-close instant.

    ``as_of`` is :meth:`CalendarResolver.session_close` for the index on the trade date — its
    own timezone-correct close (Eurex close for SX5E, NYSE close for SPX), the exact
    look-ahead-sensitive instant 1C captures at and the value the stage wiring injects.
    """

    entry: IndexEntry
    as_of: datetime


@dataclass(frozen=True, slots=True)
class EodRunPlan:
    """What one fire resolved before running: the date, the trace id, and the fired set.

    ``fired`` is the enabled indices in the calendar group that are *in session* on the trade
    date, each with its close instant. An empty ``fired`` (every index a holiday, or an empty
    enabled set for the group) is a clean no-op — :attr:`is_noop` is then ``True`` and the
    pipeline is not run.
    """

    trade_date: date
    correlation_id: str
    fired: tuple[FiredIndex, ...]

    @property
    def is_noop(self) -> bool:
        """True when no index is in session for the fire — a clean no-op, not a failure."""
        return not self.fired


# The default-wiring builder: given the resolved store/config/clock/trace and the fired indices
# (each with its own close instant), return the five-stage :class:`EodStages`. Injected so a
# test supplies a fake wiring (including one whose stage raises) with no broker. The production
# default (:func:`default_stages_builder`) wires the close-capture collection seam (1C) and the
# existing job functions; until 1C lands the collection stage is a replay/fixture stage.
StagesBuilder = Callable[
    [ParquetStore, PlatformConfig, "Mapping[str, str]", Clock, str, Sequence[FiredIndex]],
    EodStages,
]


@dataclass(frozen=True, slots=True)
class RunnerDeps:
    """Every external dependency one fire needs, injected so the path is fully testable.

    Production defaults are resolved lazily by :func:`build_default_deps` (which reads the
    config bundles and probes git for the code identity); a test passes fakes for all of them
    so ``main`` runs with no broker, no git subprocess, and no wall clock. ``code_identity`` is
    a *value* (already resolved at the entrypoint, never deeper in compute — ADR 0028).
    """

    store: ParquetStore
    config: PlatformConfig
    registry: IndexRegistry
    resolver: SessionResolver
    run_repository: RunRegistry
    stages_builder: StagesBuilder
    clock: Clock
    code_identity: str
    environment: str = "production"


def _market_day(clock: Clock) -> date:
    """The clock's current calendar day in UTC — the default fire's trade date.

    Reads the injected clock, never a wall clock, so a deterministic caller (a test, a replay)
    pins the day. The resolver then decides per index whether that day is a session.
    """
    return clock.now().date()


def _filter_scope(
    entries: Sequence[IndexEntry], *, calendar: str | None, index: str | None
) -> tuple[IndexEntry, ...]:
    """Filter the enabled entries to the fired calendar group / single index.

    ``calendar`` scopes to one exchange-calendar code (the templated timer's group — every
    enabled index on that calendar); ``index`` scopes to a single symbol. Both ``None`` = the
    whole enabled set. A ``--calendar``/``--index`` that matches nothing yields an empty set
    (a clean no-op for that fire), not an error — an exchange with no enabled index yet is a
    legitimate, harmless fire.
    """
    selected = entries
    if calendar is not None:
        selected = tuple(e for e in selected if e.calendar == calendar)
    if index is not None:
        selected = tuple(e for e in selected if e.symbol == index)
    return tuple(selected)


def plan_fire(
    deps: RunnerDeps,
    *,
    trade_date: date | None,
    calendar: str | None,
    index: str | None,
    correlation_id: str | None = None,
) -> EodRunPlan:
    """Resolve the trade date, the trace id, and the in-session fired index set for one fire.

    The trade date defaults to the clock's current market day; an explicit ``trade_date`` in
    the *future* (after the clock's market day) is rejected with a labeled :class:`EodRunError`
    — never capture a session that has not closed. The enabled indices are read from the
    registry, filtered to the calendar group / single index, and reduced to those in session on
    the date (per the 1J resolver). Each surviving index is paired with its own
    ``session_close`` instant. A bound ``correlation_id`` (a fresh UUID unless one is supplied)
    is returned for the whole fire.
    """
    today = _market_day(deps.clock)
    resolved_date = trade_date if trade_date is not None else today
    if resolved_date > today:
        raise EodRunError(
            f"trade-date {resolved_date.isoformat()} is in the future "
            f"(clock day {today.isoformat()}); a session that has not closed is never captured"
        )

    corr = correlation_id or uuid.uuid4().hex
    scoped = _filter_scope(
        enabled_indices(deps.registry), calendar=calendar, index=index
    )
    fired: list[FiredIndex] = []
    for entry in scoped:
        if not deps.resolver.is_session(entry.symbol, resolved_date):
            _LOGGER.info(
                "orchestration.eod_run.skip_non_session",
                correlation_id=corr,
                index=entry.symbol,
                calendar=entry.calendar,
                trade_date=resolved_date.isoformat(),
            )
            continue
        fired.append(
            FiredIndex(
                entry=entry,
                as_of=deps.resolver.session_close(entry.symbol, resolved_date),
            )
        )
    return EodRunPlan(
        trade_date=resolved_date,
        correlation_id=corr,
        fired=tuple(fired),
    )


def _record_manifest(deps: RunnerDeps, plan: EodRunPlan, *, status: str) -> None:
    """Freeze and persist this fire's per-run manifest (config snapshot + hashes + code id).

    The scheduled run must be reproducible *from its manifest*, not merely traceable through the
    JSONL ledger (ADR 0028 / C7): the manifest carries the fully-resolved config snapshot, the
    per-bundle ``config_hashes``, and the code identity (commit SHA + dirty flag). Keyed by the
    fire's ``correlation_id`` so a re-fire/restart overwrites its record rather than duplicating
    it. Recorded for both a clean fire and a failed one, so a failure is reproducible too.
    """
    started = deps.clock.now()
    manifest = Manifest(
        run_id=plan.correlation_id,
        environment=deps.environment,
        code_version=code_version(_INFRA_DISTRIBUTION),
        code_identity=deps.code_identity,
        config_hashes=config_hashes(deps.config),
        config_snapshot=config_snapshot(deps.config),
        input_partitions={},
        output_partitions={
            fired.entry.symbol: plan.trade_date.isoformat() for fired in plan.fired
        },
        status=status,
        correlation_id=plan.correlation_id,
    )
    deps.run_repository.record(
        RunRecord(
            manifest=manifest,
            job=EOD_JOB_NAME,
            started_at=started,
            ended_at=deps.clock.now(),
        )
    )


def run_fire(
    deps: RunnerDeps,
    *,
    trade_date: date | None = None,
    calendar: str | None = None,
    index: str | None = None,
    correlation_id: str | None = None,
) -> EodResult | None:
    """Plan and run one fire end to end; return the ``EodResult`` (``None`` on a no-op).

    Resolves the plan (:func:`plan_fire`), and — when at least one index is in session — builds
    the default stage wiring over the fired set and calls :func:`run_end_of_day`, then freezes
    the per-run manifest. A no-op fire (every fired index a holiday, or an empty scope) records
    a clean manifest and returns ``None`` without running the pipeline. A stage raising
    propagates after a *failed* manifest is recorded (so ``main`` exits non-zero and the failure
    is reproducible). One ``correlation_id`` flows from the plan into the pipeline and the
    manifest, so journald and the ledger resolve the same trace.
    """
    plan = plan_fire(
        deps,
        trade_date=trade_date,
        calendar=calendar,
        index=index,
        correlation_id=correlation_id,
    )
    log = _LOGGER.bind(
        correlation_id=plan.correlation_id,
        job=EOD_JOB_NAME,
        trade_date=plan.trade_date.isoformat(),
    )
    if plan.is_noop:
        log.info("orchestration.eod_run.noop", reason="no index in session for this fire")
        _record_manifest(deps, plan, status=RunStatus.OK)
        return None

    log.info(
        "orchestration.eod_run.start",
        indices=[f.entry.symbol for f in plan.fired],
        as_of={f.entry.symbol: f.as_of.isoformat() for f in plan.fired},
    )
    stages = deps.stages_builder(
        deps.store,
        deps.config,
        config_hashes(deps.config),
        deps.clock,
        plan.correlation_id,
        plan.fired,
    )
    try:
        result = run_end_of_day(
            deps.store,
            trade_date=plan.trade_date,
            correlation_id=plan.correlation_id,
            clock=deps.clock,
            stages=stages,
        )
    except Exception:
        # Record the failed fire (still reproducible from its manifest) before propagating, so
        # Restart=on-failure / OnFailure= engage and the failure is auditable. Nothing swallowed.
        _record_manifest(deps, plan, status=RunStatus.FAILED)
        log.error("orchestration.eod_run.failed")
        raise
    _record_manifest(deps, plan, status=RunStatus.OK)
    log.info("orchestration.eod_run.done", ran=result.ran, skipped=result.skipped)
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eod_run",
        description="Daily close-capture: fire run_end_of_day once per market day (WS 1G).",
    )
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=None,
        help="ISO date to capture (default: the clock's current market day). "
        "A future date is rejected (no look-ahead).",
    )
    parser.add_argument(
        "--calendar",
        default=None,
        help="scope the fire to one exchange-calendar code (e.g. XEUR, XNYS). "
        "Default: all enabled indices.",
    )
    parser.add_argument(
        "--index",
        default=None,
        help="scope the fire to a single index symbol (e.g. SX5E). Default: all enabled.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    deps: RunnerDeps | None = None,
    deps_factory: Callable[[], RunnerDeps] | None = None,
) -> int:
    """Entrypoint: parse args, run one fire, return a process exit code.

    Returns ``0`` on a clean fire (including a clean holiday no-op) and a non-zero code on any
    stage failure or labeled runner error, so ``Restart=on-failure`` / ``OnFailure=`` engage.
    ``deps`` (or ``deps_factory``) is injected by a test for a fully-faked path; production
    passes neither and the deps are built by :func:`build_default_deps` from the environment.
    """
    args = _parse_args(argv)
    if deps is None:
        deps = (deps_factory or build_default_deps)()
    try:
        run_fire(
            deps,
            trade_date=args.trade_date,
            calendar=args.calendar,
            index=args.index,
        )
    except EodRunError as exc:
        _LOGGER.error("orchestration.eod_run.bad_request", reason=str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001 — surface any stage failure as a non-zero exit
        _LOGGER.error("orchestration.eod_run.error", error=str(exc))
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Production wiring — only reached when no deps are injected (live daily fire). #
# --------------------------------------------------------------------------- #

# The repo root: this file is packages/infra/src/algotrading/infra/orchestration/eod_runner.py,
# so six parents up is the repository root that holds configs/.
_REPO_ROOT = Path(__file__).resolve().parents[6]
_CONFIGS_DIR = _REPO_ROOT / "configs"


@dataclass(frozen=True, slots=True)
class _DefaultEnv:
    """The few environment values the production wiring resolves (kept tiny, injectable)."""

    data_root: Path
    runs_db: Path
    configs_dir: Path = _CONFIGS_DIR
    environment: str = "production"


def _default_env() -> _DefaultEnv:
    import os

    data_root = Path(os.environ.get("ALGOTRADING_DATA_ROOT", str(_REPO_ROOT / "data")))
    runs_db = Path(os.environ.get("ALGOTRADING_RUNS_DB", str(data_root / "runs.db")))
    return _DefaultEnv(
        data_root=data_root,
        runs_db=runs_db,
        environment=os.environ.get("ALGOTRADING_ENV", "production"),
    )


def default_stages_builder(
    store: ParquetStore,
    config: PlatformConfig,
    hashes: Mapping[str, str],
    clock: Clock,
    correlation_id: str,
    fired: Sequence[FiredIndex],
) -> EodStages:
    """The live default wiring — the 1C collection seam plus the existing EOD jobs.

    Today this is a thin stub: the collection stage is the 1C broker→raw-event seam, which is
    not yet closed in production, so the live runner is wired in tests (which inject their own
    builder over fixtures/replay) and this builder raises a clear, labeled error if reached
    before 1C lands. Swapping the collection stage to ``collect_live`` (and the analytics/
    reconciliation/QC stages to their job functions over the fired baskets) is the one edit 1C
    makes here — the runner, the manifest freeze, and the timer are all already correct.
    """
    raise EodRunError(
        "default EOD stage wiring is not yet live: the 1C broker->raw-event collection seam is "
        "not closed in production. Inject a RunnerDeps with a stages_builder (replay/fixture) "
        "to exercise the timer path, or land 1C to wire collect_live here."
    )


def build_default_deps() -> RunnerDeps:
    """Build the production :class:`RunnerDeps` from config + environment (the live fire).

    Loads the economic config bundles, parses the typed index registry, builds the calendar
    resolver, opens the run repository, resolves the code identity from git *once* at the
    entrypoint, and uses :func:`default_stages_builder`. Reached only when ``main`` is called
    with no injected deps — every test injects its own, so this never runs under the gate.
    """
    env = _default_env()
    config = load_platform_config(env.configs_dir)
    registry = index_registry_from_config(config)
    return RunnerDeps(
        store=ParquetStore(env.data_root),
        config=config,
        registry=registry,
        resolver=CalendarResolver(registry),
        run_repository=RunRegistry(env.runs_db.parent),
        stages_builder=default_stages_builder,
        clock=SystemClock(),
        code_identity=_git_code_identity(),
        environment=env.environment,
    )


__all__ = [
    "EOD_JOB_NAME",
    "EodRunError",
    "EodRunPlan",
    "FiredIndex",
    "RunnerDeps",
    "SessionResolver",
    "StagesBuilder",
    "build_default_deps",
    "default_stages_builder",
    "main",
    "plan_fire",
    "run_fire",
]
