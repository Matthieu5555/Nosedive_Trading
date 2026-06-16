from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from algotrading.core.config import PlatformConfig, load_platform_config
from algotrading.infra.storage import (
    ProfileRepository,
    ProfileVersion,
    SqlProfileRepository,
    build_profile_version,
    make_profile_repository,
    platform_config_from_profile,
)
from algotrading.infra.storage.sql_repositories import sqlite_engine_url

_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"


def _config() -> PlatformConfig:
    return load_platform_config(_CONFIGS_DIR)


def _tightened(config: PlatformConfig) -> PlatformConfig:
    return config.model_copy(
        update={"solver": config.solver.model_copy(update={"iv_tolerance": 1e-9})}
    )


def test_build_profile_version_is_content_addressed_and_idempotent() -> None:
    config = _config()
    a = build_profile_version("default", date(2026, 1, 1), config)
    b = build_profile_version("default", date(2026, 6, 1), config)
    assert a.content_hash == b.content_hash
    changed = build_profile_version("default", date(2026, 1, 1), _tightened(config))
    assert changed.content_hash != a.content_hash


def test_profile_version_round_trips_through_dict_and_rebuilds_the_config() -> None:
    config = _config()
    version = build_profile_version("default", date(2026, 1, 1), config)
    assert ProfileVersion.from_dict(version.to_dict()) == version
    assert platform_config_from_profile(version) == config


_GOLDEN_PROFILE_PAYLOAD = (
    '{"config_hashes": {"solver": "abc"}, '
    '"config_snapshot": {"solver": {"iv_tolerance": 1e-08}}, '
    '"content_hash": "deadbeef", "effective_from": "2026-01-01", "name": "default"}'
)


def _small_version() -> ProfileVersion:
    return ProfileVersion(
        name="default",
        effective_from=date(2026, 1, 1),
        content_hash="deadbeef",
        config_hashes={"solver": "abc"},
        config_snapshot={"solver": {"iv_tolerance": 1e-08}},
    )


def test_profile_version_serializes_to_pinned_golden_bytes() -> None:
    assert json.dumps(_small_version().to_dict(), sort_keys=True) == _GOLDEN_PROFILE_PAYLOAD


def test_sqlite_persists_pinned_payload_and_iso_effective_from(tmp_path: Path) -> None:
    repo = SqlProfileRepository(sqlite_engine_url(tmp_path / "profiles.db"))
    repo.save(_small_version())
    with sqlite3.connect(tmp_path / "profiles.db") as connection:
        payload, effective_from = connection.execute(
            "SELECT payload, effective_from FROM profiles WHERE name = 'default'"
        ).fetchone()
    assert payload == _GOLDEN_PROFILE_PAYLOAD
    assert effective_from == "2026-01-01"


def test_adopts_a_database_created_by_the_legacy_hand_sql_backend(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-profiles.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS profiles (\n"
            "    name           TEXT NOT NULL,\n"
            "    content_hash   TEXT NOT NULL,\n"
            "    effective_from TEXT NOT NULL,\n"
            "    payload        TEXT NOT NULL,\n"
            "    PRIMARY KEY (name, content_hash)\n"
            ");\n"
            "CREATE INDEX IF NOT EXISTS idx_profiles_name_eff "
            "ON profiles (name, effective_from);\n"
        )
        connection.execute(
            "INSERT INTO profiles (name, content_hash, effective_from, payload) "
            "VALUES (?, ?, ?, ?)",
            ("default", "deadbeef", "2026-01-01", _GOLDEN_PROFILE_PAYLOAD),
        )

    repo = SqlProfileRepository(sqlite_engine_url(db_path))
    assert repo.get("default", "deadbeef") == _small_version()
    assert repo.resolve_as_of("default", date(2026, 2, 1)) == _small_version()


def _repo(tmp_path: Path) -> ProfileRepository:
    return make_profile_repository(sqlite_path=tmp_path / "profiles.db")


def test_save_and_get_by_content_hash(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    version = build_profile_version("default", date(2026, 1, 1), _config())
    repo.save(version)
    assert repo.get("default", version.content_hash) == version
    assert repo.get("default", "no-such-hash") is None


def test_resolve_as_of_returns_the_version_in_force_on_a_past_day(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _config()
    v_old = build_profile_version("default", date(2026, 1, 1), base)
    v_new = build_profile_version("default", date(2026, 6, 1), _tightened(base))
    repo.save(v_old)
    repo.save(v_new)

    assert repo.resolve_as_of("default", date(2026, 6, 15)) == v_new
    assert repo.resolve_as_of("default", date(2026, 3, 1)) == v_old
    assert repo.resolve_as_of("default", date(2026, 6, 1)) == v_new


def test_resolve_before_any_effective_date_is_a_clean_miss(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.save(build_profile_version("default", date(2026, 1, 1), _config()))
    assert repo.resolve_as_of("default", date(2025, 12, 31)) is None
    assert repo.resolve_as_of("unknown", date(2026, 6, 1)) is None


def test_versions_lists_oldest_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _config()
    v_old = build_profile_version("default", date(2026, 1, 1), base)
    v_new = build_profile_version("default", date(2026, 6, 1), _tightened(base))
    repo.save(v_new)
    repo.save(v_old)
    assert repo.versions("default") == (v_old, v_new)


def test_sql_profile_repository_satisfies_the_port(tmp_path: Path) -> None:
    repo = SqlProfileRepository(sqlite_engine_url(tmp_path / "p.db"))
    assert isinstance(repo, ProfileRepository)
