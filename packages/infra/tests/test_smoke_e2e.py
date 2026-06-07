"""V1 driver-honesty tests for ``scripts/smoke_e2e.py``.

The smoke is itself a test artifact, so this surface is light and about the driver being
honest -- the 0/1/2 exit convention, determinism, offline-by-default, SKIP-not-FAIL for an
unlanded route, and FAIL on a 5xx. It does **not** re-implement the granular suites; the
depth lives in the acceptance tests the smoke echoes by reference.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# The driver lives in the top-level scripts/ dir (outside any package); put it on the path.
_REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").exists())
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import smoke_e2e as smoke  # noqa: E402


# --- Exit-code convention (pure verdict folding) -----------------------------
def _r(name: str, status: str, *, hard: bool = True) -> smoke.StageResult:
    return smoke.StageResult(name, status, "detail", hard=hard)


def test_smoke_exit_code_convention() -> None:
    # All PASS -> 0.
    assert smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.PASS)]) == smoke.EXIT_OK
    # A hard-stage failure -> 1, regardless of other PASS/SKIP.
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.FAIL, hard=True)])
        == smoke.EXIT_HARD
    )
    # A SKIP (no hard failure) -> 2.
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.SKIP, hard=False)])
        == smoke.EXIT_SOFT
    )
    # A soft failure (no hard failure) -> 2, never 1.
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.FAIL, hard=False)])
        == smoke.EXIT_SOFT
    )
    # A hard failure dominates a soft one.
    assert (
        smoke.compute_exit_code([_r("a", smoke.FAIL, hard=False), _r("b", smoke.FAIL, hard=True)])
        == smoke.EXIT_HARD
    )


# --- Endpoint probing: no 500s, skip unlanded --------------------------------
def _stub_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/ok")
    def _ok() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/boom")
    def _boom() -> JSONResponse:
        raise RuntimeError("router wired to raise")

    return app


def test_smoke_asserts_no_500s() -> None:
    app = _stub_app()
    specs = [smoke.EndpointSpec("/api/boom", {}, required=True)]
    with TestClient(app, raise_server_exceptions=False) as client:
        results = smoke.probe_endpoints(client, app, specs)
    assert len(results) == 1
    assert results[0].status == smoke.FAIL
    assert results[0].hard is True  # a 5xx on a real route is a hard stage failure
    assert smoke.compute_exit_code(results) == smoke.EXIT_HARD


def test_smoke_skips_unlanded_stages() -> None:
    app = _stub_app()  # has /api/ok and /api/boom, but NOT /api/not-landed
    specs = [
        smoke.EndpointSpec("/api/ok", {}, required=True),
        smoke.EndpointSpec("/api/not-landed", {}, required=False),
    ]
    with TestClient(app, raise_server_exceptions=False) as client:
        results = smoke.probe_endpoints(client, app, specs)
    by_path = {r.name: r for r in results}
    assert by_path["bff /api/ok"].status == smoke.PASS
    # An unregistered route degrades to SKIP (a soft outcome), never a hard FAIL.
    assert by_path["bff /api/not-landed"].status == smoke.SKIP
    assert smoke.compute_exit_code(results) == smoke.EXIT_SOFT  # 2, not 1


# --- Full offline run: deterministic, no network -----------------------------
def test_smoke_offline_needs_no_network() -> None:
    # The default offline path (committed chain replay) reaches a verdict with no broker,
    # no network, no entitlement -- so it never hard-fails on connectivity. Web is skipped
    # so the run is fast and Node-independent here.
    code = smoke.run_smoke(["--skip-web"])
    assert code in (smoke.EXIT_OK, smoke.EXIT_SOFT)  # alive; 2 only because web is skipped


def test_smoke_is_deterministic() -> None:
    # Two consecutive offline runs produce the identical verdict (the smoke must not be flaky
    # to be a gate). The byte-identical derived bytes are asserted inside the run itself
    # (stage5), so an equal verdict across runs is the externally-visible determinism handle.
    first = smoke.run_smoke(["--skip-web"])
    second = smoke.run_smoke(["--skip-web"])
    assert first == second


def test_smoke_python_stages_are_green_offline() -> None:
    # The spine stages (bootstrap, replay, analytics, every BFF endpoint, provenance,
    # byte-identical) must PASS offline -- only the SKIPped grid/web degrade the verdict.
    repo_root = smoke.find_repo_root(Path(smoke.__file__).resolve())
    import tempfile  # noqa: PLC0415

    data_root = Path(tempfile.mkdtemp(prefix="smoke-test-"))
    try:
        args = smoke._parse_args(["--skip-web"])
        results = smoke._run_stages(args, repo_root, data_root)
    finally:
        import shutil  # noqa: PLC0415

        shutil.rmtree(data_root, ignore_errors=True)
    statuses = {r.name: r.status for r in results}
    # No hard stage may fail.
    assert not any(r.status == smoke.FAIL and r.hard for r in results), statuses
    for required in (
        "stage0 bootstrap",
        "stage1 replay",
        "stage2 analytics",
        "bff /api/health",
        "bff /api/analytics",
        "stage5 provenance",
        "stage5 byte-identical",
    ):
        assert statuses[required] == smoke.PASS, (required, statuses)


@pytest.mark.parametrize("code", [smoke.EXIT_OK, smoke.EXIT_HARD, smoke.EXIT_SOFT])
def test_verdict_labels_cover_every_exit_code(code: int) -> None:
    # The summary printer must have a label for every exit code it can emit (no KeyError).
    smoke._print_summary([_r("a", smoke.PASS)], code, as_json=False)
