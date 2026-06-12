"""Recorded-dates router tests: coverage from the 1G run-state ledger (WS 1G/1H).

``ledger_client`` (conftest) seeds two gap-free completed EOD runs plus one partial/failed
run; only complete runs count as recorded. Expected dates come from the seeded ledger,
not the router output.
"""

from __future__ import annotations

from types import ModuleType

from fastapi.testclient import TestClient


def test_recorded_dates_excludes_incomplete_runs(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    payload = ledger_client.get("/api/recorded-dates", params={"index": seed.INDEX}).json()
    assert payload["count"] == 2
    # Only the two gap-free completed days, newest first; the partial day is excluded.
    assert payload["dates"] == [
        seed.COMPLETE_DATE_2.isoformat(),
        seed.COMPLETE_DATE_1.isoformat(),
    ]
    assert seed.PARTIAL_DATE.isoformat() not in payload["dates"]


def test_recorded_dates_empty_ledger_is_count_zero(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    # seeded_client's store has no run ledger: a labeled empty state with count 0, never a 500.
    response = seeded_client.get("/api/recorded-dates", params={"index": seed.INDEX})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["dates"] == []


def test_recorded_date_pick_reresolves_membership_as_of(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    # Picking a returned past date drives the as-of re-resolution: the constituent list resolved
    # at that date returns the basket in force then (the front wires the dropdown to as_of).
    recorded = ledger_client.get("/api/recorded-dates", params={"index": seed.INDEX}).json()
    picked = recorded["dates"][0]  # 2026-05-29, a complete day
    basket = ledger_client.get(
        "/api/constituents", params={"index": seed.INDEX, "as_of": picked}
    ).json()
    assert basket["as_of"] == picked
    assert {c["symbol"] for c in basket["constituents"]} == {seed.MEMBER_AAA, seed.MEMBER_BBB}
