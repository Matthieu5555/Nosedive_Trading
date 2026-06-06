"""Real persist -> read-back path: BFF routers over a seeded ParquetStore.

Until C6 lands the live-capture build path, the BFF's "produce a surface then read it
back" loop is exercised here at the seam that matters: we *persist* real contract rows
(``surface_parameters``, ``risk_aggregates``, ``scenario_results``) through
``ParquetStore.write`` — exactly the table contracts the actor pipeline emits — and assert
the surfaces / risk / health routers read them back faithfully through the same store.

Expected values are derived independently: the SVI/Greek numbers are hand-chosen inputs
written into the rows, and the assertions check the routers surface *those* values (and
their provenance) unchanged — not numbers copied from BFF output. The rows are minimal and
internally consistent (one underlying, one maturity, one portfolio group, one scenario
cell) so each assertion pins a single contract field crossing the BFF<->infra seam.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend import runner
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 5, 29)
EXPIRY = date(2026, 8, 28)
UNDERLYING = "AAPL"
MATURITY_YEARS = 0.25
PORTFOLIO_ID = "pf-readback"

# Hand-chosen SVI + Greek values written into the seeded rows. The assertions check the
# routers echo these exact numbers back — the independent oracle is "what we wrote in".
SVI_A = 0.0123
SVI_B = 0.3400
SVI_RHO = -0.2100
SVI_M = 0.0
SVI_SIGMA = 0.1800
NET_DELTA = 123.45
NET_VEGA = 89.0
SCENARIO_PNL = -4567.89

UNDERLYING_KEY = InstrumentKey(
    underlying_symbol=UNDERLYING,
    security_type="STK",
    exchange="SMART",
    currency="USD",
    multiplier=1.0,
    broker_contract_id="u-AAPL",
)
CALL_100 = InstrumentKey(
    underlying_symbol=UNDERLYING,
    security_type="OPT",
    exchange="SMART",
    currency="USD",
    multiplier=100.0,
    broker_contract_id="o-C-100",
    expiry=EXPIRY,
    strike=100.0,
    option_right="C",
)


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="readback-test",
        config_hashes={"cfg": "cfg-readback"},
        source_records=(source_ref("raw_market_events", "sess-readback", source),),
        source_timestamps=(AS_OF,),
    )


def _snapshot(instrument: InstrumentKey, mid: float) -> tables.MarketStateSnapshot:
    return tables.MarketStateSnapshot(
        snapshot_ts=AS_OF,
        instrument_key=instrument.canonical(),
        reference_spot=100.0,
        bid=mid - 0.05,
        ask=mid + 0.05,
        last=mid,
        spread_pct=0.001,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=TRADE_DATE,
        underlying=UNDERLYING,
        provenance=_prov(f"snap:{instrument.broker_contract_id}"),
    )


def _seed_store(root: Path) -> None:
    store = ParquetStore(root)
    # A raw snapshot for the underlying/date so build_dashboard sees data flowing and can
    # match the surface partition to a raw underlying (surfaces_building -> ok).
    store.write(
        "market_state_snapshots",
        [_snapshot(UNDERLYING_KEY, 100.0), _snapshot(CALL_100, 3.99)],
    )
    store.write(
        "surface_parameters",
        [
            tables.SurfaceParameters(
                snapshot_ts=AS_OF,
                underlying=UNDERLYING,
                maturity_years=MATURITY_YEARS,
                model_version="svi-readback",
                svi_a=SVI_A,
                svi_b=SVI_B,
                svi_rho=SVI_RHO,
                svi_m=SVI_M,
                svi_sigma=SVI_SIGMA,
                expiry_date=EXPIRY,
                day_count="ACT/365",
                diagnostics=SurfaceFitDiagnostics(rmse=0.0009, n_points=11, arb_free=True),
                source_snapshot_ts=AS_OF,
                provenance=_prov("surface:AAPL"),
            )
        ],
    )
    store.write(
        "risk_aggregates",
        [
            tables.RiskAggregate(
                valuation_ts=AS_OF,
                portfolio_id=PORTFOLIO_ID,
                group_key=UNDERLYING,
                net_delta=NET_DELTA,
                net_gamma=6.7,
                net_vega=NET_VEGA,
                net_theta=-12.3,
                source_snapshot_ts=AS_OF,
                provenance=_prov("risk:AAPL"),
            )
        ],
    )
    store.write(
        "scenario_results",
        [
            tables.ScenarioResult(
                valuation_ts=AS_OF,
                portfolio_id=PORTFOLIO_ID,
                scenario_id="spot-down-10",
                contract_key=CALL_100.canonical(),
                spot_shock=-0.10,
                vol_shock=0.0,
                time_shock=0.0,
                pnl=SCENARIO_PNL,
                scenario_version="scn-1",
                source_snapshot_ts=AS_OF,
                provenance=_prov("scenario:AAPL"),
            )
        ],
    )


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over the BFF wired to a store pre-seeded with real contract rows."""
    store_root = tmp_path / "data"
    _seed_store(store_root)
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as client:
        yield client


def test_surfaces_router_reads_back_persisted_svi_slice(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/surfaces", params={"underlying": UNDERLYING}).json()
    assert payload["underlying"] == UNDERLYING
    assert payload["n_slices"] == 1
    slice_row = payload["slices"][0]
    assert slice_row["maturity_years"] == pytest.approx(MATURITY_YEARS)
    assert slice_row["svi_b"] == pytest.approx(SVI_B)
    assert slice_row["svi_sigma"] == pytest.approx(SVI_SIGMA)
    assert slice_row["diagnostics"]["arb_free"] is True
    # Provenance carried through to the UI: the stamp we wrote round-trips.
    assert slice_row["provenance"]["code_version"] == "readback-test"
    assert slice_row["provenance"]["stamp_hash"]


def test_surfaces_underlyings_lists_the_persisted_underlying(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/surfaces/underlyings").json()
    assert payload["underlyings"] == [UNDERLYING]


def test_risk_router_reads_back_persisted_aggregate(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/risk").json()
    assert payload["n_aggregates"] == 1
    agg = payload["aggregates"][0]
    assert agg["portfolio_id"] == PORTFOLIO_ID
    assert agg["group_key"] == UNDERLYING
    assert agg["net_delta"] == pytest.approx(NET_DELTA)
    assert agg["net_vega"] == pytest.approx(NET_VEGA)
    assert agg["provenance"]["config_hashes"] == {"cfg": "cfg-readback"}


def test_risk_portfolios_lists_the_persisted_portfolio(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/risk/portfolios").json()
    assert payload["portfolios"] == [PORTFOLIO_ID]


def test_risk_scenarios_read_back_persisted_cell(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/api/risk/scenarios").json()
    assert payload["n_cells"] == 1
    cell = payload["cells"][0]
    assert cell["scenario_id"] == "spot-down-10"
    assert cell["spot_shock"] == pytest.approx(-0.10)
    assert cell["pnl"] == pytest.approx(SCENARIO_PNL)


def test_risk_portfolio_filter_selects_the_seeded_portfolio(seeded_client: TestClient) -> None:
    hit = seeded_client.get("/api/risk", params={"portfolio_id": PORTFOLIO_ID}).json()
    assert hit["n_aggregates"] == 1
    miss = seeded_client.get("/api/risk", params={"portfolio_id": "nope"}).json()
    assert miss["n_aggregates"] == 0


def test_health_reflects_surfaces_and_scenarios_after_persist(seeded_client: TestClient) -> None:
    # build_dashboard reads the snapshot, surface, and scenario partitions seeded for
    # TRADE_DATE. With a raw snapshot present, data is flowing; with a surface partition
    # covering that underlying, surfaces are building; with a scenario partition present,
    # scenarios are current. Oracle: infra/orchestration/dashboard.py's flag rules.
    payload = seeded_client.get("/api/health", params={"trade_date": TRADE_DATE.isoformat()}).json()
    assert payload["trade_date"] == TRADE_DATE.isoformat()
    assert payload["data_flowing"] == "ok"
    assert payload["surfaces_building"] == "ok"
    assert payload["scenarios_current"] == "current"
