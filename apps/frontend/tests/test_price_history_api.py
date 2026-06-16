from __future__ import annotations

from datetime import timedelta
from types import ModuleType

import pytest
from fastapi.testclient import TestClient


def test_price_history_reads_back_daily_bars(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/price-history", params={"underlying": seed.MEMBER_AAA}
    ).json()
    assert payload["underlying"] == seed.MEMBER_AAA
    assert payload["n_bars"] == len(seed.AAA_BARS)
    last = payload["bars"][-1]
    assert last["trade_date"] == "2026-05-29"
    assert last["open"] == pytest.approx(seed.AAA_29_OPEN)
    assert last["high"] == pytest.approx(seed.AAA_29_HIGH)
    assert last["low"] == pytest.approx(seed.AAA_29_LOW)
    assert last["close"] == pytest.approx(seed.AAA_29_CLOSE)
    assert last["volume"] == pytest.approx(seed.AAA_29_VOLUME)
    assert last["provenance"]["code_version"] == "readback-test"


def test_price_history_uses_dailybar_ohlc_field_names(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    bar = seeded_client.get(
        "/api/price-history", params={"underlying": seed.MEMBER_AAA}
    ).json()["bars"][0]
    for field in ("trade_date", "open", "high", "low", "close", "volume"):
        assert field in bar, f"DailyBar field {field!r} must be in the payload"


def test_price_history_window_filters_inclusive(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    payload = seeded_client.get(
        "/api/price-history",
        params={"underlying": seed.MEMBER_AAA, "start": "2026-05-29", "end": "2026-05-29"},
    ).json()
    assert payload["n_bars"] == 1
    assert payload["bars"][0]["trade_date"] == "2026-05-29"


def test_price_history_unknown_ticker_is_empty_not_500(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/price-history", params={"underlying": "NOPE"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["underlying"] == "NOPE"
    assert payload["n_bars"] == 0
    assert payload["bars"] == []


def test_price_history_bad_date_is_labeled_400(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.get(
        "/api/price-history", params={"underlying": seed.MEMBER_AAA, "start": "not-a-date"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body == {"error": "bad_date", "start": "not-a-date", "end": None}


def test_price_history_batch_reads_all_requested_underlyings(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.post(
        "/api/price-history/batch",
        json={
            "underlyings": [seed.MEMBER_AAA, seed.MEMBER_BBB, seed.MEMBER_AAA],
            "end": seed.TRADE_DATE.isoformat(),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["underlyings"] == [seed.MEMBER_AAA, seed.MEMBER_BBB]
    assert payload["start"] == (seed.TRADE_DATE - timedelta(days=365)).isoformat()
    assert payload["end"] == seed.TRADE_DATE.isoformat()
    assert payload["n_underlyings"] == 2
    assert payload["n_loaded"] == 2
    assert payload["n_empty"] == 0
    assert payload["n_bars"] == len(seed.AAA_BARS) + len(seed.BBB_BARS)

    histories = {item["underlying"]: item for item in payload["histories"]}
    aaa_last = histories[seed.MEMBER_AAA]["bars"][-1]
    bbb_last = histories[seed.MEMBER_BBB]["bars"][-1]
    assert aaa_last["close"] == pytest.approx(seed.AAA_29_CLOSE)
    assert bbb_last["close"] == pytest.approx(seed.BBB_29_CLOSE)


def test_price_history_batch_unknown_member_is_labeled_empty(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.post(
        "/api/price-history/batch",
        json={"underlyings": [seed.MEMBER_AAA, "NOPE"], "end": seed.TRADE_DATE.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_underlyings"] == 2
    assert payload["n_loaded"] == 1
    assert payload["n_empty"] == 1
    nope = next(item for item in payload["histories"] if item["underlying"] == "NOPE")
    assert nope["bars"] == []


def test_price_history_batch_bad_date_is_labeled_400(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.post(
        "/api/price-history/batch",
        json={"underlyings": [seed.MEMBER_AAA], "end": "not-a-date"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_date", "start": None, "end": "not-a-date"}


def test_price_history_batch_non_string_date_is_labeled_400(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.post(
        "/api/price-history/batch",
        json={"underlyings": [seed.MEMBER_AAA], "start": 20260529},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_date", "start": 20260529, "end": None}


def test_price_history_batch_invalid_json_is_labeled_400(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/api/price-history/batch",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_batch", "detail": "body is not valid JSON"}


def test_price_history_batch_non_object_body_is_labeled_400(seeded_client: TestClient) -> None:
    response = seeded_client.post("/api/price-history/batch", json=["AAA", "BBB"])
    assert response.status_code == 400
    assert response.json() == {"error": "bad_batch", "detail": "body must be a JSON object"}
