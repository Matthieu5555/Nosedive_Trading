"""The canonical end-of-day run sequence (Part IV.F): ordered, idempotent, restartable.

This is the one entrypoint an operator runs at end of day. It runs the five stages in
order — universe refresh, collection, incremental analytics, EOD reconciliation, QC —
and records each clean completion in the run-state ledger. Two properties matter:

* **Idempotent restart.** Before each stage it checks the ledger; a stage that already
  finished cleanly for the trade date is skipped, so a pipeline killed mid-run and
  restarted re-does only the unfinished tail. The underlying writes are already
  replace-/append-idempotent (the actor replaces derived partitions; the collector and
  master writes dedupe on key), so even a stage that *does* re-run cannot duplicate or
  corrupt outputs — the ledger skip is an optimization on top of a store that is safe
  to re-run.
* **A resolvable trace.** One ``correlation_id`` is bound for the whole run and flows
  into every stage (and into the actor's own log lines), so the collector session and
  the analytics it fed share an id and the trace resolves end to end.

The stages are injected as callables so the pipeline is testable without a broker or a
scheduler: the default wiring calls the job functions in :mod:`orchestration.jobs` and
:mod:`orchestration.qc_job`, but a test passes stages that raise to simulate a mid-run
kill and asserts the restart converges to the same store state. The **collection**
stage is the seam C1 has not yet closed (see :mod:`orchestration.jobs`); a caller
supplies whatever produces a :class:`CollectionResult` today (a fixture replay in
tests; the live-collection job once the broker→raw-event bridge lands).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import structlog
from algotrading.infra.connectivity import Clock
from algotrading.infra.storage import ParquetStore

from .jobs import (
    AnalyticsResult,
    CollectionResult,
    ReconciliationResult,
    UniverseRefreshResult,
)
from .qc_job import QcJobResult
from .run_state import (
    OUTCOME_FAILED,
    OUTCOME_OK,
    STAGE_ANALYTICS,
    STAGE_COLLECTION,
    STAGE_QC,
    STAGE_RECONCILIATION,
    STAGE_UNIVERSE_REFRESH,
    StageRun,
    completed_stages,
    record_stage,
)
from .storage_root import store_root

_LOGGER = structlog.get_logger("orchestration")


@dataclass(frozen=True, slots=True)
class EodResult:
    """What one end-of-day run produced, stage by stage, plus which stages ran.

    A stage that was skipped (already clean from an earlier attempt) has a ``None``
    result and its name in ``skipped``; a stage that ran this attempt has its result
    populated and its name in ``ran``. ``escalation`` is the QC escalation level if QC
    ran. This is the handle a test asserts restart-convergence on.
    """

    trade_date: date
    correlation_id: str
    ran: tuple[str, ...]
    skipped: tuple[str, ...]
    universe: UniverseRefreshResult | None = None
    collection: CollectionResult | None = None
    analytics: AnalyticsResult | None = None
    reconciliation: ReconciliationResult | None = None
    qc: QcJobResult | None = None
    escalation: str | None = None


@dataclass(frozen=True, slots=True)
class EodStages:
    """The five stage callables, injected so the pipeline runs without a broker.

    Each callable takes nothing and returns its job result; the default wiring (built
    by the caller, e.g. a runbook script) closes over the store, config, clock, and the
    bound ``correlation_id``. A test passes a stage that raises to simulate a kill.
    """

    universe_refresh: Callable[[], UniverseRefreshResult]
    collection: Callable[[], CollectionResult]
    analytics: Callable[[], AnalyticsResult]
    reconciliation: Callable[[], ReconciliationResult]
    qc: Callable[[], QcJobResult]


def run_end_of_day(
    store: ParquetStore,
    *,
    trade_date: date,
    correlation_id: str,
    clock: Clock,
    stages: EodStages,
) -> EodResult:
    """Run (or resume) the canonical end-of-day sequence for one trade date.

    Runs the five stages in order, skipping any that already finished cleanly for the
    date (read from the run-state ledger under the store root), and records each clean
    completion so a later restart resumes from where this attempt got to. Every stage is
    logged with the shared ``correlation_id``. A stage callable that raises propagates —
    the run stops there, its completion is *not* recorded (so it is backlog on restart),
    and the store is left consistent because the underlying writes are atomic and
    idempotent. ``clock`` supplies the injected timestamps the ledger records; nothing
    here reads a wall clock. Returns the per-stage results and which stages ran vs were
    skipped.
    """
    root = store_root(store)
    already_done = completed_stages(root, trade_date)
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="eod",
        trade_date=trade_date.isoformat(),
    )
    log.info("orchestration.eod.start", already_done=sorted(already_done))

    ran: list[str] = []
    skipped: list[str] = []
    universe: UniverseRefreshResult | None = None
    collection: CollectionResult | None = None
    analytics: AnalyticsResult | None = None
    reconciliation: ReconciliationResult | None = None
    qc: QcJobResult | None = None
    escalation: str | None = None

    def _commit(stage_name: str, outcome: str) -> None:
        record_stage(
            root,
            StageRun(
                trade_date=trade_date,
                stage=stage_name,
                outcome=outcome,
                run_id=correlation_id,
                recorded_ts=clock.now(),
            ),
        )
        ran.append(stage_name)
        log.info("orchestration.eod.stage.done", stage=stage_name, outcome=outcome)

    # Overwrite-by-re-run (ADR 0032 refined): a re-fire RE-RUNS every stage rather than skipping
    # the ones the ledger already recorded. Every stage write is idempotent — derived tables are
    # replace-by-(trade_date, underlying), the raw layer is append-dedup on the content-addressed
    # event_id — so re-running a given fired index set converges to the same store state, while a
    # *different* calendar's fire (e.g. @XNYS after @XEUR the same day) now captures its own index
    # instead of being skipped by the other calendar's ledger rows. It also self-heals an intraday
    # dry-run that touched the slot: the real close overwrites it (no manual purge). The ledger
    # stays for observability (the ``already_done`` log above), never a gate; ``skipped`` is empty.
    log.info("orchestration.eod.stage.start", stage=STAGE_UNIVERSE_REFRESH)
    universe = stages.universe_refresh()
    _commit(STAGE_UNIVERSE_REFRESH, OUTCOME_OK)

    log.info("orchestration.eod.stage.start", stage=STAGE_COLLECTION)
    collection = stages.collection()
    _commit(STAGE_COLLECTION, OUTCOME_OK)

    log.info("orchestration.eod.stage.start", stage=STAGE_ANALYTICS)
    analytics = stages.analytics()
    _commit(STAGE_ANALYTICS, OUTCOME_OK)

    log.info("orchestration.eod.stage.start", stage=STAGE_RECONCILIATION)
    reconciliation = stages.reconciliation()
    _commit(
        STAGE_RECONCILIATION,
        OUTCOME_OK if reconciliation.is_clean else OUTCOME_FAILED,
    )

    log.info("orchestration.eod.stage.start", stage=STAGE_QC)
    qc = stages.qc()
    escalation = qc.escalation
    _commit(
        STAGE_QC,
        OUTCOME_OK if qc.report.overall_status == "pass" else OUTCOME_FAILED,
    )

    log.info("orchestration.eod.done", ran=ran, skipped=skipped, escalation=escalation)
    return EodResult(
        trade_date=trade_date,
        correlation_id=correlation_id,
        ran=tuple(ran),
        skipped=tuple(skipped),
        universe=universe,
        collection=collection,
        analytics=analytics,
        reconciliation=reconciliation,
        qc=qc,
        escalation=escalation,
    )
