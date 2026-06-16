from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import ProjectedOptionAnalytics, StrategySignal
from algotrading.infra.pricing import price
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.risk.valuation import pricing_state_for
from algotrading.infra.storage import ParquetStore
from algotrading.strategy import SignalKind
from algotrading.strategy.backtest import (
    BacktestConfig,
    StoreBackedBacktestData,
    TransactionCostModel,
    run_backtest,
)
from algotrading.strategy.s2_put_line import PutLineConfig, PutLineStrategy

PROVIDER = "IBKR"
INDEX = "SX5E"
TENOR = "1m"
BAND = "24dp"
SIDE = "put"
MULTIPLIER = 10.0
CURRENCY = "EUR"
_TS = datetime(2026, 1, 5, 16, 0, 0, tzinfo=UTC)

D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)


def _stamp() -> object:
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-strategy-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _ts(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0, tzinfo=UTC)


def _signal(value: float, as_of: date) -> StrategySignal:
    return StrategySignal(
        snapshot_ts=_ts(as_of),
        provider=PROVIDER,
        underlying=INDEX,
        signal_kind="iv_vs_realized",
        subject=INDEX,
        tenor_label=TENOR,
        value=value,
        source_snapshot_ts=_ts(as_of),
        provenance=_stamp(),
    )


def _put_cell(
    *, spot: float, vol: float, maturity_years: float, as_of: date
) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_ts(as_of),
        provider=PROVIDER,
        underlying=INDEX,
        tenor_label=TENOR,
        maturity_years=maturity_years,
        delta_band=BAND,
        target_delta=-0.24,
        log_moneyness=-0.03,
        strike=3800.0,
        forward_price=spot,
        implied_vol=vol,
        total_variance=vol * vol * maturity_years,
        price=40.0,
        delta=-0.24,
        gamma=0.01,
        vega=0.30,
        theta=-0.06,
        rho=-0.02,
        dollar_delta=-2400.0,
        dollar_gamma=0.01,
        dollar_vega=0.30,
        dollar_delta_unit="per $1 underlying move",
        dollar_gamma_unit="per 1% underlying move",
        dollar_vega_unit="per 1 vol point",
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_ts(as_of),
        provenance=_stamp(),
        dollar_theta=-6.0,
        dollar_rho=-2.0,
        dollar_theta_unit="per calendar day",
        dollar_rho_unit="per 1% rate move",
        surface_side=SIDE,
    )


def _seeded_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    store.write("strategy_signals", [_signal(-0.02, D1)])
    store.write(
        "projected_option_analytics",
        [_put_cell(spot=3900.0, vol=0.20, maturity_years=30 / 365, as_of=D1)],
    )
    return store


def _two_day_store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(tmp_path)
    store.write("strategy_signals", [_signal(-0.02, D1)])
    store.write(
        "projected_option_analytics",
        [_put_cell(spot=3900.0, vol=0.20, maturity_years=30 / 365, as_of=D1)],
    )
    store.write("strategy_signals", [_signal(0.05, D2)])
    store.write(
        "projected_option_analytics",
        [_put_cell(spot=3700.0, vol=0.28, maturity_years=29 / 365, as_of=D2)],
    )
    return store


def _strategy(capacity: int = 5) -> PutLineStrategy:
    return PutLineStrategy(
        PutLineConfig(
            index=INDEX, put_tenor=TENOR, put_delta_band=BAND,
            line_capacity=capacity, contracts_per_day=1.0, max_rv_minus_iv=0.0,
        )
    )


def _data(store: ParquetStore) -> StoreBackedBacktestData:
    return StoreBackedBacktestData(
        store=store, index=INDEX, reference_tenor=TENOR,
        multiplier=MULTIPLIER, currency=CURRENCY, provider=PROVIDER,
    )


def test_signals_bridge_surfaces_the_iv_vs_realized_reading(tmp_path: Path) -> None:
    data = _data(_seeded_store(tmp_path))
    snapshot = data.signals(D1)
    reading = snapshot.latest(SignalKind.IV_VS_REALIZED, subject=INDEX)
    assert reading is not None
    assert reading.value == pytest.approx(-0.02)


def test_concretize_leg_pins_strike_right_and_expiry_from_the_cell(tmp_path: Path) -> None:
    data = _data(_seeded_store(tmp_path))
    leg = _strategy().construct(D1, basket_id="seed").legs[0]
    held = data.concretize_leg(leg, D1)
    assert held is not None
    assert held.contract_key == "SX5E|OPT|P|3800.0000"
    assert held.quantity == pytest.approx(-1.0)
    assert held.expiry == D1 + timedelta(days=round(30 / 365 * 365.0))


def test_valuation_reconstructs_the_input_from_the_cell_row(tmp_path: Path) -> None:
    data = _data(_seeded_store(tmp_path))
    leg = _strategy().construct(D1, basket_id="seed").legs[0]
    held = data.concretize_leg(leg, D1)
    assert held is not None
    valuation = data.valuation(held, D1)
    assert valuation is not None
    assert valuation.strike == pytest.approx(3800.0)
    assert valuation.spot == pytest.approx(3900.0)
    assert valuation.volatility == pytest.approx(0.20)
    assert valuation.option_right == "P"
    assert valuation.multiplier == pytest.approx(MULTIPLIER)
    assert valuation.currency == CURRENCY


def test_missing_cell_returns_none(tmp_path: Path) -> None:
    data = _data(ParquetStore(tmp_path))
    leg = _strategy().construct(D1, basket_id="seed").legs[0]
    assert data.concretize_leg(leg, D1) is None


def test_run_backtest_over_the_store_books_one_put_and_attributes_the_move(
    tmp_path: Path,
) -> None:
    store = _two_day_store(tmp_path)
    data = _data(store)
    result = run_backtest(
        _strategy(), data, dates=[D1, D2],
        config=BacktestConfig(
            "bt", AttributionConfig(version="t"), stress_grid=(),
        ),
    )
    assert result.days[0].entered is True
    assert result.days[0].open_contracts == 1.0
    start = data.valuation(
        data.concretize_leg(_strategy().construct(D1, basket_id="s").legs[0], D1),  # type: ignore[arg-type]
        D1,
    )
    end = data.valuation(
        data.concretize_leg(_strategy().construct(D1, basket_id="s").legs[0], D1),  # type: ignore[arg-type]
        D2,
    )
    assert start is not None and end is not None
    expected = (
        price(pricing_state_for(end)).price - price(pricing_state_for(start)).price
    ) * MULTIPLIER * -1.0
    assert result.days[1].realized_pnl == pytest.approx(expected, rel=1e-9)
    assert result.days[1].realized_pnl is not None and result.days[1].realized_pnl < 0.0


def test_transaction_cost_lowers_net_pnl_below_gross(tmp_path: Path) -> None:
    store = _two_day_store(tmp_path)
    data = _data(store)
    result = run_backtest(
        _strategy(), data, dates=[D1, D2],
        config=BacktestConfig(
            "bt", AttributionConfig(version="t"), stress_grid=(),
            costs=TransactionCostModel(commission_per_contract=5.0, slippage_rate=0.0),
        ),
    )
    assert result.summary.total_transaction_cost == pytest.approx(5.0)
    assert result.summary.total_net_pnl == pytest.approx(
        result.summary.total_pnl - 5.0, rel=1e-9
    )


class _RecordingStore:

    def __init__(self, inner: ParquetStore) -> None:
        self._inner = inner
        self.trade_dates: list[date] = []

    def read(self, table: str, *, trade_date: date | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        if trade_date is not None:
            self.trade_dates.append(trade_date)
        return self._inner.read(table, trade_date=trade_date, **kwargs)  # type: ignore[arg-type]


def test_store_path_reads_only_the_current_as_of_no_lookahead(tmp_path: Path) -> None:
    recording = _RecordingStore(_two_day_store(tmp_path))
    data = StoreBackedBacktestData(
        store=recording,  # type: ignore[arg-type]
        index=INDEX, reference_tenor=TENOR,
        multiplier=MULTIPLIER, currency=CURRENCY, provider=PROVIDER,
    )
    dates = [D1, D2]
    run_backtest(
        _strategy(), data, dates=dates,
        config=BacktestConfig("bt", AttributionConfig(version="t"), stress_grid=()),
    )
    assert set(recording.trade_dates) <= set(dates)
    distinct: list[date] = []
    for d in recording.trade_dates:
        if not distinct or d != distinct[-1]:
            distinct.append(d)
    assert distinct == dates
