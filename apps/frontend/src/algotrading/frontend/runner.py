from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable
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
from .job_stages import (
    CAPTURE_STAGE_TOTAL,
    SAMPLE_STAGE_TOTAL,
    SampleStage,
    capture_stage_index,
    capture_stage_label,
    sample_stage_index,
    sample_stage_label,
)
from .providers import SAMPLE_PROVIDER
from .store_reads import latest_partition_date

_LOGGER = structlog.get_logger("frontend.runner")

_SAMPLE_MARKET_DATA_TYPE = 3

_MAX_WORKERS = 2

# The canonical close-capture entrypoint (the one-shot the systemd timer fires, WS 1G). Launching
# a real run from the Operations page shells out to exactly this, so the web button and the timer
# drive the same pipeline through the same code path.
_CAPTURE_SCRIPT = ("scripts", "eod_run.py")
_CAPTURE_TIMEOUT_S = 30 * 60
_OUTPUT_TAIL_LINES = 40

# Type of the capture seam: takes the app context and the job, returns a result dict carrying at
# least {"ok": bool, "message": str}. Injectable so a test never spawns the real subprocess.
CaptureFn = Callable[["AppContext", "JobStatus"], dict[str, Any]]


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
    stage: str | None = None
    stage_index: int | None = None
    stage_total: int | None = None

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
            "stage": self.stage,
            "stage_index": self.stage_index,
            "stage_total": self.stage_total,
        }

    def mark_stage(self, stage: SampleStage) -> None:
        try:
            self.stage = sample_stage_label(stage)
            self.stage_index = sample_stage_index(stage)
            self.stage_total = SAMPLE_STAGE_TOTAL
        except Exception:  # noqa: BLE001 — narration must never break the job boundary
            self.stage = None
            self.stage_index = None
            self.stage_total = None

    def start_capture_progress(self) -> None:
        # Seed a determinate tracker at step 0/N before the first stage logs, so the run reads as
        # "launching" with a real bar rather than an indeterminate spinner.
        self.stage = "Launching the capture"
        self.stage_index = 0
        self.stage_total = CAPTURE_STAGE_TOTAL
        self.message = f"Launching the close-capture for {self.underlying}…"

    def mark_capture_stage(self, stage_name: str) -> None:
        index = capture_stage_index(stage_name)
        if index is None:  # an unrecognised stage name never corrupts the tracker
            return
        self.stage = capture_stage_label(stage_name)
        self.stage_index = index
        self.stage_total = CAPTURE_STAGE_TOTAL
        self.message = self.stage


class PipelineRunner:

    def __init__(self, *, capture: CaptureFn | None = None) -> None:
        self.jobs: dict[str, JobStatus] = {}
        # The capture seam defaults to the real subprocess launch; tests inject a stub so they
        # never spawn `uv run` against the canonical store.
        self._capture: CaptureFn = capture or _run_eod_capture
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
                # A real provider runs the canonical end-of-day close-capture. The result dict
                # reports the true outcome (exit code mapped to DONE/ERROR with an honest message)
                # rather than a fixed success string.
                result = self._capture(ctx, job)
                job.summary = result
                job.state = JobState.DONE if result.get("ok") else JobState.ERROR
                job.message = result.get("message", "")
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
    job.mark_stage(SampleStage.RESOLVE)
    trade_date = _resolve_sample_day(ctx, underlying)

    job.mark_stage(SampleStage.COLLECT)
    events = replay_day(ctx.store, trade_date, underlying=underlying)
    masters = list(
        ctx.store.read("instrument_master", trade_date=trade_date, underlying=underlying)
    )
    config = load_platform_config(ctx.configs_dir)
    cfg_hashes = config_hashes(config)
    as_of = max(event.canonical_ts for event in events)
    replay_source = ReplaySource(events)

    job.mark_stage(SampleStage.FIT)
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

    job.mark_stage(SampleStage.SUMMARIZE)
    params = result.outputs.surface_parameters
    return {
        "underlying": underlying,
        "trade_date": trade_date.isoformat(),
        "n_surface_params": len(params),
        "n_fitted_maturities": result.fitted_maturities,
        "config_hashes": cfg_hashes,
        "code_version": params[0].provenance.code_version if params else None,
    }


# JSON log events the capture subprocess emits (structlog → stderr) that we turn into live state.
_EV_STAGE_START = "orchestration.eod.stage.start"
_EV_COLLECTION_LANDED = "orchestration.eod_run.collection_landed"
_EV_NO_BASKET = "orchestration.eod_run.no_basket_source"

_MAX_CAPTURE_LINES = 200


def _capture_command(underlying: str) -> list[str]:
    # Mirrors the systemd unit's ExecStart (`uv run python scripts/eod_run.py`), scoped to the
    # selected index. No --trade-date: the runner defaults to the clock's current market day.
    return ["uv", "run", "python", "/".join(_CAPTURE_SCRIPT), "--index", underlying]


def _output_tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-_OUTPUT_TAIL_LINES:])


def _parse_log_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def _apply_capture_progress(job: JobStatus, line: str, progress: dict[str, Any]) -> None:
    """Map one subprocess log line to live job state (stage tracker + ticker/count message)."""
    event = _parse_log_line(line)
    if event is None:
        return
    # `configure_logging` renames the structlog event key to "message".
    name = event.get("message")
    if name == _EV_STAGE_START:
        stage = event.get("stage")
        if isinstance(stage, str):
            job.mark_capture_stage(stage)
    elif name == _EV_COLLECTION_LANDED:
        landed = event.get("raw_events_landed")
        indices = event.get("captured_indices")
        if isinstance(landed, int):
            progress["captured_events"] = landed
            job.message = f"{job.underlying}: captured {landed:,} market events"
        if isinstance(indices, list):
            progress["captured_indices"] = indices
    elif name == _EV_NO_BASKET:
        progress.setdefault("captured_events", 0)
        job.message = f"{job.underlying}: no live capture source bound, recording a clean empty day"


def _capture_outcome_message(returncode: int, underlying: str, progress: dict[str, Any]) -> str:
    if returncode == 0:
        events = progress.get("captured_events")
        if events:
            return f"Close-capture finished for {underlying}: {events:,} market events banked."
        return (
            f"Close-capture finished for {underlying} (exit 0), but it bound no live capture "
            "source, so a clean empty day was recorded. The subprocess saw no usable source at "
            "launch (IBKR_CP_GATEWAY unset in this run's environment, the local Gateway not "
            "reachable/authenticated at that instant, or no hosted-OAuth artifacts). If the IBKR "
            "panel shows a ready session, re-run the capture, the gateway may have authenticated "
            "after this run started."
        )
    if returncode == 1:
        return (
            f"Close-capture for {underlying} ran but escalated (exit 1): a stage failed or QC "
            "paged. Data may be persisted but is flagged, check the run logs."
        )
    if returncode == 2:
        return (
            f"Close-capture rejected the request for {underlying} (exit 2): bad arguments "
            "(unknown or disabled index, or a future trade date)."
        )
    return f"Close-capture for {underlying} exited with code {returncode}."


def _run_eod_capture(ctx: AppContext, job: JobStatus) -> dict[str, Any]:
    """Run the canonical close-capture (`scripts/eod_run.py`) as a subprocess and report it.

    This is the same one-shot the systemd timer fires; launching it from the web makes the
    Operations button capture real data rather than replay a fixture. The subprocess streams
    structured logs, which we parse into a live step tracker (which stage, which ticker, how many
    events) so the job row narrates the run instead of freezing. The script writes to
    ``ALGOTRADING_DATA_ROOT`` (else repo_root/data); we pin it to the store this BFF serves so a
    launched run lands exactly where the dashboard reads it.
    """
    repo_root = ctx.configs_dir.parent
    script = repo_root.joinpath(*_CAPTURE_SCRIPT)
    if not script.exists():
        raise RuntimeError(f"close-capture entrypoint not found at {script}")
    command = _capture_command(job.underlying)
    env = {**os.environ, "ALGOTRADING_DATA_ROOT": str(ctx.store_root)}
    job.start_capture_progress()
    _LOGGER.info(
        "frontend.runner.capture.launch",
        job_id=job.job_id,
        command=command,
        store=str(ctx.store_root),
    )
    progress: dict[str, Any] = {}
    lines: list[str] = []
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell, repo-internal entrypoint
        command,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + _CAPTURE_TIMEOUT_S
    assert proc.stdout is not None
    with proc.stdout as stream:
        for raw in stream:
            line = raw.rstrip("\n")
            if line:
                lines.append(line)
                del lines[:-_MAX_CAPTURE_LINES]
                _apply_capture_progress(job, line, progress)
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return {
                    "ok": False,
                    "underlying": job.underlying,
                    "command": " ".join(command),
                    "message": (
                        f"Close-capture for {job.underlying} timed out "
                        f"after {_CAPTURE_TIMEOUT_S}s."
                    ),
                    "output_tail": _output_tail("\n".join(lines)),
                    **progress,
                }
    returncode = proc.wait()
    return {
        "ok": returncode == 0,
        "exit_code": returncode,
        "underlying": job.underlying,
        "command": " ".join(command),
        "message": _capture_outcome_message(returncode, job.underlying, progress),
        "output_tail": _output_tail("\n".join(lines)),
        **progress,
    }
