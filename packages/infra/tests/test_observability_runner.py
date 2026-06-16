from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.infra.observability.runner import run_job
from algotrading.infra.storage import RunRegistry, RunStatus


def _run(registry: RunRegistry, fn, **kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault("code_identity", "test-sha-clean")
    return run_job(
        "reconstruct",
        fn,
        registry=registry,
        environment="test",
        code_version="1.2.3",
        config_hashes={"observability": "cfg"},
        **kwargs,
    )


def test_successful_job_records_ok_and_returns_value(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)
    result = _run(registry, lambda: 42)
    assert result.value == 42
    assert result.record.manifest.status == RunStatus.OK
    assert registry.last_healthy("reconstruct") is not None


def test_failing_job_records_failed_then_reraises(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)

    def boom():  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        _run(registry, boom, run_id="r1")
    runs = registry.list_runs("reconstruct")
    assert len(runs) == 1
    assert runs[0].manifest.status == RunStatus.FAILED
    assert registry.last_healthy("reconstruct") is None


def test_restart_under_same_run_id_is_idempotent(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)
    _run(registry, lambda: 1, run_id="fixed")
    _run(registry, lambda: 1, run_id="fixed")
    runs = registry.list_runs("reconstruct")
    assert len(runs) == 1


def test_correlation_id_is_recorded_and_threadable(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)
    result = _run(registry, lambda: None, correlation_id="collector-session-7")
    assert result.record.manifest.correlation_id == "collector-session-7"


def test_generated_run_id_when_absent(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)

    def clock() -> datetime:
        return datetime(2026, 6, 19, 14, 30, 0, tzinfo=UTC)

    result = _run(registry, lambda: None, clock=clock)
    assert result.record.manifest.run_id.startswith("reconstruct-")


def test_injected_code_identity_is_recorded_and_round_trips(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path)
    result = _run(registry, lambda: None, run_id="ci", code_identity="abc123-dirty")
    assert result.record.manifest.code_identity == "abc123-dirty"
    assert registry.list_runs("reconstruct")[0].manifest.code_identity == "abc123-dirty"
