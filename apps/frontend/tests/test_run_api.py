"""Run router tests: provider listing, job launch, polling, typed errors."""

from __future__ import annotations

from algotrading.frontend import runner
from algotrading.frontend.context import AppContext
from fastapi.testclient import TestClient


def test_liveness_is_ok(infra_client: TestClient) -> None:
    response = infra_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_providers_lists_sample_as_ready(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/providers").json()
    by_name = {p["provider"]: p for p in payload["providers"]}
    assert by_name["SAMPLE"]["status"] == "ready"
    assert by_name["IBKR"]["status"] == "unavailable"
    assert by_name["SAXO"]["auth_required"] is True


def test_run_rejects_unknown_provider(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "NOPE"})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_provider"


def test_run_rejects_unavailable_provider(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "IBKR"})
    assert response.status_code == 409
    assert response.json()["error"] == "provider_unavailable"
    assert "note" in response.json()


def test_run_launch_returns_202_queued_job(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "SAMPLE"})
    assert response.status_code == 202
    job = response.json()
    assert job["provider"] == "SAMPLE"
    assert job["job_id"] in runner.JOB_STORE


def test_get_job_returns_status(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "SAMPLE"})
    job_id = response.json()["job_id"]
    status = infra_client.get(f"/api/jobs/{job_id}").json()
    assert status["job_id"] == job_id
    assert status["provider"] == "SAMPLE"


def test_get_job_unknown_returns_404(infra_client: TestClient) -> None:
    response = infra_client.get("/api/jobs/doesnotexist")
    assert response.status_code == 404
    assert response.json()["error"] == "job_not_found"


def test_list_jobs_includes_launched_job(infra_client: TestClient) -> None:
    infra_client.post("/api/run", json={"provider": "SAMPLE"})
    jobs = infra_client.get("/api/jobs").json()["jobs"]
    assert len(jobs) >= 1


def test_sample_run_goes_to_error_pending_c6_collection_seam(
    ctx: AppContext,
) -> None:
    # The SAMPLE build starts with a live capture and so depends on C6's collection
    # seam (orchestration.surface_job / collect_live), not yet on the packages stack.
    # The job lifecycle still runs: a SAMPLE run must settle to ERROR with the typed
    # C6-pending message, not crash past the job boundary.
    # run_now is synchronous so the job state is settled by the time we check.
    job = runner.new_job("SAMPLE", ctx.default_underlying)
    runner.run_now(ctx, job)
    assert job.state == runner.JobState.ERROR
    assert "C6" in job.message
    assert job.finished_at is not None  # the lifecycle ran to completion, error and all


def test_run_underlyings_includes_context_default(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/run/underlyings").json()
    assert "AAPL" in payload["underlyings"]  # context default from conftest
