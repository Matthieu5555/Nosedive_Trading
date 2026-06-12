"""Run router: list providers, launch a pipeline run, and poll job status.

``POST /api/run`` launches a tracked job (see :mod:`algotrading.frontend.runner`); the
offline ``SAMPLE`` provider produces a real, persisted surface. Unknown or non-runnable
providers are rejected with a typed payload, not a 500. Jobs live on the app-lifetime
:class:`~algotrading.frontend.runner.PipelineRunner` hung on ``app.state.runner``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..deps import CtxDep
from ..providers import SAMPLE_PROVIDER, all_capabilities, capability_for, is_runnable
from ..runner import PipelineRunner

router = APIRouter(prefix="/api", tags=["run"])


class RunRequest(BaseModel):
    """The body of a run launch: which provider, and (optionally) which underlying."""

    provider: str = SAMPLE_PROVIDER
    underlying: str | None = None


def _pipeline_runner(request: Request) -> PipelineRunner:
    runner: PipelineRunner = request.app.state.runner
    return runner


RunnerDep = Annotated[PipelineRunner, Depends(_pipeline_runner)]


@router.get("/providers")
def list_providers() -> JSONResponse:
    """Capabilities of every known provider — drives the UI selector."""
    return JSONResponse({"providers": [c.to_dict() for c in all_capabilities()]})


@router.get("/run/underlyings")
def list_run_underlyings(ctx: CtxDep) -> JSONResponse:
    """Underlyings that already have a persisted surface, plus the context default.

    Namespaced as ``/run/underlyings`` (not ``/underlyings``) to avoid shadowing the
    Codex market router's ``/api/underlyings`` fixture-selector in the combined app.
    """
    underlyings = {underlying for _, underlying in ctx.store.list_partitions("surface_parameters")}
    underlyings.add(ctx.default_underlying)
    return JSONResponse({"underlyings": sorted(underlyings)})


@router.post("/run")
def launch_run(ctx: CtxDep, runner: RunnerDep, body: RunRequest) -> JSONResponse:
    """Launch a run job for the requested provider."""
    capability = capability_for(body.provider)
    if capability is None:
        return JSONResponse(
            {"error": "unknown_provider", "provider": body.provider}, status_code=400
        )
    if not is_runnable(body.provider):
        return JSONResponse(
            {
                "error": "provider_unavailable",
                "provider": body.provider,
                "note": capability.note,
            },
            status_code=409,
        )
    underlying = body.underlying or ctx.default_underlying
    job = runner.new_job(body.provider.upper(), underlying)
    runner.launch_pipeline(ctx, job)
    return JSONResponse(job.to_dict(), status_code=202)


@router.get("/jobs/{job_id}")
def get_job(runner: RunnerDep, job_id: str) -> JSONResponse:
    """Return one job's status, or a typed not-found payload."""
    job = runner.jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "job_not_found", "job_id": job_id}, status_code=404)
    return JSONResponse(job.to_dict())


# Sort sentinel for a not-yet-started job: orders before any real start time.
_EPOCH = datetime.min.replace(tzinfo=UTC)


@router.get("/jobs")
def list_jobs(runner: RunnerDep) -> JSONResponse:
    """All jobs, most-recently-started first."""
    jobs = sorted(runner.jobs.values(), key=lambda j: j.started_at or _EPOCH, reverse=True)
    return JSONResponse({"jobs": [job.to_dict() for job in jobs]})
