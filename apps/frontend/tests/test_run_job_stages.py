from __future__ import annotations

from typing import Any

import pytest
from algotrading.frontend import runner
from algotrading.frontend.context import AppContext
from algotrading.frontend.job_stages import SampleStage
from algotrading.frontend.runner import JobState, JobStatus
from fastapi.testclient import TestClient

from .test_run_api import seeded_ctx  # noqa: F401 — pytest fixture reuse

EXPECTED_SEQUENCE: tuple[tuple[SampleStage, int, str], ...] = (
    (SampleStage.RESOLVE, 1, "Finding the last captured day"),
    (SampleStage.COLLECT, 2, "Collecting the options chain"),
    (SampleStage.FIT, 3, "Fitting the surface"),
    (SampleStage.SUMMARIZE, 4, "Surface summary"),
)

EXPECTED_TOTAL = 4


def _new_job() -> JobStatus:
    return JobStatus(job_id="test1234", provider="SAMPLE", underlying="AAPL")


def test_mark_stage_maps_each_stage_to_pm_label_index_and_total() -> None:
    job = _new_job()
    for stage, index, label in EXPECTED_SEQUENCE:
        job.mark_stage(stage)
        assert job.stage == label
        assert job.stage_index == index
        assert job.stage_total == EXPECTED_TOTAL


def test_pm_label_is_plain_english_never_the_engine_enum() -> None:
    job = _new_job()
    job.mark_stage(SampleStage.COLLECT)
    assert job.stage == "Collecting the options chain"
    assert job.stage != SampleStage.COLLECT.value
    assert "collect" not in (job.stage or "")
    assert "STAGE_" not in (job.stage or "")


def test_fresh_job_has_no_stage_fields() -> None:
    job = _new_job()
    assert job.stage is None
    assert job.stage_index is None
    assert job.stage_total is None


def test_to_dict_is_additive_and_serialises_the_stage_fields() -> None:
    job = _new_job()
    payload = job.to_dict()
    for key in ("job_id", "provider", "underlying", "state", "message", "summary"):
        assert key in payload
    assert payload["stage"] is None
    assert payload["stage_index"] is None
    assert payload["stage_total"] is None

    job.mark_stage(SampleStage.FIT)
    filled = job.to_dict()
    assert filled["stage"] == "Fitting the surface"
    assert filled["stage_index"] == 3
    assert filled["stage_total"] == EXPECTED_TOTAL


def test_real_sample_run_walks_the_stage_sequence_in_order(
    seeded_ctx: tuple[AppContext, object],  # noqa: F811
) -> None:
    ctx, _trade_date = seeded_ctx
    observed: list[tuple[str | None, int | None, int | None]] = []
    original = JobStatus.mark_stage

    def _recording_mark_stage(self: JobStatus, stage: SampleStage) -> None:
        original(self, stage)
        observed.append((self.stage, self.stage_index, self.stage_total))

    pipeline = runner.PipelineRunner()
    job = pipeline.new_job("SAMPLE", "AAPL")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(JobStatus, "mark_stage", _recording_mark_stage)
        pipeline.run_now(ctx, job)

    assert job.state == JobState.DONE, job.message
    assert observed == [(label, index, EXPECTED_TOTAL) for _stage, index, label in EXPECTED_SEQUENCE]
    assert job.stage_index == EXPECTED_TOTAL
    assert job.stage_total == EXPECTED_TOTAL


def test_failing_build_marks_error_and_does_not_lie_about_completion(
    seeded_ctx: tuple[AppContext, object],  # noqa: F811
) -> None:
    ctx, _trade_date = seeded_ctx
    pipeline = runner.PipelineRunner()
    job = pipeline.new_job("SAMPLE", "AAPL")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("fit blew up")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "build_surface", _boom)
        pipeline.run_now(ctx, job)

    assert job.state == JobState.ERROR
    assert "fit blew up" in job.message
    assert job.finished_at is not None
    assert job.stage_index is not None
    assert job.stage_index < EXPECTED_TOTAL
    assert job.stage == "Fitting the surface"


def test_non_sample_provider_never_reports_a_stage(ctx: AppContext) -> None:
    pipeline = runner.PipelineRunner()
    job = pipeline.new_job("IBKR", "AAPL")
    pipeline.run_now(ctx, job)
    assert job.state == JobState.ERROR
    assert job.stage is None
    assert job.stage_index is None
    assert job.stage_total is None


def test_jobs_endpoints_carry_the_stage_fields(infra_client: TestClient) -> None:
    launched = infra_client.post("/api/run", json={"provider": "SAMPLE"}).json()
    for key in ("stage", "stage_index", "stage_total"):
        assert key in launched

    one = infra_client.get(f"/api/jobs/{launched['job_id']}").json()
    for key in ("stage", "stage_index", "stage_total"):
        assert key in one

    listed = infra_client.get("/api/jobs").json()["jobs"]
    assert listed
    for key in ("stage", "stage_index", "stage_total"):
        assert key in listed[0]


def test_mark_stage_never_raises_into_the_job_boundary() -> None:
    job = _new_job()
    job.mark_stage(SampleStage.FIT)

    class _Rogue:
        value = "rogue"

    job.mark_stage(_Rogue())  # type: ignore[arg-type]
    assert job.stage is None
    assert job.stage_index is None
    assert job.stage_total is None
