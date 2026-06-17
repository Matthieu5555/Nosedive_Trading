from __future__ import annotations

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


def test_each_completed_date_is_one_canonical_close(
    ledger_client: TestClient, seed: ModuleType
) -> None:
    # Overwrite-last-wins: each completed trade_date is exactly ONE entry, carrying its banked
    # landing time (the ledger's latest stage ts) and the date-level QC verdict.
    available = ledger_client.get(
        "/api/recorded-dates", params={"index": seed.INDEX}
    ).json()["available"]
    by_date = {entry["date"]: entry for entry in available}
    assert len(available) == len({e["date"] for e in available})  # one entry per date
    complete_2 = by_date[seed.COMPLETE_DATE_2.isoformat()]
    assert complete_2["recorded_ts"] == seed.AS_OF.isoformat()
    assert complete_2["qc"] == "pass"


def test_a_re_fetched_date_overwrites_to_one_canonical_close(
    tmp_path: Path, seed: ModuleType
) -> None:
    # A same-day re-fetch OVERWRITES the day's slot (overwrite-last-wins, no run= partitioning).
    # The serving view shows ONE canonical close per trade_date with the latest landing time
    # (ADR 0051 / blueprint §15 / 01-arch:17) — not a second peer as-of.
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
    store.write("projected_option_analytics", cells)
    for landed in (ts_a, ts_b):
        for stage in EOD_STAGES:
            record_stage(
                root,
                StageRun(
                    trade_date=seed.TRADE_DATE,
                    stage=stage,
                    outcome=OUTCOME_OK,
                    run_id=f"run-{landed.isoformat()}",
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
    # Exactly one row for the day, carrying the latest landing time (ts_b).
    assert len(on_date) == 1
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
