from __future__ import annotations

from types import ModuleType

import pytest
from algotrading.frontend.serializers import ticket_to_dict
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import Side, build_ticket
from fastapi.testclient import TestClient


@pytest.fixture
def strangle_body(seed: ModuleType) -> dict:
    return {
        "basket_id": "strangle-aaa-3m",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "target_broker": "ibkr",
        "time_in_force": "day",
        "legs": [
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"},
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dp"},
        ],
    }


def test_ticket_preview_matches_pure_builder(
    seeded_client: TestClient, seed: ModuleType, strangle_body: dict
) -> None:
    response = seeded_client.post("/api/ticket/preview", json=strangle_body)
    assert response.status_code == 200

    expected_basket = Basket(
        basket_id="strangle-aaa-3m",
        trade_date=seed.TRADE_DATE,
        underlying=seed.MEMBER_AAA,
        legs=(
            BasketLeg(instrument_kind="option", side="long", quantity=1.0,
                      underlying=seed.MEMBER_AAA, tenor_label="3m", delta_band="30dc"),
            BasketLeg(instrument_kind="option", side="long", quantity=1.0,
                      underlying=seed.MEMBER_AAA, tenor_label="3m", delta_band="30dp"),
        ),
    )
    assert response.json() == ticket_to_dict(build_ticket(expected_basket))


def test_ticket_preview_maps_side_and_keeps_identity(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    payload = seeded_client.post("/api/ticket/preview", json=strangle_body).json()
    assert payload["source_basket_id"] == "strangle-aaa-3m"
    assert payload["target_broker"] == "ibkr"
    assert payload["time_in_force"] == "day"
    assert payload["n_legs"] == 2
    leg = payload["legs"][0]
    assert leg["side"] == Side.BUY.value
    assert leg["quantity"] == 1.0
    assert leg["price_spec"] == {"kind": "market"}
    assert (leg["tenor_label"], leg["delta_band"]) == ("3m", "30dc")


def test_ticket_preview_short_leg_maps_to_sell(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    body = {
        "basket_id": "short-call-aaa",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "legs": [
            {"instrument_kind": "option", "side": "short", "quantity": -2.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"},
        ],
    }
    leg = seeded_client.post("/api/ticket/preview", json=body).json()["legs"][0]
    assert leg["side"] == Side.SELL.value
    assert leg["quantity"] == 2.0


def test_ticket_preview_limit_price_spec(seeded_client: TestClient, seed: ModuleType) -> None:
    body = {
        "basket_id": "lmt-aaa",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "price_spec": {"kind": "limit", "price": 12.5},
        "legs": [
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"},
        ],
    }
    leg = seeded_client.post("/api/ticket/preview", json=body).json()["legs"][0]
    assert leg["price_spec"] == {"kind": "limit", "price": 12.5}


def test_ticket_preview_never_transmits(
    seeded_client: TestClient, strangle_body: dict
) -> None:
    payload = seeded_client.post("/api/ticket/preview", json=strangle_body).json()
    assert payload["mode"] == "paper"
    assert payload["gated"]["transmit"] is False
    assert "order_id" not in payload
    assert "broker_order_ref" not in payload


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param({"target_broker": "saxo"}, id="unknown-broker"),
        pytest.param({"time_in_force": "fok"}, id="unknown-tif"),
        pytest.param({"price_spec": {"kind": "limit"}}, id="limit-without-price"),
        pytest.param({"legs": [{"instrument_kind": "option", "side": "long", "quantity": 0.0,
                                "underlying": "AAA", "tenor_label": "3m", "delta_band": "30dc"}]},
                     id="zero-quantity"),
        pytest.param({"legs": []}, id="empty-basket"),
    ],
)
def test_ticket_preview_bad_input_is_labelled_400(
    seeded_client: TestClient, strangle_body: dict, mutate: dict
) -> None:
    body = {**strangle_body, **mutate}
    response = seeded_client.post("/api/ticket/preview", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_ticket"


def test_ticket_preview_bad_json_is_labelled_400(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/ticket/preview", content="not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_ticket"


def test_ticket_options_lists_the_enum_values(seeded_client: TestClient) -> None:
    from algotrading.infra.orders import TargetBroker, TimeInForce

    response = seeded_client.get("/api/ticket/options")
    assert response.status_code == 200
    assert response.json() == {
        "brokers": [b.value for b in TargetBroker],
        "time_in_force": [t.value for t in TimeInForce],
    }
