"""HTTP-seam tests for POST /api/basket/scenarios.

The reprice math is covered exhaustively in ``test_basket_scenarios.py``; these assert the router
contract: a malformed basket is a labelled 400, and a valid request is a 200 carrying the surface
payload shape the ``StressSurface`` web component renders plus labelled per-leg gaps.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import ProjectedOptionAnalytics
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE = date(2026, 6, 5)
_CONFIGS = Path("configs").resolve()  # the real economic bundles (ScenarioConfig source)


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


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A BFF client over a tmp store seeded with one grid row, configs from the repo bundles."""
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [_row()])
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
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
    # The web client sends trade_date "" until the operator picks a date: the router resolves it
    # to the latest banked analytics partition for the underlying (here the one seeded day) and
    # echoes the resolved date, so the default UI flow stresses without picking a date.
    response = client.post("/api/basket/scenarios", json=_basket_body(trade_date=""))
    assert response.status_code == 200
    assert response.json()["trade_date"] == _TRADE.isoformat()


def test_valid_request_returns_surface_payload(client: TestClient):
    response = client.post("/api/basket/scenarios", json=_basket_body())
    assert response.status_code == 200
    payload = response.json()
    surface = payload["surface"]
    # The surface payload shape the StressSurface component renders.
    assert surface["spot_shock"] and surface["vol_shock"]
    assert len(surface["scenario_pnl"]) == len(surface["spot_shock"])
    assert surface["unit"] == "$ (full-reprice PnL)"
    assert surface["n_cells"] == len(surface["spot_shock"]) * len(surface["vol_shock"])
    assert "worst_case" in payload and surface["scenario_version"]
    # The seeded grid has no instrument_master, so the leg is a labelled gap inside the 200.
    assert payload["n_gaps"] == 1
    assert payload["gaps"][0]["reason"] == "no_instrument_master"
    # Centre cell is ~0 by construction even with an unresolved leg (empty book + no overlay).
    ci = surface["spot_shock"].index(0.0)
    cj = surface["vol_shock"].index(0.0)
    assert math.isclose(surface["scenario_pnl"][ci][cj], 0.0, abs_tol=1e-6)
