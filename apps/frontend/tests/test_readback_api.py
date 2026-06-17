"""BFF <-> infra seam test for the compose / book router (2D step 5).

This is the consumer-side contract test from TESTING.md ("BFF <-> infra | a router reading
back a persisted contract field wrong"). The compose router persists nothing of its own; it
resolves the operator's ordered sub-strategies against the *persisted*
``projected_option_analytics`` + ``instrument_master`` rows and serializes the landed
``build_book_greeks`` / ``book_stress_surface`` output. So the seam this test pins is: the
router reads back those banked fields correctly, and the book it serializes is the additive
sum of the layers it read (the three-ways-one-number identity, asserted over the HTTP payload).

The independent oracle for the additivity assertion is the payload's own per-layer rows summed
by the test (not by the router): combined == sum(layers) for every Greek.
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
from algotrading.infra.contracts import InstrumentMaster, ProjectedOptionAnalytics
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE = date(2026, 6, 5)
_EXPIRY = date(2026, 9, 4)
_CONFIGS = Path("configs").resolve()

# Two distinct underlyings so the book is a genuine 2-layer composition of decorrelated names.
_UND_A = "AAA"
_UND_B = "BBB"
_DECIMAL = ("net_delta", "net_gamma", "net_vega", "net_theta")
_DOLLAR = ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho")


def _prov() -> object:
    return stamp(
        calc_ts=_TS,
        code_version="readback-test",
        config_hashes={"cfg": "cfg-readback"},
        source_records=(source_ref("raw_market_events", "sess", "evt"),),
        source_timestamps=(_TS,),
    )


def _analytics_row(underlying: str, *, strike: float, vega: float) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider="ibkr",
        underlying=underlying,
        tenor_label="1m",
        maturity_years=1.0 / 12.0,
        delta_band="atm",
        target_delta=0.50,
        log_moneyness=0.0,
        strike=strike,
        forward_price=strike,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 / 12.0,
        price=2.30,
        delta=0.5,
        gamma=0.02,
        vega=vega,
        theta=-0.05,
        rho=0.04,
        dollar_delta=500.0,
        dollar_gamma=0.02,
        dollar_vega=vega,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=_prov(),
    )


def _instrument_master(underlying: str, *, strike: float) -> InstrumentMaster:
    key = InstrumentKey(
        underlying_symbol=underlying,
        security_type="OPT",
        exchange="EUREX",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id=f"o-{underlying}",
        expiry=_EXPIRY,
        strike=strike,
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
    store.write(
        "projected_option_analytics",
        [
            _analytics_row(_UND_A, strike=100.0, vega=0.31),
            _analytics_row(_UND_B, strike=50.0, vega=0.17),
        ],
    )
    store.write(
        "instrument_master",
        [
            _instrument_master(_UND_A, strike=100.0),
            _instrument_master(_UND_B, strike=50.0),
        ],
    )
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
    with TestClient(create_app(ctx)) as test_client:
        yield test_client


def _leg(underlying: str, side: str, quantity: float) -> dict[str, object]:
    # Basket contract: a short leg carries a negative quantity, a long leg a positive one.
    signed = quantity if side == "long" else -quantity
    return {
        "instrument_kind": "option",
        "side": side,
        "quantity": signed,
        "underlying": underlying,
        "tenor_label": "1m",
        "delta_band": "atm",
    }


def _compose_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "book_id": "BK-readback",
        "trade_date": _TRADE.isoformat(),
        "layers": [
            {
                "label": "vol-seller",
                "basket_id": "L1",
                "underlying": _UND_A,
                "legs": [_leg(_UND_A, "short", 2.0)],
            },
            {
                "label": "crash-hedge",
                "basket_id": "L2",
                "underlying": _UND_B,
                "legs": [_leg(_UND_B, "long", 3.0)],
            },
        ],
    }
    body.update(overrides)
    return body


def test_list_sub_strategies_reads_back_banked_underlyings(client: TestClient) -> None:
    response = client.get("/api/compose/sub-strategies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sub_strategies"] == [_UND_A, _UND_B]
    assert payload["n_sub_strategies"] == 2


def test_compose_reads_back_persisted_book_fields(client: TestClient) -> None:
    response = client.post("/api/compose", json=_compose_body())
    assert response.status_code == 200
    payload = response.json()

    assert payload["book_id"] == "BK-readback"
    assert payload["composition_version"] == "composition-1.0.0"
    assert set(payload["config_hashes"]) == {"layer_set", "grid", "monetization"}
    assert all(payload["config_hashes"].values())

    layers = payload["layers"]
    assert [layer["layer_label"] for layer in layers] == ["vol-seller", "crash-hedge"]
    # Both legs resolved against the banked analytics + instrument_master.
    assert [layer["n_resolved"] for layer in layers] == [1, 1]

    # Independent oracle: the combined book is the additive sum of the per-layer rows the
    # router read back (three-ways-one-number, asserted on the payload, not in the router).
    combined = payload["combined"]
    assert combined["level"] == "book"
    for field in _DECIMAL:
        assert combined[field] == pytest.approx(
            math.fsum(layer[field] for layer in layers)
        )
    for field in _DOLLAR:
        assert combined[field]["value"] == pytest.approx(
            math.fsum(layer[field]["value"] for layer in layers)
        )

    # Dollar Greeks carry their unit strings read straight from the contract.
    assert combined["dollar_gamma"]["unit"] == "$ per 1% move"
    assert combined["dollar_theta"]["unit"] == "$ per calendar day"

    # The combined stressed PnL surface renders: finite, grid-shaped, centred at ~0 PnL.
    surface = payload["surface"]
    spot_axis, vol_axis, grid = surface["spot_axis"], surface["vol_axis"], surface["pnl_grid"]
    assert surface["scenario_version"]
    assert len(grid) == len(spot_axis)
    assert all(len(row) == len(vol_axis) for row in grid)
    assert all(math.isfinite(cell) for row in grid for cell in row)
    ci, cj = spot_axis.index(0.0), vol_axis.index(0.0)
    assert math.isclose(grid[ci][cj], 0.0, abs_tol=1e-6)

    # The diversification diagnostic is surfaced (read-only number), not silently dropped.
    assert "diversification_ratio" in payload


def test_empty_trade_date_resolves_to_latest_banked_day(client: TestClient) -> None:
    response = client.post("/api/compose", json=_compose_body(trade_date=""))
    assert response.status_code == 200
    assert response.json()["combined"]["level"] == "book"


def test_malformed_json_is_400(client: TestClient) -> None:
    response = client.post("/api/compose", content=b"not json")
    assert response.status_code == 400
    assert response.json()["error"] == "bad_composition"


def test_malformed_composition_is_400(client: TestClient) -> None:
    # ``layers`` must be a list of layer objects; a string is a structured rejection, not a coerce.
    response = client.post("/api/compose", json={"book_id": "x", "layers": "nope"})
    assert response.status_code == 400
    assert response.json()["error"] == "bad_composition"
