"""The demo-book population path: DEMO_BOOK=1 fills the Positions, Risk and
Reconciliation surfaces; unset leaves every endpoint graceful-empty.

The fixture analytics are synthesized in-memory (never read from the canonical
store) and written to a per-test temp store, so the test exercises the real
compute (``demo_populate`` -> ``basket_scenarios`` -> ``infra.risk`` engine)
without any dependency on banked data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.frontend.demo_populate import (
    DEMO_FLAG,
    DEMO_PORTFOLIO_ID,
    ensure_demo_book,
    populate_store,
)
from algotrading.infra.contracts import ProjectedOptionAnalytics
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS_DIR = REPO_ROOT / "configs"

TRADE_DATES = (date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17))
UNDERLYING = "SX5E"

# A book leg picks the most-preferred (tenor, band); these three cells give the
# vol-seller its ATM short + put-wing long + call-wing short.
_BANDS = (
    ("atm", 0.5, 6300.0, 100.0, 0.5),
    ("10dp", -0.10, 5800.0, 15.0, -0.10),
    ("10dc", 0.10, 6800.0, 12.0, 0.10),
)


def _prov(as_of: datetime) -> ProvenanceStamp:
    return stamp(
        calc_ts=as_of,
        code_version="demo-fixture",
        config_hashes={"cfg": "demo-fixture"},
        source_records=(source_ref("market_state_snapshots", "fx", "fixture"),),
        source_timestamps=(as_of,),
    )


def _analytics_row(
    *, as_of: datetime, band: str, target_delta: float, strike: float, price: float
) -> ProjectedOptionAnalytics:
    forward = 6300.0
    return ProjectedOptionAnalytics(
        snapshot_ts=as_of,
        provider="IBKR",
        underlying=UNDERLYING,
        tenor_label="1m",
        maturity_years=0.0833,
        delta_band=band,
        target_delta=target_delta,
        log_moneyness=0.0,
        strike=strike,
        forward_price=forward,
        implied_vol=0.18,
        total_variance=0.005,
        price=price,
        delta=target_delta,
        gamma=0.001,
        vega=300.0,
        theta=-100.0,
        rho=-0.5,
        dollar_delta=target_delta * forward,
        dollar_gamma=10.0,
        dollar_vega=3.0,
        dollar_delta_unit="$ per $1 of underlying",
        dollar_gamma_unit="$ per 1% move",
        dollar_vega_unit="$ per 1 vol point",
        model_version="svi-fixture",
        pricer_version="b76-fixture",
        source_snapshot_ts=as_of,
        provenance=_prov(as_of),
        dollar_theta=-2.0,
        dollar_rho=-0.01,
        dollar_theta_unit="$ per calendar day",
        dollar_rho_unit="$ per 1% rate",
        surface_side="combined",
    )


def _seed_analytics(store: ParquetStore) -> None:
    for trade_date in TRADE_DATES:
        as_of = datetime(
            trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=UTC
        )
        store.write(
            "projected_option_analytics",
            [
                _analytics_row(
                    as_of=as_of,
                    band=band,
                    target_delta=target_delta,
                    strike=strike,
                    price=price,
                )
                for band, target_delta, strike, price, _delta in _BANDS
            ],
        )


def _context(store_root: Path) -> AppContext:
    return AppContext(
        store_root=store_root,
        configs_dir=CONFIGS_DIR,
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )


@pytest.fixture
def populated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store_root.mkdir(parents=True)
    store = ParquetStore(store_root)
    _seed_analytics(store)
    monkeypatch.setenv(DEMO_FLAG, "1")
    ctx = _context(store_root)
    with TestClient(create_app(ctx)) as client:
        yield client


@pytest.fixture
def unset_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store_root.mkdir(parents=True)
    _seed_analytics(ParquetStore(store_root))
    monkeypatch.delenv(DEMO_FLAG, raising=False)
    ctx = _context(store_root)
    with TestClient(create_app(ctx)) as client:
        yield client


def test_demo_flag_unset_leaves_every_surface_empty(unset_client: TestClient) -> None:
    # Analytics are present, but without DEMO_BOOK the writer is a no-op: the
    # position/risk/recon surfaces stay graceful-empty, unchanged from real-book.
    assert unset_client.get("/api/positions").json()["n_lines"] == 0
    assert unset_client.get("/api/positions/fills").json()["n_fills"] == 0
    assert unset_client.get("/api/risk").json()["n_aggregates"] == 0
    assert unset_client.get("/api/risk/scenarios").json()["n_cells"] == 0
    assert unset_client.get("/api/risk/portfolios").json()["portfolios"] == []
    recon = unset_client.get("/api/reconciliation")
    assert recon.status_code == 400
    assert recon.json()["error"] == "no_broker_account"


def test_demo_flag_populates_positions_book(populated_client: TestClient) -> None:
    body = populated_client.get(
        "/api/positions", params={"trade_date": "2026-06-17"}
    ).json()
    assert body["n_lines"] == 3
    assert body["priced_contract_keys"] == 3
    assert body["unpriced_contract_keys"] == []
    rights = sorted(line["option_right"] for line in body["lines"])
    assert rights == ["C", "C", "P"]
    # ATM call is sold (10 lots short), put wing is long, call wing is short.
    by_qty = sorted(line["quantity"] for line in body["lines"])
    assert by_qty == [-10.0, -5.0, 5.0]


def test_demo_book_greeks_are_real_engine_numbers(populated_client: TestClient) -> None:
    body = populated_client.get(
        "/api/positions", params={"trade_date": "2026-06-17"}
    ).json()
    # The book is a net vol seller: short ATM dominates, so net vega is negative.
    assert body["book"]["vega"]["dollar"] < 0.0
    assert body["book"]["market_value"] != 0.0
    for line in body["lines"]:
        assert line["mark_price"] > 0.0


def test_demo_fills_ledger_is_booked_per_date(populated_client: TestClient) -> None:
    all_fills = populated_client.get("/api/positions/fills").json()
    assert all_fills["n_fills"] == 9  # 3 legs x 3 dates
    one_day = populated_client.get(
        "/api/positions/fills", params={"trade_date": "2026-06-16"}
    ).json()
    assert one_day["n_fills"] == 3
    assert all(f["source_basket_id"] == DEMO_PORTFOLIO_ID for f in one_day["fills"])


def test_demo_risk_aggregate_is_per_date_and_labelled(populated_client: TestClient) -> None:
    payload = populated_client.get(
        "/api/risk", params={"portfolio_id": DEMO_PORTFOLIO_ID}
    ).json()
    assert payload["n_aggregates"] == len(TRADE_DATES)
    agg = payload["aggregates"][0]
    assert agg["portfolio_id"] == DEMO_PORTFOLIO_ID
    assert agg["group_key"] == "underlying:SX5E"
    assert agg["net_vega"] < 0.0  # vol seller
    assert payload["portfolio_id"] == DEMO_PORTFOLIO_ID


def test_demo_portfolio_is_listed(populated_client: TestClient) -> None:
    assert populated_client.get("/api/risk/portfolios").json()["portfolios"] == [
        DEMO_PORTFOLIO_ID
    ]


def test_demo_scenarios_carry_surface_named_and_rate(populated_client: TestClient) -> None:
    payload = populated_client.get(
        "/api/risk/scenarios", params={"portfolio_id": DEMO_PORTFOLIO_ID}
    ).json()
    assert payload["n_cells"] > 0
    surface = payload["surface"]
    assert surface["n_cells"] > 0
    assert surface["spot_shock"] and surface["vol_shock"]
    assert surface["scenario_version"]
    assert payload["n_named"] >= 1
    assert payload["n_rate"] >= 1


def test_demo_reconciliation_returns_a_report(populated_client: TestClient) -> None:
    recon = populated_client.get("/api/reconciliation")
    assert recon.status_code == 200
    body = recon.json()
    assert body["account_id"]
    assert "positions" in body
    assert "cash" in body


def test_ensure_demo_book_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_root = tmp_path / "data"
    store_root.mkdir(parents=True)
    _seed_analytics(ParquetStore(store_root))
    monkeypatch.setenv(DEMO_FLAG, "1")
    ctx = _context(store_root)
    first = ensure_demo_book(ctx)
    assert first  # populated on first call
    second = ensure_demo_book(ctx)
    assert second == []  # already populated => no-op
    # The risk aggregates were not duplicated.
    aggs = [
        row
        for row in ctx.store.read("risk_aggregates")
        if row.portfolio_id == DEMO_PORTFOLIO_ID
    ]
    assert len(aggs) == len(TRADE_DATES)


def test_populate_store_skips_dates_without_analytics(tmp_path: Path) -> None:
    store_root = tmp_path / "data"
    store_root.mkdir(parents=True)
    store = ParquetStore(store_root)
    # No analytics seeded at all -> nothing to build, empty report, no writes.
    from algotrading.core.config import load_platform_config

    reports = populate_store(store, store_root, load_platform_config(CONFIGS_DIR))
    assert reports == []
    assert store.read("risk_aggregates") == []
