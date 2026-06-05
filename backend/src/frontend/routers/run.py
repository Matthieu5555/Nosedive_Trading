"""Run router: list providers, launch a pipeline run, and poll job status.

``POST /api/run`` launches a tracked job (see :mod:`frontend.runner`); the offline
``SAMPLE`` provider produces a real, persisted surface. Unknown or non-runnable
providers are rejected with a typed payload, not a 500.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..context import AppContext
from ..providers import SAMPLE_PROVIDER, all_capabilities, capability_for, is_runnable
from ..runner import JOB_STORE, launch_pipeline, new_job

router = APIRouter(prefix="/api", tags=["run"])


class RunRequest(BaseModel):
    """The body of a run launch: which provider, and (optionally) which underlying."""

    provider: str = SAMPLE_PROVIDER
    underlying: str | None = None


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("/providers")
def list_providers() -> JSONResponse:
    """Capabilities of every known provider — drives the UI selector."""
    return JSONResponse({"providers": [c.to_dict() for c in all_capabilities()]})


@router.get("/underlyings")
def list_underlyings(request: Request) -> JSONResponse:
    """Underlyings that already have a persisted surface, plus the context default."""
    ctx = _context(request)
    underlyings = {underlying for _, underlying in ctx.store.list_partitions("surface_parameters")}
    underlyings.add(ctx.default_underlying)
    return JSONResponse({"underlyings": sorted(underlyings)})


@router.post("/run")
def launch_run(request: Request, body: RunRequest) -> JSONResponse:
    """Launch a run job for the requested provider."""
    ctx = _context(request)
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
    job = new_job(body.provider.upper(), underlying)
    launch_pipeline(ctx, job)
    return JSONResponse(job.to_dict(), status_code=202)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    """Return one job's status, or a typed not-found payload."""
    job = JOB_STORE.get(job_id)
    if job is None:
        return JSONResponse({"error": "job_not_found", "job_id": job_id}, status_code=404)
    return JSONResponse(job.to_dict())


# Sort sentinel for a not-yet-started job: orders before any real start time.
_EPOCH = datetime.min.replace(tzinfo=UTC)


@router.get("/jobs")
def list_jobs() -> JSONResponse:
    """All jobs, most-recently-started first."""
    jobs = sorted(JOB_STORE.values(), key=lambda j: j.started_at or _EPOCH, reverse=True)
    return JSONResponse({"jobs": [job.to_dict() for job in jobs]})
