"""Coverage router tests: per-expiry capture + per-tenor QC coverage, store-backed.

The router reads ``instrument_master`` (the captured chain) and ``qc_results`` (WS 1H's
``tenor_coverage_floor`` / ``delta_band_completeness``) — no recompute. An empty store returns a
labeled empty payload (never a 500); a malformed ``trade_date`` is a 400. The populated case seeds a
hand-counted chain + a hand-built QC verdict and asserts the counts and per-tenor coverage the front
will render, so a passing assertion is real agreement, not a round-trip.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, QcResult
from fastapi.testclient import TestClient

_TRADE_DATE = date(2026, 6, 11)
_RUN_TS = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


def _opt(expiry: date, strike: float, right: str) -> InstrumentKey:
    return InstrumentKey(
        "SPX", "OPT", "CBOE", "USD", 100.0, f"c-{expiry}-{strike}-{right}", expiry, strike, right
    )


def _master(key: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=key.canonical(), as_of_date=_TRADE_DATE, instrument=key, raw_broker_payload="{}"
    )


def _qc(check: str, status: str, context: str = "{}") -> QcResult:
    return QcResult(
        run_id="run-cov",
        check_name=check,
        target_key="SPX",
        run_ts=_RUN_TS,
        qc_status=status,
        severity="critical",
        measured_value=0.0,
        threshold_version="t-1",
        context=context,
    )


def test_coverage_empty_store_is_well_formed(infra_client: TestClient) -> None:
    """An empty store yields a labeled empty payload (200), not a 500."""
    payload = infra_client.get("/api/coverage", params={"underlying": "SPX"}).json()
    assert payload["n_expiries"] == 0
    assert payload["expiries"] == []
    assert payload["tenors"] == []
    assert payload["qc_status"] == "unknown"


def test_coverage_bad_trade_date_is_400(infra_client: TestClient) -> None:
    """A malformed trade_date is a labeled 400, never a 500."""
    response = infra_client.get("/api/coverage", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    assert response.json()["error"] == "bad_trade_date"


def test_coverage_populated_counts_and_tenor_grid(ctx: AppContext) -> None:
    """Per-expiry counts and the whole-grid per-tenor coverage match the seeded data.

    Hand-counted chain (independent oracle):
      * 2026-06-19 — strikes {100,105} × {C,P}  -> 2 strikes, 2 calls, 2 puts, [100,105]
      * 2026-09-18 — strikes {200,205,210} × {C} -> 3 strikes, 3 calls, 0 puts, [200,210]
      * 2028-06-15 — strikes {300} × {P}          -> 1 strike,  0 calls, 1 put,  [300,300]
    Hand-built QC: tenor_coverage_floor fails with 1m (measured 0) and 3m (measured 3) breaching;
    every other pinned tenor cleared the floor; delta_band_completeness fails.
    """
    masters = [
        _master(_opt(date(2026, 6, 19), 100.0, "C")),
        _master(_opt(date(2026, 6, 19), 105.0, "C")),
        _master(_opt(date(2026, 6, 19), 100.0, "P")),
        _master(_opt(date(2026, 6, 19), 105.0, "P")),
        _master(_opt(date(2026, 9, 18), 200.0, "C")),
        _master(_opt(date(2026, 9, 18), 205.0, "C")),
        _master(_opt(date(2026, 9, 18), 210.0, "C")),
        _master(_opt(date(2028, 6, 15), 300.0, "P")),
    ]
    ctx.store.write("instrument_master", masters)
    ctx.store.write(
        "qc_results",
        [
            _qc(
                "tenor_coverage_floor",
                "fail",
                '{"underlying":"SPX","breaching_tenors":'
                '[{"tenor":"1m","measured":0,"floor":5},{"tenor":"3m","measured":3,"floor":5}]}',
            ),
            _qc("delta_band_completeness", "fail"),
        ],
    )

    with TestClient(create_app(ctx)) as client:
        payload = client.get(
            "/api/coverage", params={"underlying": "SPX", "trade_date": "2026-06-11"}
        ).json()

    assert payload["underlying"] == "SPX"
    assert payload["trade_date"] == "2026-06-11"
    assert payload["n_expiries"] == 3

    by_expiry = {row["expiry"]: row for row in payload["expiries"]}
    assert [row["expiry"] for row in payload["expiries"]] == [
        "2026-06-19", "2026-09-18", "2028-06-15"
    ]  # chronological
    a = by_expiry["2026-06-19"]
    assert (a["n_strikes"], a["n_calls"], a["n_puts"]) == (2, 2, 2)
    assert (a["strike_min"], a["strike_max"]) == (100.0, 105.0)
    assert a["tenor"] == "10d"  # nearest pinned target to 2026-06-19 from 2026-06-11
    b = by_expiry["2026-09-18"]
    assert (b["n_strikes"], b["n_calls"], b["n_puts"]) == (3, 3, 0)
    assert b["tenor"] == "3m"
    c = by_expiry["2028-06-15"]
    assert (c["n_strikes"], c["n_calls"], c["n_puts"]) == (1, 0, 1)
    assert c["tenor"] == "2y"

    # Per-tenor coverage spans the WHOLE pinned grid; the empty/thin tenors show, not omitted.
    tenors = {row["tenor"]: row for row in payload["tenors"]}
    assert set(tenors) == {"10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"}
    assert tenors["1m"]["status"] == "fail" and tenors["1m"]["measured"] == 0
    assert tenors["3m"]["status"] == "fail" and tenors["3m"]["measured"] == 3
    assert tenors["10d"]["status"] == "pass"
    assert tenors["2y"]["status"] == "pass"

    assert payload["qc_status"] == "fail"
    assert payload["delta_band_status"] == "fail"
