from __future__ import annotations

import math
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    ProjectedOptionAnalytics,
)
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE = date(2026, 6, 5)
_CONFIGS = Path("configs").resolve()


def _row() -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider="ibkr",
        underlying="AAA",
        tenor_label="1m",
        maturity_years=1.0 / 12.0,
        delta_band="atm",
        target_delta=0.30,
        log_moneyness=0.0,
        strike=100.0,
        forward_price=100.0,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 / 12.0,
        price=2.30,
        delta=0.5,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=500.0,
        dollar_gamma=0.02,
        dollar_vega=0.31,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=stamp(
            calc_ts=_TS,
            code_version="algotrading-frontend-0.1.0",
            config_hashes={"cfg": "cfg"},
            source_records=(source_ref("raw_market_events", "s", "e"),),
            source_timestamps=(_TS,),
        ),
    )


def _master() -> InstrumentMaster:
    key = InstrumentKey(
        underlying_symbol="AAA",
        security_type="OPT",
        exchange="XEUR",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id="aaa-opt",
        expiry=date(2026, 7, 17),
        strike=100.0,
        option_right="C",
    )
    return InstrumentMaster(
        instrument_key=key.canonical(),
        as_of_date=_TRADE,
        instrument=key,
        raw_broker_payload="{}",
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [_row()])
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


@pytest.fixture
def resolving_client(tmp_path: Path) -> Iterator[TestClient]:
    # Seeds an instrument_master so the option leg resolves into a repriced line, which is
    # what lights up the on-demand rate sweep (an empty leg set yields an empty sweep).
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [_row()])
    store.write("instrument_master", [_master()])
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


def _configs_without_rate_shocks(tmp_path: Path) -> Path:
    configs_dir = tmp_path / "configs"
    shutil.copytree(_CONFIGS, configs_dir)
    scenarios = configs_dir / "scenarios.yaml"
    text = scenarios.read_text()
    assert "rate_shocks: [-0.0025, 0.0, 0.0025]" in text
    scenarios.write_text(text.replace("rate_shocks: [-0.0025, 0.0, 0.0025]", "rate_shocks: []"))
    return configs_dir


@pytest.fixture
def no_rate_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [_row()])
    store.write("instrument_master", [_master()])
    ctx = AppContext(
        store_root=store_root,
        configs_dir=_configs_without_rate_shocks(tmp_path),
        store=store,
    )
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


def _basket_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "basket_id": "b1",
        "trade_date": "2026-06-05",
        "underlying": "AAA",
        "legs": [
            {
                "instrument_kind": "option",
                "side": "long",
                "quantity": 1.0,
                "underlying": "AAA",
                "tenor_label": "1m",
                "delta_band": "atm",
            }
        ],
    }
    body.update(overrides)
    return body


def test_malformed_json_is_400(client: TestClient):
    response = client.post("/api/basket/scenarios", content=b"not json")
    assert response.status_code == 400
    assert response.json()["error"] == "bad_basket"


def test_malformed_basket_is_400(client: TestClient):
    response = client.post("/api/basket/scenarios", json=_basket_body(trade_date="not-a-date"))
    assert response.status_code == 400
    assert response.json()["error"] == "bad_basket"


def test_empty_trade_date_resolves_to_the_latest_banked_day(client: TestClient):
    response = client.post("/api/basket/scenarios", json=_basket_body(trade_date=""))
    assert response.status_code == 200
    assert response.json()["trade_date"] == _TRADE.isoformat()


def test_valid_request_returns_surface_payload(client: TestClient):
    response = client.post("/api/basket/scenarios", json=_basket_body())
    assert response.status_code == 200
    payload = response.json()
    surface = payload["surface"]
    assert surface["spot_shock"] and surface["vol_shock"]
    assert len(surface["scenario_pnl"]) == len(surface["spot_shock"])
    assert surface["unit"] == "$ (full-reprice PnL)"
    assert surface["n_cells"] == len(surface["spot_shock"]) * len(surface["vol_shock"])
    assert "worst_case" in payload and surface["scenario_version"]
    assert payload["n_gaps"] == 1
    assert payload["gaps"][0]["reason"] == "no_instrument_master"
    ci = surface["spot_shock"].index(0.0)
    cj = surface["vol_shock"].index(0.0)
    assert math.isclose(surface["scenario_pnl"][ci][cj], 0.0, abs_tol=1e-6)


def test_unresolved_leg_carries_no_rate_sweep(client: TestClient):
    # No instrument_master → the leg is an unresolved gap → no repriced legs → no rate family.
    payload = client.post("/api/basket/scenarios", json=_basket_body()).json()
    assert payload["n_gaps"] == 1
    assert "rate" not in payload
    assert "n_rate" not in payload


def test_resolved_leg_carries_the_rate_sweep(resolving_client: TestClient):
    payload = resolving_client.post("/api/basket/scenarios", json=_basket_body()).json()
    assert payload["n_resolved"] == 1
    # configs/scenarios.yaml configures rate_shocks [-0.0025, 0.0, 0.0025] → a 3-cell sweep.
    rate = payload["rate"]
    assert payload["n_rate"] == len(rate) == 3
    assert [cell["rate_shock"] for cell in rate] == [-0.0025, 0.0, 0.0025]
    for cell in rate:
        assert cell["scenario_id"] == f"rate_{cell['rate_shock']:+.4f}"
        # Each cell labelled in bp and dollars, mirroring the persisted Risk path shape.
        assert cell["bp"] == pytest.approx(cell["rate_shock"] * 10_000.0)
        assert cell["bp_unit"] == "bp"
        assert cell["unit"] == "$ (full-reprice PnL)"
        assert cell["n_legs"] == 1
        assert cell["scenario_version"] == payload["surface"]["scenario_version"]
        assert isinstance(cell["scenario_pnl"], float)
    # The zero-rate cell is a no-op; a non-zero shock moves the book in dollars.
    by_shock = {cell["rate_shock"]: cell["scenario_pnl"] for cell in rate}
    assert by_shock[0.0] == pytest.approx(0.0, abs=1e-9)
    assert by_shock[0.0025] != pytest.approx(0.0, abs=1e-9)


def test_no_rate_shocks_is_byte_identical_to_surface_only(no_rate_client: TestClient):
    # An unconfigured rate axis must keep the surface-only payload byte-identical (no rate keys).
    payload = no_rate_client.post("/api/basket/scenarios", json=_basket_body()).json()
    assert payload["n_resolved"] == 1
    assert "rate" not in payload
    assert "n_rate" not in payload
