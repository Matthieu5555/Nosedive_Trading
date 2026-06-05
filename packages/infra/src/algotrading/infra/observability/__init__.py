"""algotrading.infra.observability — run lineage on top of the one actor.

The observability layer records what the orchestration plane *did*, durably and
auditably. Today it is the run-lineage wrapper:

* **runner** — :func:`run_job` wraps any job callable, recording a
  :class:`~algotrading.core.manifest.Manifest` (run id, environment, code version,
  config hashes, input/output partitions, correlation id, status) into the M10
  :class:`~algotrading.infra.storage.RunRegistry`. A failed job is recorded as failed and
  re-raised — observable, never swallowed. ``run_id`` makes a restart idempotent.

    from algotrading.infra.observability import run_job

The operator-facing metrics, alerts, and dashboard live next door in
:mod:`algotrading.infra.orchestration` (they read recorded state and the live metric
registry); this package owns the *lineage* record. Alert *routing* and live health
endpoints are deferred with the serving/API tier (handled later); the run-lineage
record is the engine-side piece and lands here now.
"""

from __future__ import annotations

from .runner import RunResult, run_job

__all__ = [
    "RunResult",
    "run_job",
]
