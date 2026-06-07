"""Orchestration and observability — the operable jobs, metrics, alerts, EOD sequence.

This package wires the already-green compute keystones (the actor and the QC plane)
into something an operator runs and watches. It is the "kitchen manager": it holds no
math and no economics, it sequences the jobs, records what ran, measures it, and raises
a hand when something is wrong. One driver only — the actor (``algotrading.infra.actor``,
hosted on Nautilus per ADR 0023); these are jobs *around* it, never a second analytics
path, so the byte-identical replay guarantee holds.

The fastest path — run the canonical end-of-day sequence from one place:

    from algotrading.infra.orchestration import EodStages, run_end_of_day, build_metrics
    # build each stage as a zero-arg callable closing over store/config/clock/corr_id,
    # then:
    result = run_end_of_day(store, trade_date=day, correlation_id=corr,
                            clock=clock, stages=stages)

What lives here:

* **jobs** — universe refresh, live collection (``collect_live``), incremental analytics,
  EOD reconciliation, each a function of injected dependencies with a structured,
  correlation-id-bound log line.
* **surface_job** — the end-to-end "give me a surface for this symbol" use case: resolve a
  chain, capture quotes via ``collect_live``, run the actor, summarize the fitted surface.
* **provider_flow** — capture from several providers into the one raw layer through the one
  collector, then run the single actor over the union (the multi-broker capture driver).
* **qc_job** — the operable wrapper that runs the QC checks over a day's outputs and
  writes the ``QcResult`` rows.
* **metrics** — the five well-labeled prometheus metrics (event rate, stale ratios,
  forward/solver failures, run time), built over an injected registry.
* **alerts** — the named conditions (collector death, missing partition, elevated
  failure rate, QC fail, and grid coverage breach), each with a documented detection
  interval and an injected clock.
* **dashboard** — a structured status object answering is-data-flowing /
  are-surfaces-building / is-QC-passing / are-scenarios-current, with the last healthy
  run and current backlog first-class.
* **run_state** — the durable ledger that makes restart idempotent and the dashboard
  answerable.
* **pipeline** — the canonical end-of-day run sequence: ordered, idempotent, logged.

The ``reconstruction`` subpackage (historical replay/backfill) is owned separately and
is deliberately *not* imported here.

The collection seam is now unified (ADR 0027 / C6): one :class:`collectors.RawCollector`,
one :class:`collectors.BrokerTick`, content-addressed exactly-once capture. ``collect_live``,
``surface_job`` and ``provider_flow`` all drive that one collector, and the EOD pipeline's
collection stage wires to ``collect_live`` (still injectable as :class:`EodStages` so the
sequence stays testable without a broker).
"""

from __future__ import annotations

from .alerts import (
    ALERT_COLLECTOR_DEATH,
    ALERT_COVERAGE_BREACH,
    ALERT_ELEVATED_FAILURE_RATE,
    ALERT_MISSING_PARTITION,
    ALERT_QC_FAIL,
    COLLECTOR_SILENCE_SECONDS,
    FAILURE_WINDOW,
    MAX_FAILURE_RATIO,
    Alert,
    collector_death_alert,
    coverage_breach_alerts,
    elevated_failure_rate_alert,
    missing_partition_alerts,
    qc_fail_alert,
)
from .dashboard import (
    DashboardStatus,
    build_dashboard,
    render_dashboard,
)
from .eod_runner import (
    EOD_JOB_NAME,
    EodRunError,
    EodRunPlan,
    FiredIndex,
    RunnerDeps,
    SessionResolver,
    StagesBuilder,
    build_default_deps,
    default_stages_builder,
    plan_fire,
    run_fire,
)
from .eod_runner import main as eod_run_main
from .jobs import (
    AnalyticsResult,
    CollectionResult,
    FeedDriver,
    ReconciliationResult,
    UniverseRefreshResult,
    collect_live,
    reconcile_end_of_day,
    record_forward_failure,
    refresh_universe,
    run_incremental_analytics,
)
from .metrics import OrchestrationMetrics, build_metrics, sample_value
from .pipeline import EodResult, EodStages, run_end_of_day
from .provider_flow import ProviderCapture, ProviderFlowResult, run_provider_flow
from .qc_job import QcJobResult, run_qc
from .run_state import (
    EOD_STAGES,
    OUTCOME_FAILED,
    OUTCOME_OK,
    STAGE_ANALYTICS,
    STAGE_COLLECTION,
    STAGE_QC,
    STAGE_RECONCILIATION,
    STAGE_UNIVERSE_REFRESH,
    StageRun,
    backlog_stages,
    completed_stages,
    last_healthy_trade_date,
    latest_by_stage,
    read_stage_runs,
    record_stage,
)
from .storage_root import store_root
from .surface_job import (
    MarketDataDiagnostics,
    SurfaceJobRequest,
    SurfaceJobResult,
    build_surface,
)

__all__ = [
    "ALERT_COLLECTOR_DEATH",
    "ALERT_COVERAGE_BREACH",
    "ALERT_ELEVATED_FAILURE_RATE",
    "ALERT_MISSING_PARTITION",
    "ALERT_QC_FAIL",
    "COLLECTOR_SILENCE_SECONDS",
    "EOD_JOB_NAME",
    "EOD_STAGES",
    "FAILURE_WINDOW",
    "MAX_FAILURE_RATIO",
    "OUTCOME_FAILED",
    "OUTCOME_OK",
    "STAGE_ANALYTICS",
    "STAGE_COLLECTION",
    "STAGE_QC",
    "STAGE_RECONCILIATION",
    "STAGE_UNIVERSE_REFRESH",
    "Alert",
    "AnalyticsResult",
    "CollectionResult",
    "DashboardStatus",
    "EodResult",
    "EodRunError",
    "EodRunPlan",
    "EodStages",
    "FeedDriver",
    "FiredIndex",
    "MarketDataDiagnostics",
    "OrchestrationMetrics",
    "ProviderCapture",
    "ProviderFlowResult",
    "QcJobResult",
    "ReconciliationResult",
    "RunnerDeps",
    "SessionResolver",
    "StageRun",
    "StagesBuilder",
    "SurfaceJobRequest",
    "SurfaceJobResult",
    "UniverseRefreshResult",
    "backlog_stages",
    "build_dashboard",
    "build_default_deps",
    "build_metrics",
    "build_surface",
    "collect_live",
    "collector_death_alert",
    "completed_stages",
    "coverage_breach_alerts",
    "default_stages_builder",
    "elevated_failure_rate_alert",
    "eod_run_main",
    "last_healthy_trade_date",
    "latest_by_stage",
    "missing_partition_alerts",
    "plan_fire",
    "qc_fail_alert",
    "read_stage_runs",
    "reconcile_end_of_day",
    "record_forward_failure",
    "record_stage",
    "refresh_universe",
    "render_dashboard",
    "run_end_of_day",
    "run_fire",
    "run_incremental_analytics",
    "run_provider_flow",
    "run_qc",
    "sample_value",
    "store_root",
]
