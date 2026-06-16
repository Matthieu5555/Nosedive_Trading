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
from .providers import SAMPLE_PROVIDER
from .store_reads import latest_partition_date

_LOGGER = structlog.get_logger("frontend.runner")

_SAMPLE_MARKET_DATA_TYPE = 3

_MAX_WORKERS = 2


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobStatus:

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


class PipelineRunner:

    def __init__(self) -> None:
        self.jobs: dict[str, JobStatus] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=_MAX_WORKERS, thread_name_prefix="frontend-runner"
        )

    def new_job(self, provider: str, underlying: str) -> JobStatus:
        job = JobStatus(
            job_id=uuid.uuid4().hex[:8], provider=provider, underlying=underlying
        )
        self.jobs[job.job_id] = job
        return job

    def launch_pipeline(self, ctx: AppContext, job: JobStatus) -> None:
        self._executor.submit(self._run_job, ctx, job.job_id)

    def run_now(self, ctx: AppContext, job: JobStatus) -> None:
        self._run_job(ctx, job.job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_job(self, ctx: AppContext, job_id: str) -> None:
        job = self.jobs[job_id]
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


def _resolve_sample_day(ctx: AppContext, underlying: str) -> date:
    day = latest_partition_date(
        ctx.store.list_partitions("raw_market_events"), underlying
    )
    if day is None:
        raise RuntimeError(
            f"no committed sample day for {underlying!r} in the store: nothing to replay. "
            "Seed a day into the raw layer (or pick an underlying the store holds)."
        )
    return day


def _build_sample_surface(ctx: AppContext, job: JobStatus) -> dict[str, Any]:
    underlying = job.underlying
    trade_date = _resolve_sample_day(ctx, underlying)
    events = replay_day(ctx.store, trade_date, underlying=underlying)
    masters = list(
        ctx.store.read("instrument_master", trade_date=trade_date, underlying=underlying)
    )
    config = load_platform_config(ctx.configs_dir)
    cfg_hashes = config_hashes(config)
    as_of = max(event.canonical_ts for event in events)
    replay_source = ReplaySource(events)

    with TemporaryDirectory(prefix="sample-surface-") as tmp:
        temp_store = ParquetStore(Path(tmp))
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
