"""Store-backed signal-set orchestration — as-of reads, look-ahead gate, labelled absence.

Seeds a temporary ParquetStore with membership, per-name + index combined-surface ATM grids,
their trailing history, and daily bars, then runs :func:`persist_signal_set` and checks the
persisted readings against independently derived values. Expected ρ̄ comes from the forward
basket identity (set so it is exactly 0.5); expected realized vol from ``statistics.stdev``.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.infra.signals import SignalConfig, persist_signal_set
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import MembershipChange, ingest_membership_changes
from fixtures.records import make_record

PROVIDER = "IBKR"
INDEX = "SX5E"
KNOWN = date(2020, 1, 1)
VENDOR = "test-vendor"

D0 = date(2026, 5, 29)  # the as-of day
DM1 = date(2026, 5, 28)
DM2 = date(2026, 5, 27)
FUTURE = date(2026, 6, 3)  # must never be read for an as-of of D0

# Index 3m IV chosen so the basket identity gives rho_bar = 0.5 exactly:
#   own = 0.6^2*0.20^2 + 0.4^2*0.30^2 = 0.0288 ; cross = 0.24^2 - 0.0288 = 0.0288
#   index_var = own + 0.5*cross = 0.0432 -> index_vol = sqrt(0.0432)
INDEX_3M_IV = math.sqrt(0.0432)
AAA_CLOSES = [100.0, 110.0, 99.0, 103.0]


def _analytics(underlying: str, day: date, tenor: str, iv: float) -> object:
    ts = datetime(day.year, day.month, day.day, 15, 30, tzinfo=UTC)
    return make_record(
        "projected_option_analytics",
        provider=PROVIDER,
        underlying=underlying,
        snapshot_ts=ts,
        source_snapshot_ts=ts,
        tenor_label=tenor,
        delta_band="atm",
        surface_side="combined",
        implied_vol=iv,
    )


def _bar(underlying: str, day: date, close: float) -> object:
    # A flat OHLC bar (open=high=low=close): only the close drives realized vol, and the OHLC
    # validator requires open/close within [low, high].
    return make_record(
        "daily_bar",
        provider=PROVIDER,
        underlying=underlying,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
    )


def _seed(store: ParquetStore) -> None:
    ingest_membership_changes(
        store,
        (
            MembershipChange(INDEX, "AAA", date(2020, 1, 1), None, KNOWN, VENDOR, 0.6),
            MembershipChange(INDEX, "BBB", date(2020, 1, 1), None, KNOWN, VENDOR, 0.4),
        ),
    )
    rows = [
        # D0 combined-surface ATM grid: index + both constituents across three tenors.
        _analytics(INDEX, D0, "1m", 0.24),
        _analytics(INDEX, D0, "3m", INDEX_3M_IV),
        _analytics(INDEX, D0, "6m", 0.27),
        _analytics("AAA", D0, "1m", 0.22),
        _analytics("AAA", D0, "3m", 0.20),
        _analytics("AAA", D0, "6m", 0.25),
        _analytics("BBB", D0, "1m", 0.28),
        _analytics("BBB", D0, "3m", 0.30),
        _analytics("BBB", D0, "6m", 0.31),
        # AAA 3m history for IV-rank: window becomes [0.10, 0.30, 0.20] -> rank(0.20) = 0.5.
        _analytics("AAA", DM2, "3m", 0.10),
        _analytics("AAA", DM1, "3m", 0.30),
        # A future grid that would move rho_bar and the IV-rank window if it leaked into D0.
        _analytics(INDEX, FUTURE, "3m", 0.50),
        _analytics("AAA", FUTURE, "3m", 0.99),
    ]
    store.write("projected_option_analytics", rows)
    store.write(
        "daily_bar",
        [_bar("AAA", day, close) for day, close in zip([DM2, DM1, D0], AAA_CLOSES[1:], strict=False)]
        + [_bar("AAA", date(2026, 5, 26), AAA_CLOSES[0])],
    )


def _persist(store: ParquetStore) -> dict[tuple[str, str, str], float]:
    config = SignalConfig(
        index=INDEX,
        provider=PROVIDER,
        reference_tenor="3m",
        term_slope_front="1m",
        term_slope_back="6m",
        iv_history_lookback_days=30,
        realized_vol_lookback_days=30,
    )
    calc_ts = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
    rows = persist_signal_set(store, config, D0, calc_ts=calc_ts, config_hashes={"signals": "h0"})
    return {(r.signal_kind, r.subject, r.tenor_label): r.value for r in rows}


def test_persisted_signal_set(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    signals = _persist(store)

    # rho_bar at 3m: independent forward-identity value, unaffected by the future 0.50 grid.
    assert signals[("implied_correlation", INDEX, "3m")] == pytest.approx(0.5)

    # IV-rank on AAA: window [0.10, 0.30, 0.20] -> (0.20-0.10)/(0.30-0.10) = 0.5.
    assert signals[("iv_rank", "AAA", "3m")] == pytest.approx(0.5)

    # RV−IV on AAA: independent realized vol minus the AAA 3m implied (0.20).
    log_returns = [math.log(b / a) for a, b in zip(AAA_CLOSES, AAA_CLOSES[1:], strict=False)]
    realized = statistics.stdev(log_returns) * math.sqrt(252.0)
    assert signals[("iv_vs_realized", "AAA", "3m")] == pytest.approx(realized - 0.20)

    # Term slope on AAA across 1m->6m: 0.25 - 0.22 = 0.03.
    assert signals[("term_structure_slope", "AAA", "1m:6m")] == pytest.approx(0.03)


def test_persisted_rows_are_read_back_identically(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    written = _persist(store)
    read_back = {
        (r.signal_kind, r.subject, r.tenor_label): r.value
        for r in store.read("strategy_signals", trade_date=D0, underlying=INDEX, provider=PROVIDER)
    }
    assert read_back == pytest.approx(written)


def test_unanswerable_signal_is_omitted_not_fabricated(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    signals = _persist(store)
    # BBB has a single 3m IV point (flat window) and no daily bars: its IV-rank and RV−IV are
    # undefined, so they are absent — a labelled absence, never a fabricated 0.
    assert ("iv_rank", "BBB", "3m") not in signals
    assert ("iv_vs_realized", "BBB", "3m") not in signals


def test_no_surface_day_writes_nothing(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    config = SignalConfig(
        index=INDEX,
        provider=PROVIDER,
        reference_tenor="3m",
        term_slope_front="1m",
        term_slope_back="6m",
        iv_history_lookback_days=30,
        realized_vol_lookback_days=30,
    )
    calc_ts = datetime(2026, 1, 1, 16, 0, tzinfo=UTC)
    empty_day = date(2026, 1, 1)
    rows = persist_signal_set(store, config, empty_day, calc_ts=calc_ts, config_hashes={"signals": "h0"})
    assert rows == ()
    assert store.read("strategy_signals", trade_date=empty_day, underlying=INDEX, provider=PROVIDER) == []
