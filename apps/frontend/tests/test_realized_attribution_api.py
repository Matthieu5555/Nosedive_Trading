"""Contract + correctness tests for GET /api/attribution/realized.

The crux this guards: the realized explain must anchor to a FIXED expiry whose maturity rolls
down with calendar days, so ``d_time`` is real and theta does not vanish. A synthetic two-date
fixed-expiry chain is seeded; the endpoint must (a) reconcile approx + residual == full_reprice
to a small residual, (b) emit a real (negative) theta with a non-zero ``d_time``, and (c) carry
the full seven-term, dollar-labelled waterfall.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

UNDERLYING = "SX5E"
EXPIRY = date(2026, 9, 18)
D0 = date(2026, 6, 15)
D1 = date(2026, 6, 16)
MULTIPLIER = 100  # encoded in the iv_points contract_key

# A flat synthetic chain: same vol every strike/side, forward rising 6300 -> 6340 between the
# two dates. Strikes straddle the forward so the ATM straddle resolves to a strike present on
# both days as both a call and a put.
STRIKES = (6200.0, 6250.0, 6275.0, 6300.0, 6325.0, 6350.0, 6400.0)
FORWARD = {D0: 6300.0, D1: 6340.0}
IV = {D0: 0.16, D1: 0.165}


def _ts(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 15, 30, tzinfo=UTC)


def _stamp(as_of: date) -> object:
    return stamp(
        calc_ts=_ts(as_of),
        code_version="realized-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("iv_points", UNDERLYING, as_of.isoformat()),),
        source_timestamps=(_ts(as_of),),
    )


def _iv_point(as_of: date, strike: float, right: str) -> IvPoint:
    forward = FORWARD[as_of]
    log_moneyness = math.log(strike / forward)
    iv = IV[as_of]
    conid = int(strike)
    key = (
        f"{UNDERLYING}|OPT|EUREX|EUR|{MULTIPLIER}|{conid}|"
        f"{EXPIRY.isoformat()}|{int(strike)}|{right}"
    )
    return IvPoint(
        snapshot_ts=_ts(as_of),
        contract_key=key,
        implied_vol=iv,
        log_moneyness=log_moneyness,
        total_variance=iv * iv * ((EXPIRY - as_of).days / 365.0),
        solver_version="test",
        diagnostics=IvDiagnostics(converged=True, iterations=1, residual=0.0, status="ok"),
        source_snapshot_ts=_ts(as_of),
        provenance=_stamp(as_of),
    )


def _seed(root: Path) -> None:
    store = ParquetStore(root)
    for as_of in (D0, D1):
        rows = [
            _iv_point(as_of, strike, right)
            for strike in STRIKES
            for right in ("C", "P")
        ]
        store.write("iv_points", rows)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "data"
    _seed(root)
    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=UNDERLYING,
    )
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


def _get(client: TestClient) -> dict:
    response = client.get(
        "/api/attribution/realized",
        params={"start_date": D0.isoformat(), "end_date": D1.isoformat()},
    )
    assert response.status_code == 200
    return response.json()


def test_realized_returns_one_step_per_consecutive_close(client: TestClient) -> None:
    body = _get(client)
    assert body["found"] is True
    assert body["underlying"] == UNDERLYING
    assert body["dates"] == [D0.isoformat(), D1.isoformat()]
    assert len(body["steps"]) == 1
    step = body["steps"][0]
    assert step["start_date"] == D0.isoformat()
    assert step["end_date"] == D1.isoformat()


def test_realized_residual_round_trip_is_small_and_honest(client: TestClient) -> None:
    # The decomposition must reconcile: approx + residual == full_reprice, and the residual
    # must clear the engine's tolerance (this is what blows up if you track a constant-maturity
    # cell instead of a fixed expiry).
    step = _get(client)["steps"][0]
    approx = step["approx_pnl"]["dollars"]
    residual = step["residual"]["dollars"]
    full = step["full_reprice_pnl"]["dollars"]
    assert approx + residual == pytest.approx(full, abs=1e-6)
    bound = max(1.0, 0.05 * abs(full))
    assert abs(residual) <= bound
    assert step["verdict"]["within_tolerance"] is True


def test_realized_theta_is_real_because_maturity_rolls_down(client: TestClient) -> None:
    # The correctness crux: a fixed expiry one calendar day closer means d_time = 1/365 yr and a
    # real, negative theta. A constant-maturity grid cell would give d_time == 0 and theta == 0.
    step = _get(client)["steps"][0]
    assert step["move"]["d_time"] == pytest.approx(1.0 / 365.0, rel=1e-9)
    terms = {t["name"]: t["dollars"] for t in step["terms"]}
    assert terms["Theta"] < 0.0
    assert abs(terms["Theta"]) > 1.0


def test_realized_waterfall_carries_seven_dollar_labelled_terms(client: TestClient) -> None:
    step = _get(client)["steps"][0]
    assert [t["name"] for t in step["terms"]] == [
        "Delta",
        "Gamma",
        "Vega",
        "Theta",
        "Rho",
        "Vanna",
        "Volga",
    ]
    for term in step["terms"]:
        assert "$" in term["unit"]
    assert "$" in step["residual"]["unit"]


def test_realized_move_records_the_spot_and_vol_change(client: TestClient) -> None:
    step = _get(client)["steps"][0]
    assert step["move"]["d_spot"] == pytest.approx(FORWARD[D1] - FORWARD[D0])
    assert step["move"]["d_vol"] == pytest.approx(IV[D1] - IV[D0])


def test_realized_empty_window_is_labelled_not_500(client: TestClient) -> None:
    response = client.get(
        "/api/attribution/realized",
        params={"start_date": D0.isoformat(), "end_date": D0.isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["steps"] == []


def test_realized_bad_window_is_labelled_400(client: TestClient) -> None:
    response = client.get(
        "/api/attribution/realized",
        params={"start_date": D1.isoformat(), "end_date": D0.isoformat()},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_window"


def test_realized_bad_date_is_labelled_400(client: TestClient) -> None:
    response = client.get(
        "/api/attribution/realized", params={"start_date": "not-a-date"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_trade_date"
