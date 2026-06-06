"""Pipeline runner: launch a surface build as a tracked, async-safe job.

The job infrastructure (``JobState``, ``JobStatus``, ``JOB_STORE``, the queue/poll
state machine) is fully wired: ``new_job`` registers a job, ``launch_pipeline`` runs it
off the request thread, and ``GET /api/jobs/{id}`` polls its lifecycle.

The SAMPLE provider builds a real surface by **replaying a committed day** through the
exact actor pipeline (C6's unified collection seam). It reads the store's most recent
committed day for the underlying read-only via :func:`collectors.replay_day`, re-emits
those events through the production :class:`collectors.ReplaySource` push adapter into a
**throwaway temp store**, and drives :func:`orchestration.build_surface` over it. The temp
store isolates the run: ``build_surface`` re-captures and re-derives without ever writing
to the canonical ``data/`` store (which it only ever reads). The fitted surface is reduced
to a small job summary the web app polls. ``persist=False`` — a SAMPLE run reports the
surface it computed; it does not restate the committed analytics.

Replay-into-the-same-store is *not* used here on purpose: the committed sample day predates
C6's content-addressed ``event_id`` scheme, so re-capturing it into ``data/`` would append
duplicates rather than no-op. The temp store sidesteps that entirely.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import structlog
from algotrading.core.config import config_hashes, load_platform_config
from algotrading.infra.collectors import ReplaySource, replay_day
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.orchestration import SurfaceJobRequest, build_surface
from algotrading.infra.storage import ParquetStore

from .context import AppContext
from .providers import SAMPLE_PROVIDER, is_runnable

_LOGGER = structlog.get_logger("frontend.runner")

# The market-data type a replayed SAMPLE session records on its status (3 = delayed/last).
_SAMPLE_MARKET_DATA_TYPE = 3


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


def _resolve_sample_day(ctx: AppContext, underlying: str) -> date:
    """The most recent committed trade date with raw events for ``underlying``.

    Raises if the store holds no committed day for it — a SAMPLE run has nothing to replay.
    """
    days = [
        part_date
        for part_date, part_underlying in ctx.store.list_partitions("raw_market_events")
        if part_underlying == underlying
    ]
    if not days:
        raise RuntimeError(
            f"no committed sample day for {underlying!r} in the store: nothing to replay. "
            "Seed a day into the raw layer (or pick an underlying the store holds)."
        )
    return max(days)


def _build_sample_surface(ctx: AppContext, job: JobStatus) -> dict[str, Any]:
    """Replay the latest committed day for the underlying into a surface, in a temp store.

    Reads the canonical store read-only (the day's events + instrument masters), replays the
    events through the production :class:`ReplaySource` into an isolated temp store, drives
    :func:`build_surface` over the exact actor pipeline, and reduces the fitted surface to a
    job summary. ``data/`` is never written — only read.
    """
    underlying = job.underlying
    trade_date = _resolve_sample_day(ctx, underlying)
    events = replay_day(ctx.store, trade_date, underlying=underlying)
    masters = list(
        ctx.store.read("instrument_master", trade_date=trade_date, underlying=underlying)
    )
    config = load_platform_config(ctx.configs_dir)
    cfg_hashes = config_hashes(config)
    # Value as-of the last quote in the day — no look-ahead, and reproducible from the events.
    as_of = max(event.canonical_ts for event in events)
    replay_source = ReplaySource(events)

    with TemporaryDirectory(prefix="sample-surface-") as tmp:
        temp_store = ParquetStore(Path(tmp))
        # Seed the masters so the temp store is self-sufficient for the analytics read-back.
        temp_store.write("instrument_master", masters)
        result = build_surface(
            request=SurfaceJobRequest(
                symbol=underlying,
                trade_date=trade_date,
                market_data_type=_SAMPLE_MARKET_DATA_TYPE,
                as_of=as_of,
                calc_ts=as_of,
                persist=False,
            ),
            store=temp_store,
            config=config,
            config_hashes=cfg_hashes,
            adapter=replay_source,
            masters=masters,
            drive=lambda _collector: replay_source.pump(),
            clock=ManualClock(start=as_of),
            correlation_id=f"api-{job.job_id}",
        )

    params = result.outputs.surface_parameters
    return {
        "underlying": underlying,
        "trade_date": trade_date.isoformat(),
        "n_surface_params": len(params),
        "n_fitted_maturities": result.fitted_maturities,
        "config_hashes": cfg_hashes,
        "code_version": params[0].provenance.code_version if params else None,
    }


def _run_in_thread(ctx: AppContext, job_id: str) -> None:
    """Synchronous job body executed in a worker thread."""
    job = JOB_STORE[job_id]
    job.state = JobState.RUNNING
    job.started_at = datetime.now(tz=UTC)
    try:
        if job.provider.upper() == SAMPLE_PROVIDER:
            job.message = "Replaying the latest committed day into a surface…"
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
