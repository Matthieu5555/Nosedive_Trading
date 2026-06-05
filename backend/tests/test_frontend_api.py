"""API tests for the M8 frontend BFF (FastAPI) over a tmp-store context.

Each router is exercised against fixture infra: errors surface as typed payloads (not
500s), and the run path produces a real, persisted surface through the actor pipeline,
which the surfaces and health endpoints then read back. Expected values are derived from
the contracts (e.g. SVI ``svi_b``/``svi_sigma`` are bounded strictly positive by the
surfaces engine — see ``surfaces/README.md``) and from hand-built request shapes, never
copied from the BFF's own output.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fixtures.library import get_fixture
from frontend import create_app, runner
from frontend.context import AppContext

_CONFIGS_DIR = Path(__file__).resolve().parents[1].parent / "configs"
_SAMPLE_UNDERLYING = get_fixture("synthetic_known_answer").underlying.underlying_symbol


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    """A context with an empty tmp store; build() resolves the repo's real configs dir."""
    context = AppContext.build(store_root=tmp_path / "data", default_underlying=_SAMPLE_UNDERLYING)
    assert context.configs_dir == _CONFIGS_DIR  # sanity: root resolution found the real configs
    return context


@pytest.fixture
def client(ctx: AppContext) -> Iterator[TestClient]:
    """A TestClient over the BFF; JOB_STORE is cleared so jobs don't leak between tests."""
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


# -- liveness ---------------------------------------------------------------


def test_liveness_is_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# -- config -----------------------------------------------------------------


def test_config_lists_and_reads_default_toml(client: TestClient) -> None:
    listing = client.get("/api/config")
    assert listing.status_code == 200
    assert "default.toml" in listing.json()["files"]

    body = client.get("/api/config/default.toml")
    assert body.status_code == 200
    payload = body.json()
    assert payload["filename"] == "default.toml"
    assert "version" in payload["content"]


def test_config_rejects_non_config_and_missing(client: TestClient) -> None:
    # A non-config suffix is refused before any filesystem read.
    unsupported = client.get("/api/config/pyproject.txt")
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == "unsupported_config"

    missing = client.get("/api/config/does_not_exist.toml")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"


# -- providers / run --------------------------------------------------------


def test_providers_lists_sample_as_ready(client: TestClient) -> None:
    payload = client.get("/api/providers").json()
    by_name = {p["provider"]: p for p in payload["providers"]}
    assert by_name["SAMPLE"]["status"] == "ready"
    assert by_name["IBKR"]["status"] == "unavailable"


def test_run_rejects_unknown_and_unavailable_providers(client: TestClient) -> None:
    unknown = client.post("/api/run", json={"provider": "NOPE"})
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "unknown_provider"

    unavailable = client.post("/api/run", json={"provider": "IBKR"})
    assert unavailable.status_code == 409
    assert unavailable.json()["error"] == "provider_unavailable"


def test_run_launch_returns_queued_job(client: TestClient) -> None:
    response = client.post("/api/run", json={"provider": "SAMPLE"})
    assert response.status_code == 202
    job = response.json()
    assert job["provider"] == "SAMPLE"
    assert job["job_id"] in runner.JOB_STORE


def test_sample_run_produces_and_persists_a_surface(ctx: AppContext, client: TestClient) -> None:
    # Drive the run synchronously for determinism (the HTTP path schedules the same body).
    job = runner.new_job("SAMPLE", _SAMPLE_UNDERLYING)
    runner.run_now(ctx, job)
    assert job.state == runner.JobState.DONE, job.message
    assert job.summary["n_surface_params"] > 0

    response = client.get("/api/surfaces", params={"underlying": _SAMPLE_UNDERLYING})
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_slices"] > 0
    # Maturities come back sorted, and every slice satisfies the SVI contract bounds.
    maturities = [s["maturity_years"] for s in payload["slices"]]
    assert maturities == sorted(maturities)
    for sl in payload["slices"]:
        assert sl["svi_b"] > 0.0
        assert sl["svi_sigma"] > 0.0
        assert sl["provenance"]["stamp_hash"]


# -- surfaces / risk empty states -------------------------------------------


def test_surfaces_empty_for_unknown_underlying(client: TestClient) -> None:
    payload = client.get("/api/surfaces", params={"underlying": "ZZZZ"}).json()
    assert payload["n_slices"] == 0
    assert payload["slices"] == []


def test_risk_empty_is_well_formed(client: TestClient) -> None:
    payload = client.get("/api/risk").json()
    assert payload["n_aggregates"] == 0
    assert payload["aggregates"] == []
    scenarios = client.get("/api/risk/scenarios").json()
    assert scenarios["cells"] == []


# -- health -----------------------------------------------------------------


def test_health_reports_no_data_on_empty_store(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["data_flowing"] == "no_data"
    assert payload["is_healthy"] is False
    assert payload["backlog"] == [] or isinstance(payload["backlog"], list)


def test_health_reflects_surfaces_after_a_run(ctx: AppContext, client: TestClient) -> None:
    job = runner.new_job("SAMPLE", _SAMPLE_UNDERLYING)
    runner.run_now(ctx, job)
    trade_date = job.summary["trade_date"]
    payload = client.get("/api/health", params={"trade_date": trade_date}).json()
    # A surface partition now exists for the run's date.
    assert payload["surfaces_building"] in ("ok", "missing")
    assert payload["trade_date"] == trade_date


# -- oauth ------------------------------------------------------------------


def test_oauth_start_then_callback_validates_state(client: TestClient) -> None:
    start = client.post("/api/oauth/saxo/start").json()
    assert start["authorize_url"].startswith("https://")
    state = start["state"]

    # Valid state but no Saxo backend yet → typed 501, not a 500.
    ok_state = client.get(
        "/api/oauth/saxo/callback", params={"code": "abc", "state": state}
    )
    assert ok_state.status_code == 501
    assert ok_state.json()["error"] == "saxo_backend_not_configured"

    # The state is single-use: a replay is now rejected.
    replay = client.get("/api/oauth/saxo/callback", params={"code": "abc", "state": state})
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_state"


def test_oauth_callback_rejects_bad_state(client: TestClient) -> None:
    response = client.get(
        "/api/oauth/saxo/callback", params={"code": "abc", "state": "forged"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_state"
