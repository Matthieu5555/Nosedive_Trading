from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.orchestration.run_state import EOD_STAGES, OUTCOME_OK, StageRun, record_stage
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient


def test_recorded_dates_excludes_incomplete_runs(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    payload = ledger_client.get("/api/recorded-dates", params={"index": seed.INDEX}).json()
    assert payload["count"] == 2
    assert payload["dates"] == [
        seed.COMPLETE_DATE_2.isoformat(),
        seed.COMPLETE_DATE_1.isoformat(),
    ]
    assert seed.PARTIAL_DATE.isoformat() not in payload["dates"]


def test_legacy_flat_data_lists_a_single_date_only_fetch(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    # The ledger fixture seeds run ids but writes flat analytics (no run= partition). With no
    # addressable run, each completed date is one date-only entry: run_id/recorded_ts null.
    available = ledger_client.get(
        "/api/recorded-dates", params={"index": seed.INDEX}
    ).json()["available"]
    by_date = {entry["date"]: entry for entry in available}
    complete_2 = by_date[seed.COMPLETE_DATE_2.isoformat()]
    assert complete_2["run_id"] is None
    assert complete_2["recorded_ts"] is None
    assert complete_2["qc"] == "pass"


def test_a_re_fetched_date_collapses_to_one_canonical_close_latest_wins(
    tmp_path: Path, seed: ModuleType
) -> None:
    # Two fetches of the SAME trade date, each its own run= partition on disk. The serving view must
    # collapse them to ONE canonical close per trade_date — the NEWEST run (latest wins, ADR 0051 /
    # blueprint §15) — so a same-day re-fetch shows once, not as a second peer as-of. The older run
    # stays on disk for forensic replay, just off the default picker.
    root = tmp_path / "data"
    store = ParquetStore(root)
    ts_a = datetime(2026, 5, 29, 8, 24, tzinfo=UTC)
    ts_b = datetime(2026, 5, 29, 13, 22, tzinfo=UTC)
    cells = [
        seed.analytics_cell(
            delta_band="30dp",
            target_delta=seed.AN_PUT_DELTA,
            log_moneyness=seed.AN_PUT_LOGM,
            implied_vol=seed.AN_PUT_IV,
            delta=seed.AN_PUT_DELTA,
            dollar_delta=seed.AN_PUT_DOLLAR_DELTA,
        )
    ]
    store.write("projected_option_analytics", cells, run_id="fetch-A")
    time.sleep(0.01)  # distinct run-dir mtimes for deterministic newest-first ordering
    store.write("projected_option_analytics", cells, run_id="fetch-B")
    for run_id, landed in (("fetch-A", ts_a), ("fetch-B", ts_b)):
        for stage in EOD_STAGES:
            record_stage(
                root,
                StageRun(
                    trade_date=seed.TRADE_DATE,
                    stage=stage,
                    outcome=OUTCOME_OK,
                    run_id=run_id,
                    recorded_ts=landed,
                ),
            )

    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying=seed.UNDERLYING,
    )
    with TestClient(create_app(ctx)) as client:
        available = client.get(
            "/api/recorded-dates", params={"index": seed.INDEX}
        ).json()["available"]

    on_date = [e for e in available if e["date"] == seed.TRADE_DATE.isoformat()]
    # One row for the day, and it is the newest run (fetch-B) with its minute-precise landing time.
    assert [e["run_id"] for e in on_date] == ["fetch-B"]
    assert on_date[0]["recorded_ts"].startswith("2026-05-29T13:22")
    assert on_date[0]["qc"] == "pass"


def test_recorded_dates_empty_ledger_is_count_zero(
    seeded_client: TestClient, seed: ModuleType
) -> None:
    response = seeded_client.get("/api/recorded-dates", params={"index": seed.INDEX})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["dates"] == []


def test_recorded_date_pick_reresolves_membership_as_of(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    recorded = ledger_client.get("/api/recorded-dates", params={"index": seed.INDEX}).json()
    picked = recorded["dates"][0]
    basket = ledger_client.get(
        "/api/constituents", params={"index": seed.INDEX, "as_of": picked}
    ).json()
    assert basket["as_of"] == picked
    assert {c["symbol"] for c in basket["constituents"]} == {seed.MEMBER_AAA, seed.MEMBER_BBB}
