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

* **jobs** — universe refresh, incremental analytics, EOD reconciliation, each a
  function of injected dependencies with a structured, correlation-id-bound log line.
* **qc_job** — the operable wrapper that runs the QC checks over a day's outputs and
  writes the ``QcResult`` rows.
* **metrics** — the five well-labeled prometheus metrics (event rate, stale ratios,
  forward/solver failures, run time), built over an injected registry.
* **alerts** — the four named conditions (collector death, missing partition, elevated
  failure rate, QC fail), each with a documented detection interval and an injected
  clock.
* **dashboard** — a structured status object answering is-data-flowing /
  are-surfaces-building / is-QC-passing / are-scenarios-current, with the last healthy
  run and current backlog first-class.
* **run_state** — the durable ledger that makes restart idempotent and the dashboard
  answerable.
* **pipeline** — the canonical end-of-day run sequence: ordered, idempotent, logged.

The ``reconstruction`` subpackage (historical replay/backfill) is owned separately and
is deliberately *not* imported here.

**Pending C1 (the broker→raw-event seam, ADR 0023).** The live-collection job
(``collect_live``) and the end-to-end surface job (``surface_job``) that begins with a
live capture both depend on the supervised broker stream feeding a collector that
writes ``RawMarketEvent`` rows — a seam C1 has not yet reconciled across the two tick
shapes on the ``packages`` stack (see :mod:`orchestration.jobs`). They are *not* ported
here yet rather than wired to a second, divergent collection path. The EOD pipeline's
collection stage stays an injected seam (:class:`EodStages`) so the sequence is
complete and testable today, and lands its live wiring when C1 closes the seam.
"""

from __future__ import annotations

from .alerts import (
    ALERT_COLLECTOR_DEATH,
    ALERT_ELEVATED_FAILURE_RATE,
    ALERT_MISSING_PARTITION,
    ALERT_QC_FAIL,
    COLLECTOR_SILENCE_SECONDS,
    FAILURE_WINDOW,
    MAX_FAILURE_RATIO,
    Alert,
    collector_death_alert,
    elevated_failure_rate_alert,
    missing_partition_alerts,
    qc_fail_alert,
)
from .dashboard import (
    DashboardStatus,
    build_dashboard,
    render_dashboard,
)
from .jobs import (
    AnalyticsResult,
    CollectionResult,
    ReconciliationResult,
    UniverseRefreshResult,
    reconcile_end_of_day,
    record_forward_failure,
    refresh_universe,
    run_incremental_analytics,
)
from .metrics import OrchestrationMetrics, build_metrics, sample_value
from .pipeline import EodResult, EodStages, run_end_of_day
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

__all__ = [
    "ALERT_COLLECTOR_DEATH",
    "ALERT_ELEVATED_FAILURE_RATE",
    "ALERT_MISSING_PARTITION",
    "ALERT_QC_FAIL",
    "COLLECTOR_SILENCE_SECONDS",
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
    "EodStages",
    "OrchestrationMetrics",
    "QcJobResult",
    "ReconciliationResult",
    "StageRun",
    "UniverseRefreshResult",
    "backlog_stages",
    "build_dashboard",
    "build_metrics",
    "collector_death_alert",
    "completed_stages",
    "elevated_failure_rate_alert",
    "last_healthy_trade_date",
    "latest_by_stage",
    "missing_partition_alerts",
    "qc_fail_alert",
    "read_stage_runs",
    "reconcile_end_of_day",
    "record_forward_failure",
    "record_stage",
    "refresh_universe",
    "render_dashboard",
    "run_end_of_day",
    "run_incremental_analytics",
    "run_qc",
    "sample_value",
    "store_root",
]
