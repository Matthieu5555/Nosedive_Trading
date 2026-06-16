from __future__ import annotations

from types import ModuleType

import pytest
from fastapi.testclient import TestClient


def test_constituents_reads_back_as_of_basket(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/constituents", params={"index": seed.INDEX, "as_of": seed.TRADE_DATE.isoformat()}
    ).json()
    assert payload["index"] == seed.INDEX
    assert payload["as_of"] == seed.TRADE_DATE.isoformat()
    symbols = [c["symbol"] for c in payload["constituents"]]
    assert symbols == [seed.MEMBER_AAA, seed.MEMBER_BBB]
    aaa = payload["constituents"][0]
    assert aaa["weight"] == pytest.approx(0.6)
    assert aaa["effective_add_date"] == "2026-01-01"
    assert aaa["effective_remove_date"] is None
    assert aaa["latest_close"] == pytest.approx(seed.AAA_29_CLOSE)


def test_constituents_price_first_orders_by_latest_close(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    closes = [
        c["latest_close"]
        for c in seeded_client.get(
            "/api/constituents",
            params={"index": seed.INDEX, "as_of": seed.TRADE_DATE.isoformat()},
        ).json()["constituents"]
    ]
    assert closes == sorted(closes, key=lambda c: -c)


def test_constituents_as_of_excludes_future_members(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    symbols = {
        c["symbol"]
        for c in seeded_client.get(
            "/api/constituents",
            params={"index": seed.INDEX, "as_of": seed.TRADE_DATE.isoformat()},
        ).json()["constituents"]
    }
    assert symbols == {seed.MEMBER_AAA, seed.MEMBER_BBB}
    assert "FUT" not in symbols
    assert "CCC" not in symbols


def test_constituents_effective_add_date_is_per_name(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    by_symbol = {
        c["symbol"]: c
        for c in seeded_client.get(
            "/api/constituents", params={"index": seed.INDEX, "as_of": "2026-06-15"}
        ).json()["constituents"]
    }
    assert by_symbol[seed.MEMBER_AAA]["effective_add_date"] == "2026-01-01"
    assert by_symbol["FUT"]["effective_add_date"] == "2026-06-01"


def test_constituents_bad_as_of_is_labeled_400(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.get(
        "/api/constituents", params={"index": seed.INDEX, "as_of": "nope"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_as_of", "as_of": "nope"}
