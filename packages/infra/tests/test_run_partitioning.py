"""Run-partitioned tables retain every fetch instead of overwriting per trade_date.

The bug these guard: a second ``eod_run`` fire for the same trade_date used to clobber the first,
because the derived/QC tables were keyed by (trade_date, underlying) only. They now carry a
``run=<run_id>`` segment, so each fetch keeps its own complete dataset; reads default to the newest
run and can address any specific one.
"""

from __future__ import annotations

import time
from pathlib import Path

from algotrading.infra.storage import ParquetStore

from .fixtures.records import make_record


def _snapshot(run_marker: float):
    # reference_spot carries a per-run marker so we can prove which fetch a read resolved to.
    return make_record("market_state_snapshots", reference_spot=run_marker)


def test_second_fetch_does_not_overwrite_the_first(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)

    store.write("market_state_snapshots", [_snapshot(100.0)], run_id="fetch-A")
    time.sleep(0.01)  # keep run-dir mtimes distinct so "newest" is unambiguous
    store.write("market_state_snapshots", [_snapshot(200.0)], run_id="fetch-B")

    run_dirs = sorted(
        p.name
        for p in tmp_path.glob("snapshot/market_state_snapshots/trade_date=*/run=*")
    )
    assert run_dirs == ["run=fetch-A", "run=fetch-B"], "both fetches must persist on disk"


def test_default_read_resolves_to_the_newest_fetch(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)

    store.write("market_state_snapshots", [_snapshot(100.0)], run_id="fetch-A")
    time.sleep(0.01)
    store.write("market_state_snapshots", [_snapshot(200.0)], run_id="fetch-B")

    default = store.read("market_state_snapshots")
    assert [row.reference_spot for row in default] == [200.0]


def test_explicit_run_id_addresses_a_specific_fetch(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)

    store.write("market_state_snapshots", [_snapshot(100.0)], run_id="fetch-A")
    time.sleep(0.01)
    store.write("market_state_snapshots", [_snapshot(200.0)], run_id="fetch-B")

    first = store.read("market_state_snapshots", run_id="fetch-A")
    second = store.read("market_state_snapshots", run_id="fetch-B")
    assert [row.reference_spot for row in first] == [100.0]
    assert [row.reference_spot for row in second] == [200.0]


def test_non_run_partitioned_table_keeps_the_legacy_layout(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)

    # instrument_master is append-only and NOT run-partitioned: a run_id must not inject a run= dir.
    store.write("instrument_master", [make_record("instrument_master")], run_id="fetch-A")

    assert not list(tmp_path.glob("**/run=*")), "non-run tables must not gain a run= segment"
    assert store.read("instrument_master"), "legacy read path still returns the rows"


def test_runs_for_lists_each_fetch_newest_first(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    trade_date = make_record("market_state_snapshots").trade_date

    store.write("market_state_snapshots", [_snapshot(100.0)], run_id="fetch-A")
    time.sleep(0.01)  # distinct run-dir mtimes so the newest-first ordering is unambiguous
    store.write("market_state_snapshots", [_snapshot(200.0)], run_id="fetch-B")

    assert store.runs_for("market_state_snapshots", trade_date) == ["fetch-B", "fetch-A"]


def test_runs_for_ignores_the_adhoc_catch_all(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    trade_date = make_record("market_state_snapshots").trade_date
    # No run_id → lands under run=_adhoc, which is not an addressable fetch identity, so runs_for
    # reports nothing selectable even though the default read still resolves the data.
    store.write("market_state_snapshots", [_snapshot(100.0)])

    assert store.runs_for("market_state_snapshots", trade_date) == []
    assert [row.reference_spot for row in store.read("market_state_snapshots")] == [100.0]
