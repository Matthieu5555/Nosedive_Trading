"""Basket router tests: POST /api/basket/risk over the seeded analytics (WS 2A).

The seeded store holds two AAA analytics cells on TRADE_DATE (provider "IBKR", tenor
"3m"): the 30Δ put and the 30Δ call, each with the hand-chosen dollar Greeks in the
conftest seed. A long strangle (long the call + long the put) sums them; the oracle is
the hand sum of those stored numbers, derived independently of the BFF output.

The malformed-basket cases pin the labelled ``{"error": "bad_basket", "detail": …}`` 400
shape the web client matches on — including the request-model errors (missing field,
legs not a list, body not an object), which must stay 400s, never FastAPI's 422.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


@pytest.fixture
def strangle_body(seed: ModuleType) -> dict:
    """A long strangle on AAA: long 1 of the 30Δ call cell + long 1 of the 30Δ put cell."""
    return {
        "basket_id": "strangle-aaa-3m",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "provider": "IBKR",
        "legs": [
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"},
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dp"},
        ],
    }


def test_basket_router_reads_back_and_sums(
    seeded_client: TestClient, seed: ModuleType, strangle_body: dict
) -> None:
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_gaps"] == 0
    metrics = payload["metrics"]
    # Hand sums over the two seeded cells (both long, q=+1):
    #   delta = 58.5 + (-58.5) = 0.0
    #   gamma = 7.6 + 7.6      = 15.2
    #   vega  = 0.31 + 0.31    = 0.62
    #   theta = 2 * (-0.000041) = -0.000082
    #   rho   = 2 * 0.0005      = 0.001
    #   price = 2 * 4.2         = 8.4
    assert metrics["delta"]["dollar"] == pytest.approx(
        seed.AN_CALL_DOLLAR_DELTA + seed.AN_PUT_DOLLAR_DELTA
    )
    assert metrics["gamma"]["dollar"] == pytest.approx(2 * seed.AN_DOLLAR_GAMMA)
    assert metrics["vega"]["dollar"] == pytest.approx(2 * seed.AN_DOLLAR_VEGA)
    assert metrics["theta"]["dollar"] == pytest.approx(2 * seed.AN_DOLLAR_THETA)
    assert metrics["rho"]["dollar"] == pytest.approx(2 * seed.AN_DOLLAR_RHO)
    assert payload["price"] == pytest.approx(2 * seed.AN_PRICE)
    # The per-leg breakdown proves the aggregate is the sum of the per-leg analytics numbers.
    assert payload["n_legs"] == 2
    contributions = sorted(leg["metrics"]["delta"]["dollar"] for leg in payload["legs"])
    assert contributions == pytest.approx(
        sorted([seed.AN_CALL_DOLLAR_DELTA, seed.AN_PUT_DOLLAR_DELTA])
    )


def test_basket_payload_uses_blueprint_field_names(
    seeded_client: TestClient, seed: ModuleType, strangle_body: dict
) -> None:
    # ADR-0029 names cross the seam: a resolved option leg echoes the matched cell's
    # forward_price / implied_vol / log_moneyness (a renamed contract field turns this red).
    payload = seeded_client.post("/api/basket/risk", json=strangle_body).json()
    call_leg = next(leg for leg in payload["legs"] if leg["delta_band"] == "30dc")
    assert call_leg["forward_price"] == pytest.approx(seed.AN_FORWARD)
    assert call_leg["implied_vol"] == pytest.approx(seed.AN_CALL_IV)
    assert call_leg["log_moneyness"] == pytest.approx(seed.AN_CALL_LOGM)
    assert set(payload["metrics"]) == {"delta", "gamma", "vega", "theta", "rho"}


def test_basket_dollar_greeks_carry_unit_strings(
    seeded_client: TestClient, seed: ModuleType, strangle_body: dict
) -> None:
    payload = seeded_client.post("/api/basket/risk", json=strangle_body).json()
    metrics = payload["metrics"]
    assert metrics["delta"]["unit"] == seed.AN_DOLLAR_DELTA_UNIT
    assert metrics["gamma"]["unit"] == seed.AN_DOLLAR_GAMMA_UNIT
    assert metrics["vega"]["unit"] == seed.AN_DOLLAR_VEGA_UNIT
    assert metrics["theta"]["unit"] == seed.AN_DOLLAR_THETA_UNIT
    assert metrics["rho"]["unit"] == seed.AN_DOLLAR_RHO_UNIT
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        assert metrics[greek]["unit"]  # non-empty


def test_basket_stock_leg_prices_off_daily_bar_close(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # A stock leg's dollar delta = signed_qty * spot, where spot is AAA's close on TRADE_DATE
    # read from daily_bar (192.0). No option legs, so the other Greeks are zero.
    body = {
        "basket_id": "stk-aaa",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "provider": "IBKR",
        "legs": [
            {"instrument_kind": "stock", "side": "long", "quantity": 10.0,
             "underlying": seed.MEMBER_AAA},
        ],
    }
    payload = seeded_client.post("/api/basket/risk", json=body).json()
    assert payload["metrics"]["delta"]["dollar"] == pytest.approx(10.0 * seed.AAA_29_CLOSE)
    assert payload["metrics"]["gamma"]["dollar"] == pytest.approx(0.0)
    assert payload["n_gaps"] == 0


def test_unpriced_leg_is_200_not_500(
    seeded_client: TestClient, seed: ModuleType, strangle_body: dict
) -> None:
    # A leg on a cell that was never seeded ("10dp") is a labelled gap with HTTP 200, never a 500.
    strangle_body["basket_id"] = "has-a-gap"
    strangle_body["legs"].append(
        {"instrument_kind": "option", "side": "long", "quantity": 1.0,
         "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "10dp"}
    )
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_gaps"] == 1
    gap = payload["gaps"][0]
    assert gap["delta_band"] == "10dp"
    assert gap["reason"] == "no_analytics_row"
    # The two priced legs still sum; the gap is reported, not absorbed as a zero.
    assert payload["metrics"]["gamma"]["dollar"] == pytest.approx(2 * seed.AN_DOLLAR_GAMMA)


# --- malformed baskets: the labelled bad_basket 400 contract ------------------------------


def test_malformed_basket_side_sign_is_400(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    strangle_body["legs"][0]["quantity"] = -1.0  # a "long" leg with a negative quantity
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_basket"
    # The ContractValidationError carries the offending value into the detail.
    assert "quantity" in body["detail"] and "-1.0" in body["detail"]


def test_malformed_basket_bad_trade_date_is_400(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    strangle_body["trade_date"] = "not-a-date"
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_basket"


def test_basket_missing_leg_field_is_a_400_naming_the_field(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    # A leg without its "side" is a labelled 400 whose detail names the missing field —
    # never an opaque "'side'" KeyError repr, and never FastAPI's 422.
    del strangle_body["legs"][0]["side"]
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_basket"
    assert "side" in body["detail"]


def test_basket_legs_not_a_list_is_400(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    strangle_body["legs"] = "not-a-list"
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_basket"


def test_basket_non_object_body_is_400(seeded_client: TestClient) -> None:
    response = seeded_client.post("/api/basket/risk", json=["not", "an", "object"])
    assert response.status_code == 400
    assert response.json()["error"] == "bad_basket"


def test_basket_invalid_json_is_400(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/basket/risk", content=b"not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_basket", "detail": "body is not valid JSON"}


# --- trade-date semantics ------------------------------------------------------------------


def test_basket_prices_off_its_own_trade_date_no_look_ahead(
    tmp_path: Path, seed: ModuleType
) -> None:
    # No look-ahead: the basket prices off the analytics for its OWN trade_date; a later
    # snapshot with different numbers does not change the priced basket. Self-contained store.
    early = seed.TRADE_DATE
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [
        seed.analytics_cell_on(
            datetime(2026, 5, 29, 15, 30, tzinfo=UTC), delta_band="30dc", dollar_delta=58.5
        ),
        seed.analytics_cell_on(
            datetime(2026, 5, 30, 15, 30, tzinfo=UTC), delta_band="30dc", dollar_delta=999.0
        ),
    ])
    app_ctx = AppContext(
        store_root=store_root, configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root), default_underlying=seed.MEMBER_AAA,
    )
    with TestClient(create_app(app_ctx)) as client:
        body = {
            "basket_id": "no-la", "trade_date": early.isoformat(),
            "underlying": seed.MEMBER_AAA, "provider": "IBKR",
            "legs": [{"instrument_kind": "option", "side": "long", "quantity": 1.0,
                      "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"}],
        }
        payload = client.post("/api/basket/risk", json=body).json()
    # Priced at the early date: the early number (58.5), never the later snapshot's 999.0.
    assert payload["metrics"]["delta"]["dollar"] == pytest.approx(58.5)


def test_empty_trade_date_resolves_to_latest_banked_day(
    tmp_path: Path, seed: ModuleType
) -> None:
    # The web client sends trade_date "" until the operator picks a date: the router resolves it
    # to the LATEST banked analytics partition for the underlying — here 2026-05-30 (the 999.0
    # cell), never the earlier 2026-05-29 (58.5) — and echoes the resolved date in the payload.
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write("projected_option_analytics", [
        seed.analytics_cell_on(
            datetime(2026, 5, 29, 15, 30, tzinfo=UTC), delta_band="30dc", dollar_delta=58.5
        ),
        seed.analytics_cell_on(
            datetime(2026, 5, 30, 15, 30, tzinfo=UTC), delta_band="30dc", dollar_delta=999.0
        ),
    ])
    app_ctx = AppContext(
        store_root=store_root, configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root), default_underlying=seed.MEMBER_AAA,
    )
    with TestClient(create_app(app_ctx)) as client:
        body = {
            "basket_id": "latest", "trade_date": "", "underlying": seed.MEMBER_AAA,
            "provider": "IBKR",
            "legs": [{"instrument_kind": "option", "side": "long", "quantity": 1.0,
                      "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"}],
        }
        response = client.post("/api/basket/risk", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["trade_date"] == "2026-05-30"
    assert payload["metrics"]["delta"]["dollar"] == pytest.approx(999.0)


def test_empty_trade_date_with_nothing_banked_is_a_labelled_400(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    # An empty date over an underlying with no banked grid has no day to default to: a labelled
    # 400 naming the underlying, never a silent guess or a 500.
    strangle_body["trade_date"] = ""
    strangle_body["underlying"] = "ZZZ"
    response = seeded_client.post("/api/basket/risk", json=strangle_body)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "bad_basket"
    assert "ZZZ" in payload["detail"]
