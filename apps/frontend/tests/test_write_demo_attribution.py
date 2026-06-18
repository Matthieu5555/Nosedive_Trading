"""The demo-attribution writer must populate scenario_attributions readable by /api/attribution.

Seeds a synthetic fixed-expiry chain in a temp store, runs the writer's row builder, writes the
rows, and confirms /api/attribution (which previously read an empty table) now returns the book
waterfall with the per-day terms + residual + verdict the engine produced.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_WRITER_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "write_demo_attribution.py"
)
_spec = importlib.util.spec_from_file_location("write_demo_attribution", _WRITER_PATH)
assert _spec is not None and _spec.loader is not None
writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(writer)

UNDERLYING = "SX5E"
EXPIRY = date(2026, 9, 18)
D0 = date(2026, 6, 15)
D1 = date(2026, 6, 16)
D2 = date(2026, 6, 17)
STRIKES = (6200.0, 6275.0, 6300.0, 6325.0, 6400.0)
FORWARD = {D0: 6300.0, D1: 6320.0, D2: 6340.0}
IV = {D0: 0.16, D1: 0.162, D2: 0.165}


def _ts(as_of: date) -> datetime:
    return datetime(as_of.year, as_of.month, as_of.day, 15, 30, tzinfo=UTC)


def _stamp(as_of: date) -> object:
    return stamp(
        calc_ts=_ts(as_of),
        code_version="writer-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("iv_points", UNDERLYING, as_of.isoformat()),),
        source_timestamps=(_ts(as_of),),
    )


def _iv_point(as_of: date, strike: float, right: str) -> IvPoint:
    log_moneyness = math.log(strike / FORWARD[as_of])
    iv = IV[as_of]
    key = (
        f"{UNDERLYING}|OPT|EUREX|EUR|100|{int(strike)}|"
        f"{EXPIRY.isoformat()}|{int(strike)}|{right}"
    )
    return IvPoint(
        snapshot_ts=_ts(as_of),
        contract_key=key,
        implied_vol=iv,
        log_moneyness=log_moneyness,
        total_variance=iv * iv * ((EXPIRY - as_of).days / 365.0),
        solver_version="test",
        diagnostics=IvDiagnostics(converged=True, iterations=1, residual=0.0, status="ok"),
        source_snapshot_ts=_ts(as_of),
        provenance=_stamp(as_of),
    )


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    store = ParquetStore(root)
    for as_of in (D0, D1, D2):
        store.write(
            "iv_points",
            [_iv_point(as_of, k, r) for k in STRIKES for r in ("C", "P")],
        )
    return root


def test_writer_builds_book_and_position_rows_for_each_day_step(store_root: Path) -> None:
    store = ParquetStore(store_root)
    spec, rows = writer.build_rows(store)
    # 3 banked dates -> 2 day-steps; each step = 1 book row + 2 straddle-leg position rows.
    assert len(rows) == 6
    book_rows = [r for r in rows if r.level == "book"]
    position_rows = [r for r in rows if r.level == "position"]
    assert len(book_rows) == 2
    assert len(position_rows) == 4
    for row in book_rows:
        assert row.portfolio_id == spec.portfolio_id
        assert row.approx_pnl + row.residual == pytest.approx(row.full_reprice_pnl, abs=1e-6)
        assert row.theta_pnl < 0.0  # the fixed-expiry roll makes theta real


def test_written_rows_are_readable_via_attribution_endpoint(store_root: Path) -> None:
    _spec_book, rows = writer.build_rows(ParquetStore(store_root))
    ParquetStore(store_root).write("scenario_attributions", rows)

    ctx = AppContext(
        store_root=store_root,
        configs_dir=store_root.parent / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    with TestClient(create_app(ctx)) as client:
        body = client.get(
            "/api/attribution",
            params={"trade_date": D1.isoformat(), "portfolio_id": "demo-sep-straddle"},
        ).json()
    assert body["found"] is True
    assert body["level"] == "book"
    names = [term["name"] for term in body["terms"]]
    assert names[:4] == ["Delta", "Gamma", "Vega", "Theta"]
    assert body["residual"]["dollars"] is not None
    assert body["verdict"]["within_tolerance"] in (True, False)


def test_writer_temp_validation_round_trips(store_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "validate"
    written = writer._write_and_read_back(ParquetStore(store_root), target)
    assert written == 6
