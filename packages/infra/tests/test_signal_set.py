from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import SignalEntryConfig
from algotrading.infra.signals import SignalConfig, persist_signal_set, signal_config_for
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import MembershipChange, ingest_membership_changes
from fixtures.records import make_record

PROVIDER = "IBKR"
INDEX = "SX5E"
KNOWN = date(2020, 1, 1)
VENDOR = "test-vendor"

D0 = date(2026, 5, 29)
DM1 = date(2026, 5, 28)
DM2 = date(2026, 5, 27)
FUTURE = date(2026, 6, 3)

# Index 3m implied vol, kept below the weighted constituent reach so the realized-vol ρ̄ lands in
# a sane band. ρ̄ itself is asserted from an independent closed-form derivation, not this constant.
INDEX_3M_IV = 0.10
AAA_CLOSES = [100.0, 101.0, 100.5, 101.0]
BBB_CLOSES = [50.0, 50.7, 50.3, 50.6]


def _realized_vol(closes: list[float]) -> float:
    log_returns = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    return statistics.stdev(log_returns) * math.sqrt(252.0)


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
            MembershipChange(INDEX, "AAA", date(2020, 1, 1), None, KNOWN, VENDOR, 0.5),
            MembershipChange(INDEX, "BBB", date(2020, 1, 1), None, KNOWN, VENDOR, 0.3),
            MembershipChange(INDEX, "CCC", date(2020, 1, 1), None, KNOWN, VENDOR, 0.2),
        ),
    )
    rows = [
        _analytics(INDEX, D0, "1m", 0.24),
        _analytics(INDEX, D0, "3m", INDEX_3M_IV),
        _analytics(INDEX, D0, "6m", 0.27),
        _analytics("AAA", D0, "1m", 0.22),
        _analytics("AAA", D0, "3m", 0.20),
        _analytics("AAA", D0, "6m", 0.25),
        _analytics("BBB", D0, "1m", 0.28),
        _analytics("BBB", D0, "3m", 0.30),
        _analytics("BBB", D0, "6m", 0.31),
        # CCC carries a reference surface but no daily bars and no IV history — the
        # "unanswerable" name whose realized-vol / IV-rank signals must be omitted.
        _analytics("CCC", D0, "3m", 0.26),
        _analytics("AAA", DM2, "3m", 0.10),
        _analytics("AAA", DM1, "3m", 0.30),
        _analytics(INDEX, FUTURE, "3m", 0.50),
        _analytics("AAA", FUTURE, "3m", 0.99),
    ]
    store.write("projected_option_analytics", rows)
    store.write(
        "daily_bar",
        [_bar("AAA", day, c) for day, c in zip([DM2, DM1, D0], AAA_CLOSES[1:], strict=False)]
        + [_bar("AAA", date(2026, 5, 26), AAA_CLOSES[0])]
        + [_bar("BBB", day, c) for day, c in zip([DM2, DM1, D0], BBB_CLOSES[1:], strict=False)]
        + [_bar("BBB", date(2026, 5, 26), BBB_CLOSES[0])],
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

    # ρ̄ (ADR 0051) is solved from the constituents' REALIZED vols (every name with bars), with
    # the index's implied ATM vol as the index leg — independently derived here via Eq. 23's
    # closed form. CCC has no bars, so only AAA and BBB enter the basket. The basket weights are
    # NORMALIZED to sum to 1.0 before the solve: membership weights are stored as percentages, and
    # feeding them raw inflates the variance terms and drives ρ̄ negative. AAA=0.5, BBB=0.3 enter,
    # so the fractional weights are 0.5/0.8 and 0.3/0.8.
    realized_aaa = _realized_vol(AAA_CLOSES)
    realized_bbb = _realized_vol(BBB_CLOSES)
    total = 0.5 + 0.3
    w_aaa, w_bbb = 0.5 / total, 0.3 / total
    own = (w_aaa * realized_aaa) ** 2 + (w_bbb * realized_bbb) ** 2
    cross = (w_aaa * realized_aaa + w_bbb * realized_bbb) ** 2 - own
    expected_rho = (INDEX_3M_IV**2 - own) / cross
    rho_bar = signals[("implied_correlation", INDEX, "3m")]
    assert rho_bar == pytest.approx(expected_rho)
    assert 0.0 < rho_bar < 1.0

    assert signals[("iv_rank", "AAA", "3m")] == pytest.approx(0.5)

    assert signals[("iv_vs_realized", "AAA", "3m")] == pytest.approx(realized_aaa - 0.20)

    assert signals[("term_structure_slope", "AAA", "1m:6m")] == pytest.approx(0.03)


def test_iv_rank_carries_honest_lookback_companions(tmp_path: Path) -> None:
    # IV rank must not silently imply a 365-day window. Alongside every rank we emit the real
    # sample count and the calendar-day span it was computed over, so the frontend can label it
    # truthfully ("rank of the last 3 days, spanning 2 days") instead of "top of the year".
    # AAA has three banked 3m observations (2026-05-27, -28, -29): n=3, span=2 days.
    store = ParquetStore(tmp_path)
    _seed(store)
    signals = _persist(store)

    assert signals[("iv_rank", "AAA", "3m")] == pytest.approx(0.5)
    assert signals[("iv_rank_n_observations", "AAA", "3m")] == pytest.approx(3.0)
    assert signals[("iv_rank_window_days", "AAA", "3m")] == pytest.approx(2.0)


def test_honesty_companions_only_accompany_an_emitted_rank(tmp_path: Path) -> None:
    # The companions never appear without the rank they explain. CCC has no IV history, so it has
    # neither an iv_rank nor any companion rows — no fabricated "0 of 0 days" noise.
    store = ParquetStore(tmp_path)
    _seed(store)
    signals = _persist(store)

    assert ("iv_rank", "CCC", "3m") not in signals
    assert ("iv_rank_n_observations", "CCC", "3m") not in signals
    assert ("iv_rank_window_days", "CCC", "3m") not in signals


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
    assert ("iv_rank", "CCC", "3m") not in signals
    assert ("iv_vs_realized", "CCC", "3m") not in signals


def test_signal_config_for_maps_the_typed_universe_block() -> None:
    entry = SignalEntryConfig(
        version="sig-1",
        reference_tenor="3m",
        term_slope_front="1m",
        term_slope_back="12m",
        iv_history_lookback_days=200,
        realized_vol_lookback_days=21,
        periods_per_year=260.0,
        basket_size=7,
    )
    built = signal_config_for(entry, index="SX5E", provider="IBKR")
    assert built == SignalConfig(
        index="SX5E",
        provider="IBKR",
        reference_tenor="3m",
        term_slope_front="1m",
        term_slope_back="12m",
        iv_history_lookback_days=200,
        realized_vol_lookback_days=21,
        periods_per_year=260.0,
        basket_size=7,
    )


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
