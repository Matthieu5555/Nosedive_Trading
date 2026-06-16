from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from algotrading.infra.contracts import DailyBar
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.compaction import (
    compact_ticker,
    compacted_file_path,
    is_compacted_file,
    list_hot_files_for_ticker,
)
from fixtures.records import make_record

_PROVIDER = "IBKR"
_TICKERS = ["AAPL", "MSFT", "GOOG"]

_DATES = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
]

_BASE_CLOSE: dict[str, float] = {
    "AAPL": 185.0,
    "MSFT": 375.0,
    "GOOG": 140.0,
}


def _bar(ticker: str, d: date, *, day_index: int = 0) -> DailyBar:
    base = _BASE_CLOSE[ticker]
    low = round(base - 1.0, 2)
    high = round(base + day_index + 2.0, 2)
    open_ = round(base - 0.5, 2)
    close = round(base + float(day_index), 2)
    return make_record(
        "daily_bar",
        provider=_PROVIDER,
        underlying=ticker,
        trade_date=d,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000.0,
    )


def _bars_for_ticker(ticker: str) -> list[DailyBar]:
    return [_bar(ticker, d, day_index=i) for i, d in enumerate(_DATES)]


def _populate_hot_store(
    store: ParquetStore, tickers: list[str] | None = None
) -> dict[str, list[DailyBar]]:
    tickers = tickers if tickers is not None else _TICKERS
    all_bars: dict[str, list[DailyBar]] = {}
    for ticker in tickers:
        bars = _bars_for_ticker(ticker)
        all_bars[ticker] = bars
        store.write("daily_bar", bars)
    return all_bars


def test_compact_ticker_produces_identical_rows(tmp_path: Path) -> None:
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    expected_bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", expected_bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert sorted(result, key=lambda b: b.trade_date) == sorted(
        expected_bars, key=lambda b: b.trade_date
    )


def test_compact_ticker_count_matches_pre_compaction(tmp_path: Path) -> None:
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "MSFT"
    bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert len(result) == 6


def test_compact_ticker_content_hash_matches(tmp_path: Path) -> None:
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "GOOG"
    bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", bars)

    def _row_hash(bar_list: list[DailyBar]) -> str:
        rows = sorted((b.trade_date.isoformat(), b.close) for b in bar_list)
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

    ref_hash = _row_hash(bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)
    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)

    assert _row_hash(result) == ref_hash, (
        "Content hash changed after compaction — rows were mutated or reordered"
    )


def test_date_range_inclusive_bounds_on_compacted_store(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    all_bars = _bars_for_ticker(ticker)
    store.write("daily_bar", all_bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read(
        "daily_bar",
        underlying=ticker,
        provider=_PROVIDER,
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 5),
    )
    result_dates = {b.trade_date for b in result}
    assert result_dates == {date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)}
    assert len(result) == 3


def test_date_range_single_day_on_compacted_store(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read(
        "daily_bar",
        underlying=ticker,
        start_date=date(2024, 1, 4),
        end_date=date(2024, 1, 4),
    )
    assert len(result) == 1
    assert result[0].trade_date == date(2024, 1, 4)
    assert result[0].close == pytest.approx(187.0)


def test_empty_window_on_compacted_store_returns_empty(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read(
        "daily_bar",
        underlying=ticker,
        start_date=date(2023, 12, 1),
        end_date=date(2023, 12, 31),
    )
    assert result == []


def test_unknown_ticker_on_compacted_store_returns_empty(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    bars = _bars_for_ticker("AAPL")
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, "AAPL")

    result = store.read("daily_bar", underlying="NOPE")
    assert result == []


def test_full_range_read_across_all_compacted_tickers(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    _populate_hot_store(store, _TICKERS)
    for ticker in _TICKERS:
        compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read("daily_bar", provider=_PROVIDER)
    assert len(result) == 18
    assert {b.underlying for b in result} == set(_TICKERS)


def test_hot_cold_union_no_duplication(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=False)

    cold_path = compacted_file_path(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert cold_path.exists(), "cold file must exist after compaction"
    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert len(hot_files) > 0, "hot files must still exist when remove_hot=False"

    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    result_dates = {b.trade_date for b in result}
    assert len(result) == 6, f"Expected 6 rows (deduped union), got {len(result)}"
    assert result_dates == set(_DATES)


def test_hot_cold_union_values_match_original(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "MSFT"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=False)

    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    expected_by_date = {b.trade_date: b for b in bars}
    for row in result:
        expected = expected_by_date[row.trade_date]
        assert row.close == pytest.approx(expected.close)
        assert row.open == pytest.approx(expected.open)


def test_compact_ticker_is_idempotent(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert len(result) == 6
    assert {b.trade_date for b in result} == set(_DATES)


def test_compact_ticker_without_hot_files_is_noop(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "GOOG"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)
    cold_path = compacted_file_path(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    cold_mtime = cold_path.stat().st_mtime

    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)
    assert cold_path.stat().st_mtime == cold_mtime, (
        "cold file must not be re-written when no hot files exist"
    )
    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert len(result) == 6


def test_is_compacted_file_distinguishes_hot_from_cold(tmp_path: Path) -> None:
    cold = (
        tmp_path / "raw" / "daily_bar" / "provider=IBKR" / "underlying=SPX" / "data.parquet"
    )
    hot = (
        tmp_path
        / "raw"
        / "daily_bar"
        / "provider=IBKR"
        / "trade_date=2024-01-01"
        / "underlying=SPX"
        / "data.parquet"
    )
    assert is_compacted_file(cold) is True
    assert is_compacted_file(hot) is False


def test_compacted_file_path_is_inside_table_dir(tmp_path: Path) -> None:
    cold = compacted_file_path(tmp_path / "data", "daily_bar", "IBKR", "SPX")
    assert cold.name == "data.parquet"
    assert cold.parent.name == "underlying=SPX"
    assert cold.parent.parent.name == "provider=IBKR"
    assert cold.parent.parent.parent.name == "daily_bar"


def test_list_hot_files_for_ticker_excludes_cold(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=False)

    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    for f in hot_files:
        assert not is_compacted_file(f), f"cold file leaked into hot list: {f}"
    assert len(hot_files) == 6


def test_compacted_date_range_uses_column_predicate(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)

    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert hot_files == [], "hot files should be removed after remove_hot=True"

    result = store.read(
        "daily_bar",
        underlying=ticker,
        start_date=date(2024, 1, 4),
        end_date=date(2024, 1, 8),
    )
    result_dates = {b.trade_date for b in result}
    assert result_dates == {date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)}
    assert len(result) == 3


def test_compact_ticker_provider_isolation(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    ibkr_bars = _bars_for_ticker(ticker)
    saxo_bars = [dataclasses.replace(b, provider="SAXO") for b in ibkr_bars]
    store.write("daily_bar", ibkr_bars)
    store.write("daily_bar", saxo_bars)

    compact_ticker(tmp_path / "store", "IBKR", ticker, remove_hot=True)

    saxo_hot = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", "SAXO", ticker)
    assert len(saxo_hot) == 6, "SAXO hot files must survive IBKR compaction"

    result = store.read("daily_bar", underlying=ticker)
    providers = {b.provider for b in result}
    assert providers == {"IBKR", "SAXO"}
    assert len(result) == 12
