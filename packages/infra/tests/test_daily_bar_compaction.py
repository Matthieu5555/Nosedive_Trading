"""Tests for daily_bar cold-compaction (ADR 0034 §3 / infra-daily-bar-compaction spec).

Covers the load-bearing invariants:
  (a) row-identity: compacted read == pre-compaction read (by value, for sampled tickers/windows)
  (b) date-range correctness: inclusive bounds, open bounds, empty window, unknown ticker
  (c) hot+cold union: a ticker partially compacted reads without loss or duplication
  (d) dedup: the union deduplicates on (provider, underlying, trade_date)
  (e) idempotency: compacting an already-compacted ticker is a no-op (same rows read back)

Expected values are derived independently from the fixture input, never from the code under test.
"""

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

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PROVIDER = "IBKR"
_TICKERS = ["AAPL", "MSFT", "GOOG"]

# Six sequential trading days — independently chosen, not derived from code output.
_DATES = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
]

# Per-ticker base closes — hand-computed reference values for identity checks.
_BASE_CLOSE: dict[str, float] = {
    "AAPL": 185.0,
    "MSFT": 375.0,
    "GOOG": 140.0,
}


def _bar(ticker: str, d: date, *, day_index: int = 0) -> DailyBar:
    """Build one DailyBar with independently computed OHLC.

    All four OHLC prices are derived from the ticker's base close and a per-day
    offset so that the OHLC constraint (low <= open, close <= high) is always
    satisfied and expected values are independently calculable without running
    the code under test.

    Formula (hand-derived, independent of the implementation):
        low   = base - 1.0
        high  = base + day_index + 2.0     (grows with day_index so close fits)
        open  = base - 0.5
        close = base + day_index            (close_offset = day_index)

    All four lie within [low, high] by construction.
    """
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
    """All bars for one ticker across the shared date range."""
    return [_bar(ticker, d, day_index=i) for i, d in enumerate(_DATES)]


def _populate_hot_store(
    store: ParquetStore, tickers: list[str] | None = None
) -> dict[str, list[DailyBar]]:
    """Write per-day hot partitions for each ticker; return the written bars by ticker."""
    tickers = tickers if tickers is not None else _TICKERS
    all_bars: dict[str, list[DailyBar]] = {}
    for ticker in tickers:
        bars = _bars_for_ticker(ticker)
        all_bars[ticker] = bars
        store.write("daily_bar", bars)
    return all_bars


# ---------------------------------------------------------------------------
# Section A — row identity: compacted read == pre-compaction read
# ---------------------------------------------------------------------------


def test_compact_ticker_produces_identical_rows(tmp_path: Path) -> None:
    """compact_ticker writes one file whose rows equal the pre-compaction per-day rows."""
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    expected_bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", expected_bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    # Read back via the store's public API — must equal the original bars exactly.
    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert sorted(result, key=lambda b: b.trade_date) == sorted(
        expected_bars, key=lambda b: b.trade_date
    )


def test_compact_ticker_count_matches_pre_compaction(tmp_path: Path) -> None:
    """Row count is preserved exactly after compaction."""
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "MSFT"
    bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    # Independently computed: 6 dates × 1 ticker
    assert len(result) == 6


def test_compact_ticker_content_hash_matches(tmp_path: Path) -> None:
    """Content hash (sorted rows serialized) is identical before and after compaction.

    The hash is computed independently here as a sorted JSON of (trade_date, close) tuples,
    not by re-running the code under test.
    """
    hot_store = ParquetStore(tmp_path / "store")
    ticker = "GOOG"
    bars = _bars_for_ticker(ticker)
    hot_store.write("daily_bar", bars)

    # Reference hash: derived from the input bars directly (independent of code under test).
    def _row_hash(bar_list: list[DailyBar]) -> str:
        rows = sorted((b.trade_date.isoformat(), b.close) for b in bar_list)
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

    ref_hash = _row_hash(bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)
    result = hot_store.read("daily_bar", underlying=ticker, provider=_PROVIDER)

    assert _row_hash(result) == ref_hash, (
        "Content hash changed after compaction — rows were mutated or reordered"
    )


# ---------------------------------------------------------------------------
# Section B — date-range correctness
# ---------------------------------------------------------------------------


def test_date_range_inclusive_bounds_on_compacted_store(tmp_path: Path) -> None:
    """Inclusive [start, end] date-range read returns exactly those bars on a compacted store."""
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    all_bars = _bars_for_ticker(ticker)
    store.write("daily_bar", all_bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    # Independently computed: _DATES[1] = 2024-01-03, _DATES[3] = 2024-01-05 → 3 bars
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
    """A single-day window returns exactly one bar (the one on that date)."""
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
    # Independently: _DATES[2]=2024-01-04, day_index=2 → close = 185.0 + 2 = 187.0
    assert result[0].trade_date == date(2024, 1, 4)
    assert result[0].close == pytest.approx(187.0)


def test_empty_window_on_compacted_store_returns_empty(tmp_path: Path) -> None:
    """A window with no bars in it returns an empty list (not an error)."""
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    # 2023-12-01 is before all stored dates — empty result expected.
    result = store.read(
        "daily_bar",
        underlying=ticker,
        start_date=date(2023, 12, 1),
        end_date=date(2023, 12, 31),
    )
    assert result == []


def test_unknown_ticker_on_compacted_store_returns_empty(tmp_path: Path) -> None:
    """Reading a ticker not in the store returns an empty list."""
    store = ParquetStore(tmp_path / "store")
    bars = _bars_for_ticker("AAPL")
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, "AAPL")

    result = store.read("daily_bar", underlying="NOPE")
    assert result == []


def test_full_range_read_across_all_compacted_tickers(tmp_path: Path) -> None:
    """Reading without underlying filter returns all tickers' rows after compaction."""
    store = ParquetStore(tmp_path / "store")
    _populate_hot_store(store, _TICKERS)
    for ticker in _TICKERS:
        compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read("daily_bar", provider=_PROVIDER)
    # Independently: 3 tickers × 6 dates = 18 rows total
    assert len(result) == 18
    assert {b.underlying for b in result} == set(_TICKERS)


# ---------------------------------------------------------------------------
# Section C — hot + cold union: partial compaction (remove_hot=False)
# ---------------------------------------------------------------------------


def test_hot_cold_union_no_duplication(tmp_path: Path) -> None:
    """A ticker with BOTH a cold file AND remaining hot files reads without duplication.

    Scenario: compact but keep hot files (remove_hot=False simulates the window before
    the migration script archives them). The union must return exactly 6 rows.
    """
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=False)

    # Verify hot files still exist alongside the cold file.
    cold_path = compacted_file_path(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert cold_path.exists(), "cold file must exist after compaction"
    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert len(hot_files) > 0, "hot files must still exist when remove_hot=False"

    # Read back — must be exactly 6 unique rows (the union deduplicates on PK).
    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    result_dates = {b.trade_date for b in result}
    assert len(result) == 6, f"Expected 6 rows (deduped union), got {len(result)}"
    assert result_dates == set(_DATES)


def test_hot_cold_union_values_match_original(tmp_path: Path) -> None:
    """The union of hot+cold returns the exact same values as the original bars."""
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


# ---------------------------------------------------------------------------
# Section D — idempotency
# ---------------------------------------------------------------------------


def test_compact_ticker_is_idempotent(tmp_path: Path) -> None:
    """Compacting the same ticker twice yields the same rows — no duplication, no error."""
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    compact_ticker(tmp_path / "store", _PROVIDER, ticker)
    # Second compact: cold file already exists, hot files already removed.
    compact_ticker(tmp_path / "store", _PROVIDER, ticker)

    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    # Independently: 6 dates, no duplication on idempotent re-compact
    assert len(result) == 6
    assert {b.trade_date for b in result} == set(_DATES)


def test_compact_ticker_without_hot_files_is_noop(tmp_path: Path) -> None:
    """Compacting a ticker that has no hot files (only a cold file) is a silent no-op."""
    store = ParquetStore(tmp_path / "store")
    ticker = "GOOG"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)

    # First compact: removes hot files, writes cold file.
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)
    cold_path = compacted_file_path(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    cold_mtime = cold_path.stat().st_mtime

    # Second compact: no hot files → cold file must be untouched.
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)
    assert cold_path.stat().st_mtime == cold_mtime, (
        "cold file must not be re-written when no hot files exist"
    )
    result = store.read("daily_bar", underlying=ticker, provider=_PROVIDER)
    assert len(result) == 6


# ---------------------------------------------------------------------------
# Section E — helper function contracts
# ---------------------------------------------------------------------------


def test_is_compacted_file_distinguishes_hot_from_cold(tmp_path: Path) -> None:
    """is_compacted_file returns True only for cold (no trade_date segment) paths."""
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
    """compacted_file_path returns the expected cold path structure."""
    cold = compacted_file_path(tmp_path / "data", "daily_bar", "IBKR", "SPX")
    # Expected: data/raw/daily_bar/provider=IBKR/underlying=SPX/data.parquet
    assert cold.name == "data.parquet"
    assert cold.parent.name == "underlying=SPX"
    assert cold.parent.parent.name == "provider=IBKR"
    assert cold.parent.parent.parent.name == "daily_bar"


def test_list_hot_files_for_ticker_excludes_cold(tmp_path: Path) -> None:
    """list_hot_files_for_ticker returns only per-day hot files, never the cold file."""
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=False)

    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    # All hot files must live under a trade_date= segment (not directly under provider=)
    for f in hot_files:
        assert not is_compacted_file(f), f"cold file leaked into hot list: {f}"
    # Must have found the 6 hot files (one per date)
    assert len(hot_files) == 6


# ---------------------------------------------------------------------------
# Section F — date-range on compacted file uses DuckDB predicate pushdown
# ---------------------------------------------------------------------------


def test_compacted_date_range_uses_column_predicate(tmp_path: Path) -> None:
    """After full compaction (no hot files remain), date-range read still works correctly.

    This verifies the DuckDB WHERE trade_date BETWEEN ? AND ? path on the cold file.
    """
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    bars = _bars_for_ticker(ticker)
    store.write("daily_bar", bars)
    compact_ticker(tmp_path / "store", _PROVIDER, ticker, remove_hot=True)

    # Verify no hot files remain.
    hot_files = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", _PROVIDER, ticker)
    assert hot_files == [], "hot files should be removed after remove_hot=True"

    # Date range: _DATES[2..4] = 2024-01-04 to 2024-01-08 (inclusive)
    result = store.read(
        "daily_bar",
        underlying=ticker,
        start_date=date(2024, 1, 4),
        end_date=date(2024, 1, 8),
    )
    # Independently: dates 2024-01-04, 2024-01-05, 2024-01-08 → 3 rows
    result_dates = {b.trade_date for b in result}
    assert result_dates == {date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)}
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Section G — multi-provider correctness (forward-proofing)
# ---------------------------------------------------------------------------


def test_compact_ticker_provider_isolation(tmp_path: Path) -> None:
    """Compacting IBKR does not affect a second provider's hot files."""
    store = ParquetStore(tmp_path / "store")
    ticker = "AAPL"
    ibkr_bars = _bars_for_ticker(ticker)
    # Build a second provider's bars by patching the provider field.
    saxo_bars = [dataclasses.replace(b, provider="SAXO") for b in ibkr_bars]
    store.write("daily_bar", ibkr_bars)
    store.write("daily_bar", saxo_bars)

    # Compact only IBKR.
    compact_ticker(tmp_path / "store", "IBKR", ticker, remove_hot=True)

    # SAXO hot files must still be present (cold file only covers IBKR).
    saxo_hot = list_hot_files_for_ticker(tmp_path / "store", "daily_bar", "SAXO", ticker)
    assert len(saxo_hot) == 6, "SAXO hot files must survive IBKR compaction"

    # Cross-provider read (no provider filter) returns both providers' rows.
    result = store.read("daily_bar", underlying=ticker)
    providers = {b.provider for b in result}
    assert providers == {"IBKR", "SAXO"}
    assert len(result) == 12  # 6 dates × 2 providers, no duplication
