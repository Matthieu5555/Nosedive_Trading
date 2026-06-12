"""algotrading.infra.observability — run lineage and the one logging configuration.

The observability layer records what the orchestration plane *did*, durably and
auditably, and owns how the platform's processes log:

* **runner** — :func:`run_job` wraps any job callable, recording a
  :class:`~algotrading.core.manifest.Manifest` (run id, environment, code version,
  config hashes, input/output partitions, correlation id, status) into the M10
  :class:`~algotrading.infra.storage.RunRegistry`. A failed job is recorded as failed and
  re-raised — observable, never swallowed. ``run_id`` makes a restart idempotent.

    from algotrading.infra.observability import run_job

* **structured_logging** — :func:`configure_logging`, the single platform-wide logging
  configuration (audit M8): one root handler renders every stream — structlog-native,
  ``core.log.get_logger``, plain stdlib, third-party — as one-line JSON with the pinned
  ``ts``/``level``/``logger``/``message`` schema. Call once per process entrypoint.

    from algotrading.infra.observability import configure_logging

The operator-facing metrics, alerts, and dashboard live next door in
:mod:`algotrading.infra.orchestration` (they read recorded state and the live metric
registry); this package owns the *lineage* record. Alert *routing* and live health
endpoints are deferred with the serving/API tier (handled later); the run-lineage
record is the engine-side piece and lands here now.
"""

from __future__ import annotations

from .runner import RunResult, run_job
from .structured_logging import configure_logging

__all__ = [
    "RunResult",
    "configure_logging",
    "run_job",
]
