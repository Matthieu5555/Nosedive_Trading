from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import config_hashes, load_platform_config
from algotrading.infra.orchestration.reconstruction import reconstruct_day
from algotrading.infra.orchestration.reconstruction.report import RECONSTRUCTED
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir

_REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").exists())
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import rebuild_from_raw as rebuild  # noqa: E402
import smoke_e2e as smoke  # noqa: E402

_PROVENANCE_TABLES = (
    "market_state_snapshots",
    "forward_curve",
    "iv_points",
    "surface_parameters",
    "surface_grid",
    "pricing_results",
    "risk_aggregates",
    "scenario_results",
    "projected_option_analytics",
)


@pytest.fixture
def configs_dir() -> Path:
    return _REPO_ROOT / "configs"


def _build_day(store: ParquetStore, configs_dir: Path) -> smoke.OfflineDay:
    day = smoke.seed_offline_day(store, _REPO_ROOT)
    positions = smoke._positions_for(store, day)
    store.write("positions", positions)
    config = load_platform_config(configs_dir)
    hashes = config_hashes(config)
    masters = store.read("instrument_master")
    instruments = [master.instrument for master in masters]
    outcome = reconstruct_day(
        store,
        day.trade_date,
        store.read("positions", trade_date=day.trade_date),
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=hashes,
        as_of=day.as_of,
        calc_ts=day.as_of,
        persist=True,
    )
    assert outcome.status == RECONSTRUCTED
    return day


def _stamp_hashes(store: ParquetStore) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table in _PROVENANCE_TABLES:
        rows = store.read(table)
        out[table] = sorted(row.provenance.stamp_hash for row in rows)
    return out


def test_raw_present_distinguishes_date_and_index(tmp_path: Path, configs_dir: Path) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)
    assert rebuild.raw_present(store, day.trade_date) is True
    assert rebuild.raw_present(store, day.trade_date, day.underlying) is True
    assert rebuild.raw_present(store, day.trade_date, "NOT-AN-INDEX") is False
    assert rebuild.raw_present(store, date(1999, 1, 4)) is False


def test_rebuild_refuses_and_purges_nothing_when_raw_absent(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    with pytest.raises(rebuild.RawAbsentError):
        rebuild.rebuild_day(store, date(2026, 6, 12), configs_dir=configs_dir, index="SX5E")
    assert list(tmp_path.iterdir()) == []


def test_resolve_as_of_prefers_override_and_reads_snapshot_layer(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)
    assert rebuild.resolve_as_of(store, day.trade_date) == day.as_of
    override = datetime(2026, 6, 12, 16, 30, tzinfo=UTC)
    assert rebuild.resolve_as_of(store, day.trade_date, override) == override


def test_resolve_as_of_raises_when_snapshot_layer_empty(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    _build_day(store, configs_dir)
    with pytest.raises(rebuild.AsOfUnresolvedError):
        rebuild.resolve_as_of(store, date(2026, 6, 11))


def test_rebuilt_partition_dirs_target_only_rebuilt_tables(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)
    dirs = rebuild.rebuilt_partition_dirs(tmp_path, day.trade_date)
    assert dirs
    raw_layer = tmp_path / rebuild.RAW_LAYER
    rebuilt_layer_table_dirs = {table_dir(tmp_path, table) for table in rebuild.REBUILT_TABLES}
    for path in dirs:
        assert raw_layer not in path.parents
        assert any(layer_dir in path.parents for layer_dir in rebuilt_layer_table_dirs)


def test_rebuild_reproduces_derived_byte_for_byte_and_leaves_raw_untouched(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)

    raw_hash_before = rebuild.hash_tree(tmp_path / rebuild.RAW_LAYER)
    stamps_before = _stamp_hashes(store)
    backup_root = tmp_path / "backups"

    result = rebuild.rebuild_day(
        store,
        day.trade_date,
        configs_dir=configs_dir,
        index=day.underlying,
        backup_root=backup_root,
    )

    assert result.status == RECONSTRUCTED
    assert result.record_count > 0
    assert result.raw_hash == raw_hash_before
    assert rebuild.hash_tree(tmp_path / rebuild.RAW_LAYER) == raw_hash_before
    assert _stamp_hashes(store) == stamps_before
    assert result.backup_dir is not None and result.backup_dir.exists()

    second = rebuild.rebuild_day(
        store,
        day.trade_date,
        configs_dir=configs_dir,
        index=day.underlying,
        backup_root=tmp_path / "backups-2",
    )
    assert second.status == RECONSTRUCTED
    assert _stamp_hashes(store) == stamps_before


def test_rebuild_purges_stale_partition_content(tmp_path: Path, configs_dir: Path) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)
    dirs = rebuild.rebuilt_partition_dirs(tmp_path, day.trade_date)
    grid_dir = next(path for path in dirs if "surface_grid" in str(path))
    sentinel = grid_dir / "stale_nonconforming.leftover"
    sentinel.write_bytes(b"non-conforming schema leftover")
    assert sentinel.exists()

    rebuild.rebuild_day(store, day.trade_date, configs_dir=configs_dir, index=day.underlying)

    assert not sentinel.exists()
    assert store.read("surface_parameters")


def test_dry_run_reports_targets_without_touching_disk(
    tmp_path: Path, configs_dir: Path
) -> None:
    store = ParquetStore(tmp_path)
    day = _build_day(store, configs_dir)
    stamps_before = _stamp_hashes(store)
    raw_hash_before = rebuild.hash_tree(tmp_path / rebuild.RAW_LAYER)

    result = rebuild.rebuild_day(
        store, day.trade_date, configs_dir=configs_dir, index=day.underlying, dry_run=True
    )

    assert result.dry_run is True
    assert result.status == "DRY_RUN"
    assert result.purged_dirs
    assert result.backup_dir is None
    assert _stamp_hashes(store) == stamps_before
    assert rebuild.hash_tree(tmp_path / rebuild.RAW_LAYER) == raw_hash_before


def test_rebuilt_tables_are_all_non_raw() -> None:
    from algotrading.infra.contracts.registry import spec_for_table

    for table in rebuild.REBUILT_TABLES:
        assert spec_for_table(table).layer != rebuild.RAW_LAYER


def _master(key: str, as_of_date: date, payload: str) -> object:
    from algotrading.infra.contracts.instrument_key import InstrumentKey
    from algotrading.infra.contracts.tables import InstrumentMaster

    instrument = InstrumentKey(
        underlying_symbol=key,
        security_type="IND",
        exchange="EUREX",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id=key,
    )
    return InstrumentMaster(
        instrument_key=key,
        as_of_date=as_of_date,
        instrument=instrument,
        raw_broker_payload=payload,
    )


def test_distinct_masters_collapses_duplicate_keys_keeping_latest_as_of() -> None:
    older = _master("SX5E", date(2026, 6, 14), "payload-older")
    newer = _master("SX5E", date(2026, 6, 16), "payload-newer")
    single = _master("OESX", date(2026, 6, 15), "payload-single")

    distinct = rebuild._distinct_masters([newer, single, older])

    keys = [master.instrument_key for master in distinct]
    assert len(keys) == 2
    assert sorted(keys) == sorted(set(keys))

    by_key = {master.instrument_key: master for master in distinct}
    assert by_key["SX5E"].as_of_date == date(2026, 6, 16)
    assert by_key["SX5E"].raw_broker_payload == "payload-newer"
    assert by_key["OESX"].as_of_date == date(2026, 6, 15)


def test_distinct_masters_is_idempotent_and_order_independent() -> None:
    a = _master("SX5E", date(2026, 6, 14), "a")
    b = _master("SX5E", date(2026, 6, 16), "b")

    forward = rebuild._distinct_masters([a, b])
    reverse = rebuild._distinct_masters([b, a])

    assert [m.instrument_key for m in forward] == [m.instrument_key for m in reverse]
    assert forward[0].as_of_date == reverse[0].as_of_date == date(2026, 6, 16)
    assert rebuild._distinct_masters(forward) == forward
