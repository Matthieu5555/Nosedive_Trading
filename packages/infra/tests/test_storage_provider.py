"""D1 storage foundation: the ``provider`` partition segment (ADR 0017 / 0034 §4).

These tests pin the storage-seam behaviour D1 adds on top of the contract round-trip
and disjoint-partition checks already in ``test_p0_contracts.py``. The named D1 test
surface (spec ``tasks/D1-storage-foundation.md`` "Test surface"), with the obligations
``test_p0_contracts.py`` does not already cover:

* a ``read`` without a ``provider`` filter does **not** silently merge two sources of the
  same ``(underlying, trade_date)`` — it returns both as distinct rows because ``provider``
  is part of the primary key, never one overwriting the other;
* a ``read(..., provider=P)`` returns only that source, and lineage
  (``source_records_for``) resolves **within** the right provider — because ``provider`` is
  part of the source table's full primary key, a stamp pointing at one source's bar never
  pulls back the other source's bar for the same ``(underlying, trade_date)``;
* the partition-management surface is provider-aware: ``list_partitions`` de-duplicates the
  same ``(trade_date, underlying)`` across providers, ``delete_partition(provider=P)`` removes
  only that source's segment, ``list_versions(provider=P)`` is scoped to one source;
* a record that cannot name a single, valid ``provider`` path segment (empty, or carrying a
  path separator / ``=``) is **rejected at the write door**, never dumped into a catch-all;
* writes are order-invariant: shuffling the input batch yields the same on-disk partitions
  and the same read-back set (TESTING.md reordering invariance).

Records come from the shared fixture builders (``fixtures.records.make_record`` /
``make_stamp``). ``DailyBar`` is the provider-partitioned table in the registry, so it
is the vehicle here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref
from algotrading.infra.contracts import DailyBar, ForwardCurvePoint
from algotrading.infra.storage import ParquetStore, StorageError
from fixtures.records import TRADE_DATE, make_record, make_stamp


def _bar(provider: str = "IBKR", underlying: str = "AAPL", **overrides: object) -> DailyBar:
    # The bar's own stamp points back at raw events; the exact source table is
    # immaterial to the bar's identity (the lineage tests here resolve bars, not events).
    base: dict[str, object] = {
        "open": 99.0, "high": 101.5, "low": 98.5, "close": 100.25,
        "volume": 1_234_567.0, "source": "cp-rest",
        "provenance": make_stamp((source_ref("raw_market_events", f"sess-{provider}", "evt-1"),)),
    }
    return make_record("daily_bar", provider=provider, underlying=underlying,
                       **{**base, **overrides})


def _forward_sourced_from_bar(provider: str) -> ForwardCurvePoint:
    """A derived record whose lineage points at *one* provider's daily bar.

    ``daily_bar``'s primary key is ``(provider, underlying, trade_date)``, so the source
    reference carries ``provider`` in its key tuple — the mechanism that makes lineage
    provider-scoped without a special-case filter.
    """
    return make_record(
        "forward_curve",
        provenance=make_stamp((source_ref("daily_bar", provider, "AAPL", TRADE_DATE),)),
    )


# -- a provider-blind read does not merge two sources -----------------------------------
def test_provider_blind_read_returns_both_sources_as_distinct_rows(tmp_path: Path) -> None:
    # Two providers writing the same (underlying, trade_date) must coexist, not overwrite:
    # provider is part of the primary key, so a default read returns BOTH bars unchanged.
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    both = store.read("daily_bar")
    assert len(both) == 2
    assert {b.provider: b.close for b in both} == {"IBKR": 100.25, "SAXO": 100.30}


def test_second_provider_write_does_not_overwrite_the_first(tmp_path: Path) -> None:
    # Writing SAXO after IBKR for the same (underlying, trade_date) must not touch IBKR's
    # partition — the failure ADR 0017 exists to prevent (sources mixing on disk).
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR", close=100.25)])
    store.write("daily_bar", [_bar(provider="SAXO", close=100.30)])
    assert store.read("daily_bar", provider="IBKR")[0].close == pytest.approx(100.25)
    assert store.read("daily_bar", provider="SAXO")[0].close == pytest.approx(100.30)


# -- provider-scoped reads and lineage --------------------------------------------------
def test_provider_scoped_read_returns_only_that_source(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])
    assert store.read("daily_bar", provider="IBKR") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == [saxo]


def test_lineage_resolves_within_the_referenced_provider(tmp_path: Path) -> None:
    # Both providers hold a bar for the SAME (underlying, trade_date). A derived record whose
    # stamp references the IBKR bar must resolve to the IBKR bar ONLY — never the SAXO one —
    # because provider is part of daily_bar's full primary key (source_records_for matches the
    # whole key, so the provider segment is what disambiguates the two).
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    derived = _forward_sourced_from_bar("IBKR")
    resolved = store.source_records_for(derived)
    assert resolved["daily_bar"] == [ibkr]  # not the SAXO bar for the same symbol/date


def test_lineage_for_the_other_provider_resolves_to_the_other_bar(tmp_path: Path) -> None:
    # The dual of the test above: a stamp pointing at SAXO resolves to SAXO, proving the
    # provider segment, not luck or ordering, is doing the disambiguation.
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    resolved = store.source_records_for(_forward_sourced_from_bar("SAXO"))
    assert resolved["daily_bar"] == [saxo]


# -- partition management is provider-aware ---------------------------------------------
def test_list_partitions_dedups_the_same_date_underlying_across_providers(tmp_path: Path) -> None:
    # IBKR and SAXO both hold (AAPL, 2026-05-29); list_partitions keeps the legacy
    # (trade_date, underlying) two-tuple shape and reports the pair once, not twice.
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR"), _bar(provider="SAXO")])
    assert store.list_partitions("daily_bar") == [(TRADE_DATE, "AAPL")]


def test_delete_partition_is_scoped_to_one_provider(tmp_path: Path) -> None:
    # Deleting SAXO's partition must leave IBKR's intact — the provider segment isolates
    # the two sources on disk.
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR")
    saxo = _bar(provider="SAXO")
    store.write("daily_bar", [ibkr, saxo])
    store.delete_partition("daily_bar", TRADE_DATE, "AAPL", provider="SAXO")
    assert store.read("daily_bar") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == []


def test_list_versions_is_scoped_to_one_provider(tmp_path: Path) -> None:
    # A restatement written under one provider's segment is not visible under another's.
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR")])
    store.write("daily_bar", [_bar(provider="IBKR", close=100.99)], version="recompute-2")
    assert store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="IBKR") == ["recompute-2"]
    assert store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="SAXO") == []


# -- malformed provider rejected at the write door --------------------------------------
def test_empty_provider_is_rejected(tmp_path: Path) -> None:
    # A provider-partitioned record with no usable provider cannot be placed; it is an
    # error, not a silent dump into a catch-all segment.
    store = ParquetStore(tmp_path)
    with pytest.raises(StorageError):
        store.write("daily_bar", [_bar(provider="")])


@pytest.mark.parametrize("bad", ["IB/KR", "IB\\KR", "provider=IBKR"])
def test_provider_with_a_path_separator_or_equals_is_rejected(tmp_path: Path, bad: str) -> None:
    # A provider value that is not a single Hive path segment would corrupt the tree, so it
    # is refused at construction time rather than producing a misplaced file.
    store = ParquetStore(tmp_path)
    with pytest.raises(StorageError):
        store.write("daily_bar", [_bar(provider=bad)])


# -- reordering invariance (TESTING.md) -------------------------------------------------
def test_write_order_does_not_change_the_partitions_or_read_back(tmp_path: Path) -> None:
    # Shuffling the input batch must not change where data lands or what reads back: the
    # partition of each record is a pure function of its (provider, trade_date, underlying).
    forward = ParquetStore(tmp_path / "forward")
    reverse = ParquetStore(tmp_path / "reverse")
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    forward.write("daily_bar", [ibkr, saxo])
    reverse.write("daily_bar", [saxo, ibkr])

    key = lambda b: b.provider  # noqa: E731
    assert sorted(forward.read("daily_bar"), key=key) == sorted(reverse.read("daily_bar"), key=key)
    assert forward.list_partitions("daily_bar") == reverse.list_partitions("daily_bar")


# -- underlyings_present: the one-pass presence scan (ohlc-constituent-backfill) --------
def test_underlyings_present_lists_names_without_reading_parquet(tmp_path: Path) -> None:
    # The presence set comes from the partition directory names alone (a filesystem walk),
    # so a caller deciding "skip what is already on disk" pays one cheap scan, never a
    # full-table parquet read per ticker. Oracle: exactly the names written, per provider.
    store = ParquetStore(tmp_path)
    store.write(
        "daily_bar",
        [
            _bar(provider="IBKR", underlying="AAPL"),
            _bar(provider="IBKR", underlying="NVDA", trade_date=date(2026, 5, 28)),
            _bar(provider="SAXO", underlying="ASML"),
        ],
    )
    assert store.underlyings_present("daily_bar", provider="IBKR") == frozenset(
        {"AAPL", "NVDA"}
    )
    assert store.underlyings_present("daily_bar", provider="SAXO") == frozenset({"ASML"})
    # Provider-blind: the union across sources.
    assert store.underlyings_present("daily_bar") == frozenset({"AAPL", "NVDA", "ASML"})


def test_underlyings_present_is_empty_for_an_absent_table_or_provider(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    assert store.underlyings_present("daily_bar") == frozenset()
    store.write("daily_bar", [_bar(provider="IBKR")])
    assert store.underlyings_present("daily_bar", provider="DERIBIT") == frozenset()
