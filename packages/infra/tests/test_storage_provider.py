from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref
from algotrading.infra.contracts import DailyBar, ForwardCurvePoint
from algotrading.infra.storage import ParquetStore, StorageError
from fixtures.records import TRADE_DATE, make_record, make_stamp


def _bar(provider: str = "IBKR", underlying: str = "AAPL", **overrides: object) -> DailyBar:
    base: dict[str, object] = {
        "open": 99.0, "high": 101.5, "low": 98.5, "close": 100.25,
        "volume": 1_234_567.0, "source": "cp-rest",
        "provenance": make_stamp((source_ref("raw_market_events", f"sess-{provider}", "evt-1"),)),
    }
    return make_record("daily_bar", provider=provider, underlying=underlying,
                       **{**base, **overrides})


def _forward_sourced_from_bar(provider: str) -> ForwardCurvePoint:
    return make_record(
        "forward_curve",
        provenance=make_stamp((source_ref("daily_bar", provider, "AAPL", TRADE_DATE),)),
    )


def test_provider_blind_read_returns_both_sources_as_distinct_rows(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    both = store.read("daily_bar")
    assert len(both) == 2
    assert {b.provider: b.close for b in both} == {"IBKR": 100.25, "SAXO": 100.30}


def test_second_provider_write_does_not_overwrite_the_first(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR", close=100.25)])
    store.write("daily_bar", [_bar(provider="SAXO", close=100.30)])
    assert store.read("daily_bar", provider="IBKR")[0].close == pytest.approx(100.25)
    assert store.read("daily_bar", provider="SAXO")[0].close == pytest.approx(100.30)


def test_provider_scoped_read_returns_only_that_source(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])
    assert store.read("daily_bar", provider="IBKR") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == [saxo]


def test_lineage_resolves_within_the_referenced_provider(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    derived = _forward_sourced_from_bar("IBKR")
    resolved = store.source_records_for(derived)
    assert resolved["daily_bar"] == [ibkr]


def test_lineage_for_the_other_provider_resolves_to_the_other_bar(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])

    resolved = store.source_records_for(_forward_sourced_from_bar("SAXO"))
    assert resolved["daily_bar"] == [saxo]


def test_list_partitions_dedups_the_same_date_underlying_across_providers(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR"), _bar(provider="SAXO")])
    assert store.list_partitions("daily_bar") == [(TRADE_DATE, "AAPL")]


def test_delete_partition_is_scoped_to_one_provider(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _bar(provider="IBKR")
    saxo = _bar(provider="SAXO")
    store.write("daily_bar", [ibkr, saxo])
    store.delete_partition("daily_bar", TRADE_DATE, "AAPL", provider="SAXO")
    assert store.read("daily_bar") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == []


def test_list_versions_is_scoped_to_one_provider(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("daily_bar", [_bar(provider="IBKR")])
    store.write("daily_bar", [_bar(provider="IBKR", close=100.99)], version="recompute-2")
    assert store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="IBKR") == ["recompute-2"]
    assert store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="SAXO") == []


def test_empty_provider_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    with pytest.raises(StorageError):
        store.write("daily_bar", [_bar(provider="")])


@pytest.mark.parametrize("bad", ["IB/KR", "IB\\KR", "provider=IBKR"])
def test_provider_with_a_path_separator_or_equals_is_rejected(tmp_path: Path, bad: str) -> None:
    store = ParquetStore(tmp_path)
    with pytest.raises(StorageError):
        store.write("daily_bar", [_bar(provider=bad)])


def test_write_order_does_not_change_the_partitions_or_read_back(tmp_path: Path) -> None:
    forward = ParquetStore(tmp_path / "forward")
    reverse = ParquetStore(tmp_path / "reverse")
    ibkr = _bar(provider="IBKR", close=100.25)
    saxo = _bar(provider="SAXO", close=100.30)
    forward.write("daily_bar", [ibkr, saxo])
    reverse.write("daily_bar", [saxo, ibkr])

    key = lambda b: b.provider  # noqa: E731
    assert sorted(forward.read("daily_bar"), key=key) == sorted(reverse.read("daily_bar"), key=key)
    assert forward.list_partitions("daily_bar") == reverse.list_partitions("daily_bar")


def test_underlyings_present_lists_names_without_reading_parquet(tmp_path: Path) -> None:
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
    assert store.underlyings_present("daily_bar") == frozenset({"AAPL", "NVDA", "ASML"})


def test_underlyings_present_is_empty_for_an_absent_table_or_provider(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    assert store.underlyings_present("daily_bar") == frozenset()
    store.write("daily_bar", [_bar(provider="IBKR")])
    assert store.underlyings_present("daily_bar", provider="DERIBIT") == frozenset()
