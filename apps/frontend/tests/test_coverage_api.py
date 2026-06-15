"""Coverage router tests: per-expiry capture + per-tenor QC coverage, store-backed.

The router reads ``instrument_master`` (the captured chain) and ``qc_results`` (WS 1H's
``tenor_coverage_floor`` / ``delta_band_completeness``) — no recompute. An empty store returns a
labeled empty payload (never a 500); a malformed ``trade_date`` is a 400. The populated case seeds a
hand-counted chain + a hand-built QC verdict and asserts the counts and per-tenor coverage the front
will render, so a passing assertion is real agreement, not a round-trip.

Volume tests (TARGET §7 #7) cover:
* ``total_volume`` in expiry rows is the sum of per-contract option volumes from
  ``market_state_snapshots`` (additive-nullable: null when no contracts reported volume).
* ``_volume_by_expiry`` sums correctly per expiry and skips None-volume contracts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.core.provenance import stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    MarketStateSnapshot,
    QcResult,
)
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


# ---------------------------------------------------------------------------
# Volume tests (TARGET §7 #7) — ``total_volume`` in expiry rows
# ---------------------------------------------------------------------------

_PROV = stamp(
    calc_ts=_RUN_TS,
    code_version="test-snap-1.0.0",
    config_hashes={"qc": "qc-test"},
    source_records=(),
    source_timestamps=(),
)


def _snap(key: InstrumentKey, volume: float | None = None) -> MarketStateSnapshot:
    """Minimal ``MarketStateSnapshot`` for the coverage volume test."""
    return MarketStateSnapshot(
        snapshot_ts=_RUN_TS,
        instrument_key=key.canonical(),
        reference_spot=100.0,
        bid=9.90,
        ask=10.10,
        last=10.0,
        spread_pct=0.02,
        reference_type="mid",
        flags=("open",),
        completeness=1.0,
        trade_date=_TRADE_DATE,
        underlying="SPX",
        provenance=_PROV,
        volume=volume,
    )


def test_coverage_total_volume_sums_by_expiry(ctx: AppContext) -> None:
    """``total_volume`` in expiry rows is the per-expiry sum of ``market_state_snapshots.volume``.

    Independent oracle (hand-summed):
      * 2026-06-19 has two contracts: 300 + 500 = 800 total.
      * 2026-09-18 has one contract with volume=None → total_volume must be null (absent from map).
    The mapping skips contracts with None volume, so a lone None-volume expiry is null, not 0.
    """
    expiry_a = date(2026, 6, 19)
    expiry_b = date(2026, 9, 18)

    opt_a1 = _opt(expiry_a, 100.0, "C")
    opt_a2 = _opt(expiry_a, 105.0, "C")
    opt_b1 = _opt(expiry_b, 200.0, "C")

    masters = [_master(opt_a1), _master(opt_a2), _master(opt_b1)]
    snapshots = [
        _snap(opt_a1, volume=300.0),  # expiry 2026-06-19 → partial sum 300
        _snap(opt_a2, volume=500.0),  # expiry 2026-06-19 → partial sum 500; total 800
        _snap(opt_b1, volume=None),   # expiry 2026-09-18 → no volume → total_volume null
    ]

    ctx.store.write("instrument_master", masters)
    ctx.store.write("market_state_snapshots", snapshots)

    with TestClient(create_app(ctx)) as client:
        payload = client.get(
            "/api/coverage", params={"underlying": "SPX", "trade_date": "2026-06-11"}
        ).json()

    by_expiry = {row["expiry"]: row for row in payload["expiries"]}

    # 2026-06-19: 300 + 500 = 800 (independently hand-summed)
    assert by_expiry["2026-06-19"]["total_volume"] == 800.0, (
        "total_volume should sum 300 + 500 = 800 for two contracts in expiry 2026-06-19"
    )
    # 2026-09-18: lone contract has volume=None → null in response
    assert by_expiry["2026-09-18"]["total_volume"] is None, (
        "total_volume must be null when all contracts in an expiry report None volume"
    )


def test_coverage_total_volume_is_null_with_no_snapshots(ctx: AppContext) -> None:
    """When ``market_state_snapshots`` has no data for this date, ``total_volume`` is null.

    Additive-nullable guarantee: captures written before the volume lane was active
    have no snapshot records at all; the expiry rows must still be present with null volumes.
    """
    opt_a = _opt(date(2026, 6, 19), 100.0, "C")
    ctx.store.write("instrument_master", [_master(opt_a)])
    # No market_state_snapshots written → empty read → volume_by_expiry = {}

    with TestClient(create_app(ctx)) as client:
        payload = client.get(
            "/api/coverage", params={"underlying": "SPX", "trade_date": "2026-06-11"}
        ).json()

    by_expiry = {row["expiry"]: row for row in payload["expiries"]}
    assert by_expiry["2026-06-19"]["total_volume"] is None


def test_coverage_volume_by_expiry_unit() -> None:
    """Unit test for ``_volume_by_expiry`` directly (no I/O, no BFF wire).

    Independently-derived expected values:
      * Two contracts in expiry "2026-06-19" with volumes 100.0 and 250.5 → sum = 350.5
      * One contract in expiry "2026-09-18" with volume None → absent from map
      * One contract with no expiry segment (the underlying leg, key has no expiry YYYY-MM-DD)
        → skipped (not a tradable option row)
    """
    from algotrading.frontend.routers.coverage import _volume_by_expiry

    class FakeSnap:
        def __init__(self, key: str, volume: float | None) -> None:
            self.instrument_key = key
            self.volume = volume

    snaps = [
        # 2026-06-19 contracts
        FakeSnap("SX5E|OPT|EUREX|EUR|100.0|11111|2026-06-19|4800.0|C", 100.0),
        FakeSnap("SX5E|OPT|EUREX|EUR|100.0|22222|2026-06-19|4850.0|P", 250.5),
        # 2026-09-18 contract — volume None → skipped
        FakeSnap("SX5E|OPT|EUREX|EUR|100.0|33333|2026-09-18|5000.0|C", None),
        # Underlying leg — no expiry segment (empty 7th segment) → skipped
        FakeSnap("SX5E|STK|EUREX|EUR|1.0|99999||0.0|", 500.0),
    ]

    result = _volume_by_expiry(snaps)

    # 2026-06-19: 100.0 + 250.5 = 350.5 (hand-summed)
    assert result == {"2026-06-19": 350.5}, (
        f"Expected {{'2026-06-19': 350.5}}, got {result}"
    )
