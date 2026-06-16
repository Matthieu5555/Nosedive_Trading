from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

_REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").exists())
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import smoke_e2e as smoke  # noqa: E402


def _r(name: str, status: str, *, hard: bool = True) -> smoke.StageResult:
    return smoke.StageResult(name, status, "detail", hard=hard)


def test_strict_exit_code_folds_the_verdict_to_ci_binary() -> None:
    assert smoke.strict_exit_code(smoke.EXIT_SOFT) == 0
    assert smoke.strict_exit_code(smoke.EXIT_OK) == 0
    assert smoke.strict_exit_code(smoke.EXIT_HARD) == 1


def test_smoke_exit_code_convention() -> None:
    assert smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.PASS)]) == smoke.EXIT_OK
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.FAIL, hard=True)])
        == smoke.EXIT_HARD
    )
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.SKIP, hard=False)])
        == smoke.EXIT_SOFT
    )
    assert (
        smoke.compute_exit_code([_r("a", smoke.PASS), _r("b", smoke.FAIL, hard=False)])
        == smoke.EXIT_SOFT
    )
    assert (
        smoke.compute_exit_code([_r("a", smoke.FAIL, hard=False), _r("b", smoke.FAIL, hard=True)])
        == smoke.EXIT_HARD
    )


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
    assert results[0].hard is True
    assert smoke.compute_exit_code(results) == smoke.EXIT_HARD


def test_smoke_skips_unlanded_stages() -> None:
    app = _stub_app()
    specs = [
        smoke.EndpointSpec("/api/ok", {}, required=True),
        smoke.EndpointSpec("/api/not-landed", {}, required=False),
    ]
    with TestClient(app, raise_server_exceptions=False) as client:
        results = smoke.probe_endpoints(client, app, specs)
    by_path = {r.name: r for r in results}
    assert by_path["bff /api/ok"].status == smoke.PASS
    assert by_path["bff /api/not-landed"].status == smoke.SKIP
    assert smoke.compute_exit_code(results) == smoke.EXIT_SOFT


def test_smoke_offline_needs_no_network() -> None:
    code = smoke.run_smoke(["--skip-web"])
    assert code in (smoke.EXIT_OK, smoke.EXIT_SOFT)


def test_smoke_is_deterministic() -> None:
    first = smoke.run_smoke(["--skip-web"])
    second = smoke.run_smoke(["--skip-web"])
    assert first == second


def test_smoke_python_stages_are_green_offline() -> None:
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
    smoke._print_summary([_r("a", smoke.PASS)], code, as_json=False)
