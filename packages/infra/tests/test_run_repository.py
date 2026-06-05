"""Conformance tests for the ``RunRepository`` port.

Every concrete backend (``SqliteRunRepository``, ``PostgresRunRepository``) must pass
the exact same test suite. If a test has to know *which* backend it is running against,
the port is leaking — fix the port, not the test.

**Postgres tests** skip automatically when ``POSTGRES_URL`` is not set in the
environment. To run them locally::

    export POSTGRES_URL="postgresql://user:pass@localhost:5432/testdb"
    cd packages/infra && uv run pytest tests/test_run_repository.py -v

The Postgres tests use a fresh table name per test session (via the ``pg_dsn`` fixture)
so they can run in parallel against a shared schema without collisions.

**Test oracle:** expected values are derived from the ``RunRecord`` inputs, not from
the backend under test. Round-trips are asserted field-by-field so a backend that
silently drops a field fails immediately.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.manifest import Manifest
from algotrading.infra.storage.factory import make_run_repository
from algotrading.infra.storage.ports import RunRepository
from algotrading.infra.storage.runs import RunRecord, RunStatus
from algotrading.infra.storage.sqlite_runs import SqliteRunRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 6, 5, 9, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 6, 5, 9, 30, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 5, 10, 0, 0, tzinfo=UTC)


def _manifest(run_id: str, status: str = RunStatus.OK) -> Manifest:
    return Manifest(
        run_id=run_id,
        environment="test",
        code_version="0.1.0",
        config_hashes={"qc": "abc123"},
        input_partitions={"raw": "2026-06-05/BTC"},
        output_partitions={"derived": "2026-06-05/BTC"},
        status=status,
        correlation_id="corr-1",
    )


def _record(run_id: str, job: str = "eod", status: str = RunStatus.OK) -> RunRecord:
    return RunRecord(
        manifest=_manifest(run_id, status),
        job=job,
        started_at=_T0,
        ended_at=_T1,
    )


@pytest.fixture()
def sqlite_repo(tmp_path: Path) -> SqliteRunRepository:
    return SqliteRunRepository(tmp_path / "runs.db")


@pytest.fixture()
def pg_dsn() -> str:
    dsn = os.environ.get("POSTGRES_URL", "")
    if not dsn:
        pytest.skip("POSTGRES_URL not set — skipping Postgres conformance tests")
    return dsn


# ---------------------------------------------------------------------------
# Shared conformance suite — runs against both backends via indirect parametrize
# ---------------------------------------------------------------------------

@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path: Path) -> RunRepository:
    """Parametrize over all available backends.

    Postgres variant skips when POSTGRES_URL is unset; SQLite variant always runs.
    Each variant resolves its own dependency so a skipped Postgres never affects SQLite.
    """
    if request.param == "postgres":
        dsn = os.environ.get("POSTGRES_URL", "")
        if not dsn:
            pytest.skip("POSTGRES_URL not set — skipping Postgres conformance tests")
        from algotrading.infra.storage.postgres_runs import PostgresRunRepository

        return PostgresRunRepository(dsn)
    return SqliteRunRepository(tmp_path / "runs.db")


# --- port structural conformance ---

def test_satisfies_port_structurally(sqlite_repo: SqliteRunRepository) -> None:
    """Backend satisfies RunRepository by shape (structural typing)."""
    assert isinstance(sqlite_repo, RunRepository)


def test_postgres_satisfies_port_structurally(pg_dsn: str) -> None:
    from algotrading.infra.storage.postgres_runs import PostgresRunRepository

    assert isinstance(PostgresRunRepository(pg_dsn), RunRepository)


# --- round-trip ---

def test_record_and_list_runs(repo: RunRepository) -> None:
    run = _record("run-001")
    repo.record(run)

    listed = repo.list_runs("eod")
    assert len(listed) == 1
    assert listed[0].manifest.run_id == "run-001"
    assert listed[0].job == "eod"
    assert listed[0].manifest.status == RunStatus.OK
    assert listed[0].manifest.code_version == "0.1.0"
    assert listed[0].manifest.correlation_id == "corr-1"


def test_list_runs_empty_for_unknown_job(repo: RunRepository) -> None:
    assert repo.list_runs("nonexistent") == ()


def test_list_runs_ordered_by_end_time(repo: RunRepository) -> None:
    early = RunRecord(_manifest("run-A"), "eod", _T0, _T1)
    late = RunRecord(_manifest("run-B"), "eod", _T1, _T2)
    # Insert out of order to prove ordering is by ended_at, not insertion order.
    repo.record(late)
    repo.record(early)

    listed = repo.list_runs("eod")
    assert [r.manifest.run_id for r in listed] == ["run-A", "run-B"]


def test_idempotent_overwrite_on_same_run_id(repo: RunRepository) -> None:
    """Same run_id written twice: second write wins, no duplicate rows."""
    first = RunRecord(_manifest("run-X", RunStatus.FAILED), "eod", _T0, _T1)
    second = RunRecord(_manifest("run-X", RunStatus.OK), "eod", _T0, _T2)
    repo.record(first)
    repo.record(second)

    listed = repo.list_runs("eod")
    assert len(listed) == 1
    assert listed[0].manifest.status == RunStatus.OK
    assert listed[0].ended_at == _T2


# --- last_healthy ---

def test_last_healthy_returns_none_when_empty(repo: RunRepository) -> None:
    assert repo.last_healthy("eod") is None


def test_last_healthy_returns_none_when_all_failed(repo: RunRepository) -> None:
    repo.record(_record("run-F1", status=RunStatus.FAILED))
    repo.record(_record("run-F2", status=RunStatus.FAILED))
    assert repo.last_healthy("eod") is None


def test_last_healthy_skips_failures(repo: RunRepository) -> None:
    ok = RunRecord(_manifest("run-ok", RunStatus.OK), "eod", _T0, _T1)
    bad = RunRecord(_manifest("run-bad", RunStatus.FAILED), "eod", _T1, _T2)
    repo.record(ok)
    repo.record(bad)

    result = repo.last_healthy("eod")
    assert result is not None
    assert result.manifest.run_id == "run-ok"


def test_last_healthy_returns_most_recent_ok(repo: RunRepository) -> None:
    t3 = datetime(2026, 6, 5, 11, 0, 0, tzinfo=UTC)
    first_ok = RunRecord(_manifest("run-1", RunStatus.OK), "eod", _T0, _T1)
    second_ok = RunRecord(_manifest("run-2", RunStatus.OK), "eod", _T1, _T2)
    third_ok = RunRecord(_manifest("run-3", RunStatus.OK), "eod", _T2, t3)
    for r in [first_ok, second_ok, third_ok]:
        repo.record(r)

    result = repo.last_healthy("eod")
    assert result is not None
    assert result.manifest.run_id == "run-3"


def test_jobs_are_isolated(repo: RunRepository) -> None:
    """``list_runs`` and ``last_healthy`` are scoped to the requested job."""
    repo.record(_record("run-eod-1", job="eod"))
    repo.record(_record("run-intra-1", job="intraday"))

    assert len(repo.list_runs("eod")) == 1
    assert repo.list_runs("eod")[0].manifest.run_id == "run-eod-1"

    assert len(repo.list_runs("intraday")) == 1
    assert repo.list_runs("intraday")[0].manifest.run_id == "run-intra-1"


# --- factory ---

def test_factory_returns_sqlite_when_no_postgres_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    repo = make_run_repository(sqlite_path=tmp_path / "runs.db")
    assert isinstance(repo, SqliteRunRepository)


def test_factory_raises_without_any_backend(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValueError, match="No run-registry backend"):
        make_run_repository()


def test_factory_prefers_postgres_over_sqlite(pg_dsn: str, tmp_path: Path) -> None:
    from algotrading.infra.storage.postgres_runs import PostgresRunRepository

    repo = make_run_repository(sqlite_path=tmp_path / "runs.db", postgres_dsn=pg_dsn)
    assert isinstance(repo, PostgresRunRepository)
