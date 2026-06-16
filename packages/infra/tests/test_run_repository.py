from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.manifest import Manifest
from algotrading.infra.storage.factory import make_run_repository
from algotrading.infra.storage.ports import RunRepository
from algotrading.infra.storage.runs import RunRecord, RunStatus
from algotrading.infra.storage.sql_repositories import (
    SqlRunRepository,
    postgres_engine_url,
    sqlite_engine_url,
)

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
def sqlite_repo(tmp_path: Path) -> SqlRunRepository:
    return SqlRunRepository(sqlite_engine_url(tmp_path / "runs.db"))


@pytest.fixture()
def pg_dsn() -> str:
    dsn = os.environ.get("POSTGRES_URL", "")
    if not dsn:
        pytest.skip("POSTGRES_URL not set — skipping Postgres conformance tests")
    return dsn


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path: Path) -> RunRepository:
    if request.param == "postgres":
        dsn = os.environ.get("POSTGRES_URL", "")
        if not dsn:
            pytest.skip("POSTGRES_URL not set — skipping Postgres conformance tests")
        return SqlRunRepository(postgres_engine_url(dsn))
    return SqlRunRepository(sqlite_engine_url(tmp_path / "runs.db"))


_GOLDEN_RUN_PAYLOAD = (
    '{"ended_at": "2026-06-05T09:30:00+00:00", "job": "eod", '
    '"manifest": {"code_identity": "unknown", "code_version": "0.1.0", '
    '"config_hashes": {"qc": "abc123"}, "config_snapshot": {}, '
    '"correlation_id": "corr-1", "environment": "test", '
    '"input_partitions": {"raw": "2026-06-05/BTC"}, '
    '"output_partitions": {"derived": "2026-06-05/BTC"}, '
    '"run_id": "run-001", "status": "ok"}, '
    '"started_at": "2026-06-05T09:00:00+00:00"}'
)


def test_run_record_serializes_to_pinned_golden_bytes() -> None:
    assert json.dumps(_record("run-001").to_dict(), sort_keys=True) == _GOLDEN_RUN_PAYLOAD


def test_run_record_round_trips_from_its_serialized_form() -> None:
    record = _record("run-001")
    rebuilt = RunRecord.from_dict(record.to_dict())
    assert rebuilt.job == record.job
    assert rebuilt.started_at == record.started_at
    assert rebuilt.ended_at == record.ended_at
    assert rebuilt.manifest == record.manifest


def test_run_record_from_dict_applies_manifest_defaults_for_old_payloads() -> None:
    payload = json.loads(_GOLDEN_RUN_PAYLOAD)
    del payload["manifest"]["code_identity"]
    del payload["manifest"]["config_snapshot"]
    del payload["manifest"]["correlation_id"]
    rebuilt = RunRecord.from_dict(payload)
    assert rebuilt.manifest.code_identity == "unknown"
    assert rebuilt.manifest.config_snapshot == {}
    assert rebuilt.manifest.correlation_id is None


def test_sqlite_persists_pinned_payload_and_iso_t_ended_at(
    sqlite_repo: SqlRunRepository, tmp_path: Path
) -> None:
    sqlite_repo.record(_record("run-001"))
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        payload, ended_at = connection.execute(
            "SELECT payload, ended_at FROM runs WHERE run_id = 'run-001'"
        ).fetchone()
    assert payload == _GOLDEN_RUN_PAYLOAD
    assert ended_at == "2026-06-05T09:30:00+00:00"


def test_adopts_a_database_created_by_the_legacy_hand_sql_backend(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS runs (\n"
            "    run_id   TEXT PRIMARY KEY,\n"
            "    job      TEXT NOT NULL,\n"
            "    status   TEXT NOT NULL,\n"
            "    ended_at TEXT NOT NULL,\n"
            "    payload  TEXT NOT NULL\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_runs_job_ended ON runs (job, ended_at);\n"
        )
        connection.execute(
            "INSERT INTO runs (run_id, job, status, ended_at, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-001", "eod", "ok", "2026-06-05T09:30:00+00:00", _GOLDEN_RUN_PAYLOAD),
        )

    repo = SqlRunRepository(sqlite_engine_url(db_path))
    listed = repo.list_runs("eod")
    assert len(listed) == 1
    assert listed[0].manifest.run_id == "run-001"
    assert listed[0].ended_at == _T1
    healthy = repo.last_healthy("eod")
    assert healthy is not None and healthy.manifest.run_id == "run-001"
    repo.record(_record("run-002"))
    assert [r.manifest.run_id for r in repo.list_runs("eod")] == ["run-001", "run-002"]


def test_satisfies_port_structurally(sqlite_repo: SqlRunRepository) -> None:
    assert isinstance(sqlite_repo, RunRepository)


def test_postgres_satisfies_port_structurally(pg_dsn: str) -> None:
    assert isinstance(SqlRunRepository(postgres_engine_url(pg_dsn)), RunRepository)


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
    repo.record(late)
    repo.record(early)

    listed = repo.list_runs("eod")
    assert [r.manifest.run_id for r in listed] == ["run-A", "run-B"]


def test_idempotent_overwrite_on_same_run_id(repo: RunRepository) -> None:
    first = RunRecord(_manifest("run-X", RunStatus.FAILED), "eod", _T0, _T1)
    second = RunRecord(_manifest("run-X", RunStatus.OK), "eod", _T0, _T2)
    repo.record(first)
    repo.record(second)

    listed = repo.list_runs("eod")
    assert len(listed) == 1
    assert listed[0].manifest.status == RunStatus.OK
    assert listed[0].ended_at == _T2


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
    repo.record(_record("run-eod-1", job="eod"))
    repo.record(_record("run-intra-1", job="intraday"))

    assert len(repo.list_runs("eod")) == 1
    assert repo.list_runs("eod")[0].manifest.run_id == "run-eod-1"

    assert len(repo.list_runs("intraday")) == 1
    assert repo.list_runs("intraday")[0].manifest.run_id == "run-intra-1"


def test_factory_returns_sqlite_backed_repo_when_no_postgres_url(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    repo = make_run_repository(sqlite_path=tmp_path / "runs.db")
    assert isinstance(repo, SqlRunRepository)
    repo.record(_record("run-001"))
    assert (tmp_path / "runs.db").exists()


def test_factory_raises_without_any_backend(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValueError, match="No run-registry backend"):
        make_run_repository()


def test_factory_prefers_postgres_over_sqlite(pg_dsn: str, tmp_path: Path) -> None:
    repo = make_run_repository(sqlite_path=tmp_path / "runs.db", postgres_dsn=pg_dsn)
    assert isinstance(repo, SqlRunRepository)
    repo.record(_record("run-factory-pg"))
    assert not (tmp_path / "runs.db").exists()


def test_postgres_engine_url_selects_the_psycopg3_driver() -> None:
    assert (
        postgres_engine_url("postgresql://u:p@h:5432/db")
        == "postgresql+psycopg://u:p@h:5432/db"
    )
    assert (
        postgres_engine_url("postgres://u:p@h:5432/db")
        == "postgresql+psycopg://u:p@h:5432/db"
    )
    assert (
        postgres_engine_url("postgresql+psycopg://u:p@h/db")
        == "postgresql+psycopg://u:p@h/db"
    )
