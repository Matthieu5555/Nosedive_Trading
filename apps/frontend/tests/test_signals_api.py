from __future__ import annotations

from datetime import UTC, date, datetime

from algotrading.core.provenance import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import StrategySignal
from fastapi.testclient import TestClient

_INDEX = "SX5E"
_PROVIDER = "IBKR"
_SNAP_1 = datetime(2026, 6, 15, 15, 30, tzinfo=UTC)
_SNAP_2 = datetime(2026, 6, 16, 15, 30, tzinfo=UTC)
_DATE_1 = date(2026, 6, 15)
_DATE_2 = date(2026, 6, 16)


def _prov() -> object:
    return stamp(
        calc_ts=_SNAP_2,
        code_version="signal-layer-1",
        config_hashes={"signals": "sig-test"},
        source_records=(source_ref("projected_option_analytics", "sess", _INDEX),),
        source_timestamps=(_SNAP_2,),
    )


def _signal(
    *,
    snapshot_ts: datetime,
    signal_kind: str,
    subject: str,
    tenor_label: str,
    value: float,
) -> StrategySignal:
    return StrategySignal(
        snapshot_ts=snapshot_ts,
        provider=_PROVIDER,
        underlying=_INDEX,
        signal_kind=signal_kind,
        subject=subject,
        tenor_label=tenor_label,
        value=value,
        source_snapshot_ts=snapshot_ts,
        provenance=_prov(),
    )


_SEED_ROWS = (
    _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="iv_rank",
        subject="SX5E",
        tenor_label="3m",
        value=0.24004,
    ),
    _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="iv_rank",
        subject="ASML",
        tenor_label="3m",
        value=0.71,
    ),
    _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="iv_vs_realized",
        subject="SX5E",
        tenor_label="3m",
        value=0.00533,
    ),
    _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="term_structure_slope",
        subject="SX5E",
        tenor_label="1m:6m",
        value=0.02244,
    ),
    _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="implied_correlation",
        subject="SX5E",
        tenor_label="10d",
        value=-0.05294,
    ),
)

_EXPECTED_UNITS = {
    "iv_rank": "fraction [0,1]",
    "iv_vs_realized": "vol points (annualized)",
    "term_structure_slope": "vol points (back − front)",
    "implied_correlation": "correlation [-1,1]",
}
_EXPECTED_LABELS = {
    "iv_rank": "IV rank",
    "iv_vs_realized": "Realized − implied",
    "term_structure_slope": "Term-structure slope",
    "implied_correlation": "Implied correlation (ρ)",
}


def _client(ctx: AppContext) -> TestClient:
    return TestClient(create_app(ctx))


def test_signals_empty_store_is_well_formed(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/signals", params={"underlying": _INDEX}).json()
    assert payload["n_signals"] == 0
    assert payload["signals"] == []
    assert payload["by_kind"] == {}
    assert payload["kinds"] == []
    assert payload["snapshot_ts"] is None


def test_signals_bad_trade_date_is_400(infra_client: TestClient) -> None:
    response = infra_client.get("/api/signals", params={"trade_date": "not-a-date"})
    assert response.status_code == 400
    assert response.json()["error"] == "bad_trade_date"


def test_signals_underlyings_lists_index_not_subjects(ctx: AppContext) -> None:
    ctx.store.write("strategy_signals", list(_SEED_ROWS))
    with _client(ctx) as client:
        payload = client.get("/api/signals/underlyings").json()
    assert payload["underlyings"] == [_INDEX], (
        "underlyings is the index 'underlying' column, never the per-name subjects (ASML)"
    )


def test_signals_populated_shape_units_and_grouping(ctx: AppContext) -> None:
    ctx.store.write("strategy_signals", list(_SEED_ROWS))
    with _client(ctx) as client:
        payload = client.get(
            "/api/signals", params={"underlying": _INDEX, "trade_date": "2026-06-16"}
        ).json()

    assert payload["underlying"] == _INDEX
    assert payload["trade_date"] == "2026-06-16"
    assert payload["snapshot_ts"] == "2026-06-16T15:30:00+00:00"
    assert payload["n_signals"] == len(_SEED_ROWS)

    assert set(payload["kinds"]) == set(_EXPECTED_UNITS), (
        "the four persisted signal kinds must all surface"
    )

    by_kind = payload["by_kind"]
    assert {k: len(v) for k, v in by_kind.items()} == {
        "iv_rank": 2,
        "iv_vs_realized": 1,
        "term_structure_slope": 1,
        "implied_correlation": 1,
    }, "by_kind partitions the rows exactly by signal_kind"

    sx5e_rank = next(
        s
        for s in by_kind["iv_rank"]
        if s["subject"] == "SX5E"
    )
    assert sx5e_rank["value"] == 0.24004
    assert sx5e_rank["tenor_label"] == "3m"
    assert sx5e_rank["unit"] == _EXPECTED_UNITS["iv_rank"]
    assert sx5e_rank["label"] == _EXPECTED_LABELS["iv_rank"]
    assert sx5e_rank["provenance"]["code_version"] == "signal-layer-1"

    for kind, items in by_kind.items():
        for item in items:
            assert item["unit"] == _EXPECTED_UNITS[kind]
            assert item["label"] == _EXPECTED_LABELS[kind]


def test_signals_latest_partition_resolution(ctx: AppContext) -> None:
    older = _signal(
        snapshot_ts=_SNAP_1,
        signal_kind="iv_rank",
        subject="SX5E",
        tenor_label="3m",
        value=0.10,
    )
    ctx.store.write("strategy_signals", [older])
    ctx.store.write("strategy_signals", list(_SEED_ROWS))

    with _client(ctx) as client:
        payload = client.get("/api/signals", params={"underlying": _INDEX}).json()

    assert payload["trade_date"] == "2026-06-16", (
        "no trade_date resolves the latest persisted partition (2026-06-16, not 2026-06-15)"
    )
    sx5e_rank = next(s for s in payload["by_kind"]["iv_rank"] if s["subject"] == "SX5E")
    assert sx5e_rank["value"] == 0.24004


def test_signal_serializer_unknown_kind_falls_back_to_null_unit() -> None:
    from algotrading.frontend.serializers import strategy_signal_to_dict

    row = _signal(
        snapshot_ts=_SNAP_2,
        signal_kind="unmodeled_future_kind",
        subject="SX5E",
        tenor_label="3m",
        value=1.23,
    )
    out = strategy_signal_to_dict(row)
    assert out["unit"] is None, "an unknown kind serializes with a null unit, never raising"
    assert out["label"] == "unmodeled_future_kind"
    assert out["value"] == 1.23
