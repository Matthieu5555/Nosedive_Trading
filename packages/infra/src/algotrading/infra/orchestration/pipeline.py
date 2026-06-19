from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import structlog
from algotrading.infra.connectivity import Clock
from algotrading.infra.qc import ESCALATION_PAGE
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

    trade_date: date
    correlation_id: str
    ran: tuple[str, ...]
    universe: UniverseRefreshResult | None = None
    collection: CollectionResult | None = None
    analytics: AnalyticsResult | None = None
    reconciliation: ReconciliationResult | None = None
    qc: QcJobResult | None = None
    escalation: str | None = None


@dataclass(frozen=True, slots=True)
class EodStages:

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
    root = store_root(store)
    already_done = completed_stages(root, trade_date)
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        job="eod",
        trade_date=trade_date.isoformat(),
    )
    log.info("orchestration.eod.start", already_done=sorted(already_done))

    ran: list[str] = []
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
    # Banking keys off the PAGING escalation, not the raw worst-of overall_status (ADR 0060). A
    # genuinely-blocking QC failure is one that pages: a CRITICAL-severity fail, which since ADR
    # 0060 means an INDEX defect (a constituent's CRITICAL-gate fail is downgraded to a WARNING ->
    # NOTICE). So a date banks healthy when only constituents fail and the index is clean, while an
    # index CRITICAL fail (escalation PAGE) still blocks the date from banking. The intraday cap in
    # eod_stages also lowers a provisional PAGE to NOTICE, so an intraday fire likewise banks -- the
    # pre-existing intent that intraday is informational, now expressed through the same gate.
    _commit(
        STAGE_QC,
        OUTCOME_FAILED if escalation == ESCALATION_PAGE else OUTCOME_OK,
    )

    log.info("orchestration.eod.done", ran=ran, escalation=escalation)
    return EodResult(
        trade_date=trade_date,
        correlation_id=correlation_id,
        ran=tuple(ran),
        universe=universe,
        collection=collection,
        analytics=analytics,
        reconciliation=reconciliation,
        qc=qc,
        escalation=escalation,
    )
