from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.core.provenance import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    ProjectedOptionAnalytics,
    StrategySignal,
)
from fastapi.testclient import TestClient

INDEX = "SX5E"
PROVIDER = "IBKR"
TENOR = "1m"
BAND = "24dp"
MULTIPLIER = 10.0
CURRENCY = "EUR"

D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)


def _ts(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0, tzinfo=UTC)


def _stamp(as_of: date) -> object:
    return stamp(
        calc_ts=_ts(as_of),
        code_version="bff-backtest-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_ts(as_of),),
    )


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
        provenance=_stamp(as_of),
    )


def _cell(*, spot: float, vol: float, mat: float, as_of: date) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_ts(as_of),
        provider=PROVIDER,
        underlying=INDEX,
        tenor_label=TENOR,
        maturity_years=mat,
        delta_band=BAND,
        target_delta=-0.24,
        log_moneyness=-0.03,
        strike=3800.0,
        forward_price=spot,
        implied_vol=vol,
        total_variance=vol * vol * mat,
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
        provenance=_stamp(as_of),
        dollar_theta=-6.0,
        dollar_rho=-2.0,
        dollar_theta_unit="per calendar day",
        dollar_rho_unit="per 1% rate move",
        surface_side="put",
    )


def _master() -> InstrumentMaster:
    key = InstrumentKey(
        INDEX, "OPT", "EUREX", CURRENCY, MULTIPLIER, "c-1", date(2026, 2, 4), 3800.0, "P"
    )
    return InstrumentMaster(
        instrument_key=key.canonical(), as_of_date=D1, instrument=key, raw_broker_payload="{}"
    )


def _seed(ctx: AppContext) -> None:
    ctx.store.write("instrument_master", [_master()])
    ctx.store.write("strategy_signals", [_signal(-0.02, D1)])
    ctx.store.write(
        "projected_option_analytics",
        [_cell(spot=3900.0, vol=0.20, mat=30 / 365, as_of=D1)],
    )
    ctx.store.write("strategy_signals", [_signal(0.05, D2)])
    ctx.store.write(
        "projected_option_analytics",
        [_cell(spot=3700.0, vol=0.28, mat=29 / 365, as_of=D2)],
    )


def _request_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "index": INDEX,
        "reference_tenor": TENOR,
        "start_date": D1.isoformat(),
        "end_date": D2.isoformat(),
        "provider": PROVIDER,
        "put_line": {
            "put_tenor": TENOR,
            "put_delta_band": BAND,
            "line_capacity": 5,
            "contracts_per_day": 1.0,
            "max_rv_minus_iv": 0.0,
        },
    }
    body.update(overrides)
    return body


def test_run_backtest_returns_full_result_shape(ctx: AppContext) -> None:
    _seed(ctx)
    with TestClient(create_app(ctx)) as client:
        payload = client.post("/api/backtest/run", json=_request_body()).json()
    assert payload["strategy_id"] == "S2-index-put-line"
    summary = payload["summary"]
    assert set(summary) == {
        "total_pnl", "total_net_pnl", "total_transaction_cost",
        "max_drawdown", "sharpe", "turnover", "worst_stress_loss",
    }
    assert summary["turnover"] == 1
    assert set(payload["cumulative_attribution"]) == {
        "delta", "gamma", "vega", "theta", "rho", "vanna", "volga",
    }
    assert len(payload["days"]) == 2
    day0 = payload["days"][0]
    assert day0["entered"] is True
    assert day0["open_contracts"] == 1.0
    assert set(day0["greeks"]) == {"delta", "gamma", "vega", "theta"}
    assert payload["days"][1]["realized_pnl"] is not None


def test_transaction_costs_are_reflected_in_net(ctx: AppContext) -> None:
    _seed(ctx)
    body = _request_body(costs={"commission_per_contract": 7.0, "slippage_rate": 0.0})
    with TestClient(create_app(ctx)) as client:
        payload = client.post("/api/backtest/run", json=body).json()
    summary = payload["summary"]
    assert summary["total_transaction_cost"] == 7.0
    assert summary["total_net_pnl"] == summary["total_pnl"] - 7.0


def test_no_banked_days_is_400(ctx: AppContext) -> None:
    _seed(ctx)
    body = _request_body(start_date="2030-01-01", end_date="2030-01-31")
    with TestClient(create_app(ctx)) as client:
        response = client.post("/api/backtest/run", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "no_banked_days"


def test_bad_put_line_config_is_400(ctx: AppContext) -> None:
    _seed(ctx)
    body = _request_body(
        put_line={
            "put_tenor": TENOR,
            "put_delta_band": "24dc",
            "line_capacity": 5,
        }
    )
    with TestClient(create_app(ctx)) as client:
        response = client.post("/api/backtest/run", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_put_line_config"


def test_inverted_window_is_400(ctx: AppContext) -> None:
    _seed(ctx)
    body = _request_body(start_date=D2.isoformat(), end_date=D1.isoformat())
    with TestClient(create_app(ctx)) as client:
        response = client.post("/api/backtest/run", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_window"
