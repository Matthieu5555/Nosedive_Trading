"""Ticket router tests: POST /api/ticket/preview (WS 3A, preview-only, paper).

Builds an order ticket from the same composed-basket body the basket router takes, over the
seeded AAA analytics on TRADE_DATE. The assertions pin:

* **the BFF returns exactly what the pure builder returns** for the same basket — the endpoint
  is a thin serializer over :func:`~algotrading.infra.orders.build_ticket`, never a parallel
  computation;
* **the long/short -> BUY/SELL mapping** and positive-magnitude quantity, with the grid identity
  carried through;
* **the labelled ``{"error": "bad_ticket", "detail": …}`` 400** for malformed input (an unknown
  broker, a bad leg) — never FastAPI's 422, never a 500;
* **the gate**: the payload says ``gated.transmit == False`` and ``mode == "paper"`` and carries
  no order id — nothing transmits.

Independent oracle: the expected ticket is the pure builder's output on a hand-built basket; the
HTTP payload is compared to ``ticket_to_dict`` of that, not to itself.
"""

from __future__ import annotations

from types import ModuleType

import pytest
from algotrading.frontend.serializers import ticket_to_dict
from algotrading.infra.contracts import Basket, BasketLeg
from algotrading.infra.orders import Side, build_ticket
from fastapi.testclient import TestClient


@pytest.fixture
def strangle_body(seed: ModuleType) -> dict:
    """A long strangle on AAA: long 1 of the 30Δ call cell + long 1 of the 30Δ put cell."""
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

    # Independent oracle: the same basket through the pure builder, serialized the same way.
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
    # long -> buy, magnitude quantity, grid identity carried, default market price spec.
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
    assert leg["quantity"] == 2.0  # abs of the signed -2.0


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
    # The gate, asserted on the wire: paper-only, transmit explicitly false, no order handle.
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
    # The selector source for the web panel — derived from the TargetBroker / TimeInForce enums,
    # not a hardcoded list (independent oracle = the enum values themselves).
    from algotrading.infra.orders import TargetBroker, TimeInForce

    response = seeded_client.get("/api/ticket/options")
    assert response.status_code == 200
    assert response.json() == {
        "brokers": [b.value for b in TargetBroker],
        "time_in_force": [t.value for t in TimeInForce],
    }
