"""Pipeline runner: launch a surface build as a tracked, async-safe job.

The job infrastructure (JobState, JobStatus, JOB_STORE, polling) is fully wired now.
The SAMPLE provider will drive ``algotrading.infra.orchestration.build_surface`` through
the full actor pipeline once the C1 (market-data/actor) and C3 (orchestration) seams
land. Until then the job transitions to ERROR with a typed "seam pending" message —
the queue/poll/state-machine lifecycle is exercised end-to-end and the test suite can
verify it.

When C3 lands, ``_build_sample_surface`` must be completed with:
  - The chain fixture (will move from ``backend/src/fixtures`` to ``packages/infra``)
  - ``algotrading.infra.orchestration.SurfaceJobRequest`` + ``build_surface``
  - ``algotrading.infra.universe.ChainSelection``
  - ``algotrading.infra.connectivity.FakeBrokerSession`` / ``ManualClock`` / ``SessionSupervisor``
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

# Checked once at module load: avoid repeated try/import on every job.
try:
    from algotrading.infra.orchestration import build_surface as _build_surface_fn  # noqa: F401
    _ORCHESTRATION_AVAILABLE = True
except ImportError:
    _ORCHESTRATION_AVAILABLE = False


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
    """Drive build_surface over the offline sample chain fixture.

    Requires C1 (market-data/actor) and C3 (orchestration) seams. Called only when
    _ORCHESTRATION_AVAILABLE is True. Raises RuntimeError until the fixture move and
    connectivity stubs are in packages/infra.
    """
    # Imports are deferred inside the function (not at module top) so the module
    # stays importable now; the try/except at module level already gates this path.
    from algotrading.core import config_hash, load_config  # noqa: PLC0415
    from algotrading.infra.orchestration import (  # type: ignore[import-not-found] # noqa: PLC0415
        SurfaceJobRequest,
        build_surface,
    )

    # Chain fixtures are in backend/src/fixtures until C1 moves them to packages/infra.
    try:
        from fixtures.library import get_fixture  # type: ignore[import-untyped] # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "fixtures.library not importable: chain fixtures are in backend/src/fixtures "
            "and haven't moved to packages/infra yet (C1 pending)."
        ) from exc

    from algotrading.infra.universe import ChainSelection  # type: ignore[import-not-found] # noqa: PLC0415

    chain = get_fixture("synthetic_known_answer")
    config = load_config(ctx.configs_dir / "default.toml")
    cfg_hash = config_hash(config)
    symbol = chain.underlying.underlying_symbol

    request = SurfaceJobRequest(
        symbol=symbol,
        trade_date=chain.as_of.date(),
        selection=ChainSelection(),
        as_of=chain.as_of,
        calc_ts=chain.as_of,
    )
    result = build_surface(
        request=request,
        store=ctx.store,
        config=config,
        config_hash=cfg_hash,
        correlation_id=f"api-{job.job_id}",
    )
    params = result.outputs.surface_parameters
    return {
        "underlying": symbol,
        "trade_date": chain.as_of.date().isoformat(),
        "n_surface_params": len(params),
        "n_fitted_maturities": result.fitted_maturities,
        "config_hash": cfg_hash,
        "code_version": params[0].provenance.code_version if params else None,
    }


def _run_in_thread(ctx: AppContext, job_id: str) -> None:
    """Synchronous job body executed in a worker thread."""
    job = JOB_STORE[job_id]
    job.state = JobState.RUNNING
    job.started_at = datetime.now(tz=UTC)
    try:
        if job.provider.upper() == SAMPLE_PROVIDER:
            if not _ORCHESTRATION_AVAILABLE:
                raise RuntimeError(
                    "C1+C3 seams not yet landed: "
                    "algotrading.infra.orchestration.build_surface is not available. "
                    "The SAMPLE pipeline will run once orchestration and actor are merged."
                )
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
