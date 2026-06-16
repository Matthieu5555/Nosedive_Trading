from __future__ import annotations

import secrets
from pathlib import Path
from types import ModuleType

import pytest
from algotrading.execution.booking.password_gate import (
    ENV_GATE_HASH,
    ENV_GATE_SALT,
    hash_password,
)
from fastapi.testclient import TestClient

_PASSWORD = "let-me-in"


@pytest.fixture
def gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    salt = secrets.token_bytes(16)
    monkeypatch.setenv(ENV_GATE_SALT, salt.hex())
    monkeypatch.setenv(ENV_GATE_HASH, hash_password(_PASSWORD, salt))


@pytest.fixture
def booking_body(seed: ModuleType) -> dict:
    return {
        "basket_id": "book-aaa-3m",
        "trade_date": seed.TRADE_DATE.isoformat(),
        "underlying": seed.MEMBER_AAA,
        "target_broker": "ibkr",
        "time_in_force": "day",
        "legs": [
            {"instrument_kind": "option", "side": "long", "quantity": 1.0,
             "underlying": seed.MEMBER_AAA, "tenor_label": "3m", "delta_band": "30dc"},
        ],
    }


def _fills_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "booking" / "fills.jsonl"


def test_a_wrong_password_is_a_labelled_block_with_no_fill_written(
    seeded_client: TestClient, booking_body: dict, gate_env: None, tmp_path: Path
) -> None:
    body = {**booking_body, "password": "not-the-password"}
    response = seeded_client.post("/api/booking/commit", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] == "block"
    assert payload["reason"] == "wrong_password"
    assert not _fills_file(tmp_path).exists()


def test_an_absent_password_is_a_labelled_block(
    seeded_client: TestClient, booking_body: dict, gate_env: None
) -> None:
    body = {**booking_body, "password": ""}
    payload = seeded_client.post("/api/booking/commit", json=body).json()
    assert payload["decision"] == "block"
    assert payload["reason"] == "absent_password"


def test_an_unconfigured_gate_blocks(
    seeded_client: TestClient, booking_body: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_GATE_SALT, raising=False)
    monkeypatch.delenv(ENV_GATE_HASH, raising=False)
    body = {**booking_body, "password": _PASSWORD}
    payload = seeded_client.post("/api/booking/commit", json=body).json()
    assert payload["decision"] == "block"
    assert payload["reason"] == "unconfigured_gate"


def test_a_correct_password_passes_the_barrier_to_the_concretization_seam(
    seeded_client: TestClient, booking_body: dict, gate_env: None, tmp_path: Path
) -> None:
    body = {**booking_body, "password": _PASSWORD}
    payload = seeded_client.post("/api/booking/commit", json=body).json()
    assert payload["decision"] == "block"
    assert payload["reason"] == "unresolvable_leg"
    assert not _fills_file(tmp_path).exists()


def test_the_block_is_recorded_in_the_durable_audit_log(
    seeded_client: TestClient, booking_body: dict, gate_env: None, tmp_path: Path
) -> None:
    seeded_client.post("/api/booking/commit", json={**booking_body, "password": "wrong"})
    audit_file = tmp_path / "data" / "booking" / "booking_audit.jsonl"
    assert audit_file.exists()
    assert audit_file.read_text(encoding="utf-8").strip() != ""


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param({"target_broker": "saxo"}, id="unknown-broker"),
        pytest.param({"legs": []}, id="empty-basket"),
        pytest.param(
            {"legs": [{"instrument_kind": "option", "side": "long", "quantity": 0.0,
                       "underlying": "AAA", "tenor_label": "3m", "delta_band": "30dc"}]},
            id="zero-quantity",
        ),
    ],
)
def test_a_malformed_request_is_a_labelled_400(
    seeded_client: TestClient, booking_body: dict, gate_env: None, mutate: dict
) -> None:
    body = {**booking_body, **mutate, "password": _PASSWORD}
    response = seeded_client.post("/api/booking/commit", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_booking"


def test_the_commit_response_carries_no_broker_order_handle(
    seeded_client: TestClient, booking_body: dict, gate_env: None
) -> None:
    payload = seeded_client.post(
        "/api/booking/commit", json={**booking_body, "password": _PASSWORD}
    ).json()
    assert "order_id" not in payload
    assert "broker_order_ref" not in payload
    assert "transmit" not in payload
