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

    provider: str = SAMPLE_PROVIDER
    underlying: str | None = None


def _pipeline_runner(request: Request) -> PipelineRunner:
    runner: PipelineRunner = request.app.state.runner
    return runner


RunnerDep = Annotated[PipelineRunner, Depends(_pipeline_runner)]


@router.get("/providers")
def list_providers() -> JSONResponse:
    return JSONResponse({"providers": [c.to_dict() for c in all_capabilities()]})


@router.get("/run/underlyings")
def list_run_underlyings(ctx: CtxDep) -> JSONResponse:
    underlyings = {underlying for _, underlying in ctx.store.list_partitions("surface_parameters")}
    underlyings.add(ctx.default_underlying)
    return JSONResponse({"underlyings": sorted(underlyings)})


@router.post("/run")
def launch_run(ctx: CtxDep, runner: RunnerDep, body: RunRequest) -> JSONResponse:
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
    job = runner.jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "job_not_found", "job_id": job_id}, status_code=404)
    return JSONResponse(job.to_dict())


_EPOCH = datetime.min.replace(tzinfo=UTC)


@router.get("/jobs")
def list_jobs(runner: RunnerDep) -> JSONResponse:
    jobs = sorted(runner.jobs.values(), key=lambda j: j.started_at or _EPOCH, reverse=True)
    return JSONResponse({"jobs": [job.to_dict() for job in jobs]})
