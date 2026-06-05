"""Pipeline runner: launch a surface build as a tracked, async-safe job.

The job infrastructure (``JobState``, ``JobStatus``, ``JOB_STORE``, the queue/poll
state machine) is fully wired: ``new_job`` registers a job, ``launch_pipeline`` runs it
off the request thread, and ``GET /api/jobs/{id}`` polls its lifecycle. That lifecycle is
exercised end-to-end by the test suite today.

The one thing it cannot do yet is actually *run* the SAMPLE pipeline. A surface build
starts with a live capture — resolve the chain off a broker session, collect a window of
quotes into the raw layer, then run the actor — so it depends on the broker-session →
``RawMarketEvent`` collection seam. That seam (``orchestration.surface_job`` /
``collect_live``) is owned by C6 and has not yet landed on the ``packages`` stack: the C3
orchestration package deliberately did *not* port ``build_surface`` rather than wire it to
a second, divergent collection path (see ``infra/orchestration/__init__.py``). Until C6
closes the seam, a SAMPLE run transitions to ``ERROR`` with a typed "C6 pending" message
instead of pretending to build a surface.

TODO(C6): when ``algotrading.infra.orchestration`` exports ``build_surface`` /
``SurfaceJobRequest`` over the unified collector, replace ``_build_sample_surface``'s stub
body with the real call — resolve the ``synthetic_known_answer`` chain through a
``FakeBrokerSession`` / ``ManualClock`` / ``SessionSupervisor``, drive ``build_surface``,
persist into ``ctx.store`` — so the surfaces/health endpoints read the result back. The
shape is the backend ``frontend/runner.py``'s ``_build_sample_surface``; only the import
target moves. See tasks/C6-collection-seam-unification.md.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

from .context import AppContext
from .providers import SAMPLE_PROVIDER, is_runnable

_LOGGER = structlog.get_logger("frontend.runner")

# The typed message a SAMPLE run carries until C6 lands the surface-build collection seam.
_C6_PENDING_MESSAGE = (
    "SAMPLE pipeline pending C6: a surface build starts with a live capture, so it needs "
    "the broker-session->RawMarketEvent collection seam (orchestration.surface_job / "
    "collect_live), which C6 unifies onto the packages stack. The job lifecycle is live; "
    "only the build body is stubbed. See tasks/C6-collection-seam-unification.md."
)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobStatus:
    """A run job's lifecycle, polled by the web app."""

    job_id: str
    provider: str
    underlying: str
    state: JobState = JobState.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "underlying": self.underlying,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "message": self.message,
            "summary": self.summary,
        }


# Process-wide job store (in-memory; a restart drops history, acceptable for a BFF).
JOB_STORE: dict[str, JobStatus] = {}

# CPU-bound builds run in a small thread pool off the request thread.
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frontend-runner")


def new_job(provider: str, underlying: str) -> JobStatus:
    """Register a queued job and return it."""
    job = JobStatus(job_id=uuid.uuid4().hex[:8], provider=provider, underlying=underlying)
    JOB_STORE[job.job_id] = job
    return job


def _build_sample_surface(ctx: AppContext, job: JobStatus) -> dict[str, Any]:
    """Build a surface from the offline sample chain — C6-pending stub.

    A surface build composes a live capture (``collect_live``) with the actor pipeline,
    so it depends on the unified collection seam C6 owns. Until that lands in
    ``algotrading.infra.orchestration`` this raises a typed error and the caller marks the
    job ERROR. See the module docstring and tasks/C6-collection-seam-unification.md.
    """
    raise RuntimeError(_C6_PENDING_MESSAGE)


def _run_in_thread(ctx: AppContext, job_id: str) -> None:
    """Synchronous job body executed in a worker thread."""
    job = JOB_STORE[job_id]
    job.state = JobState.RUNNING
    job.started_at = datetime.now(tz=UTC)
    try:
        if job.provider.upper() == SAMPLE_PROVIDER:
            job.message = "Building surface from the offline sample chain…"
            job.summary = _build_sample_surface(ctx, job)
            job.state = JobState.DONE
            job.message = "Pipeline completed successfully"
        else:
            job.state = JobState.ERROR
            job.message = f"provider {job.provider!r} is not runnable in this deployment"
    except Exception as exc:  # noqa: BLE001 — job boundary: any failure marks ERROR and is logged
        job.state = JobState.ERROR
        job.message = str(exc)
        _LOGGER.exception("run job failed", job_id=job_id, provider=job.provider)
    finally:
        job.finished_at = datetime.now(tz=UTC)


def launch_pipeline(ctx: AppContext, job: JobStatus) -> None:
    """Schedule the job on the runner thread pool. Non-blocking."""
    _EXECUTOR.submit(_run_in_thread, ctx, job.job_id)


def run_now(ctx: AppContext, job: JobStatus) -> None:
    """Run the job synchronously in the current thread (used by tests for determinism)."""
    _run_in_thread(ctx, job.job_id)


def is_provider_runnable(provider: str) -> bool:
    """Re-exported guard so the router validates without importing providers directly."""
    return is_runnable(provider)
