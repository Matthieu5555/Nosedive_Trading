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
from algotrading.infra.orchestration.run_state import (
    EOD_STAGES,
    OUTCOME_FAILED,
    OUTCOME_OK,
    StageRun,
    record_stage,
)
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

# Hand-chosen dollar Greeks written into a pricing_results row; the metrics endpoint must
# echo these back beside their raw per-unit values and a non-empty unit string.
PR_DOLLAR_DELTA = 55.0
PR_DOLLAR_GAMMA = 8.0
PR_DOLLAR_VEGA = 0.10
PR_DOLLAR_THETA = -0.0000274
PR_DOLLAR_RHO = 0.0003

# Hand-chosen daily OHLC bars for the index members. The price-history endpoint must echo
# these exact values back; the constituent price-first ordering keys off the latest close.
# AAA's latest close (192.0) > BBB's (45.5), so AAA must sort first.
INDEX = "TESTIDX"
MEMBER_AAA = "AAA"
MEMBER_BBB = "BBB"
AAA_BARS = [
    (date(2026, 5, 28), 188.0, 191.0, 187.0, 190.0, 1_000_000.0),
    (date(2026, 5, 29), 190.0, 193.5, 189.5, 192.0, 1_200_000.0),
]
BBB_BARS = [
    (date(2026, 5, 28), 44.0, 46.0, 43.5, 45.0, 500_000.0),
    (date(2026, 5, 29), 45.0, 46.2, 44.8, 45.5, 600_000.0),
]
# AAA's bar on 2026-05-29: the field-name conformance + read-back oracle.
AAA_29_OPEN = 190.0
AAA_29_HIGH = 193.5
AAA_29_LOW = 189.5
AAA_29_CLOSE = 192.0
AAA_29_VOLUME = 1_200_000.0

# Hand-chosen projected-analytics cell values for AAA, one maturity (3M), two band points
# (a 30Δ put and a 30Δ call). The analytics endpoint must echo these back with the stored
# unit strings; the smile is ordered by delta (put first).
AN_FORWARD = 195.0
AN_PUT_IV = 0.2700
AN_PUT_LOGM = -0.1500
AN_PUT_DELTA = -0.30
AN_CALL_IV = 0.2300
AN_CALL_LOGM = 0.1200
AN_CALL_DELTA = 0.30
AN_PUT_DOLLAR_DELTA = -58.5
AN_CALL_DOLLAR_DELTA = 58.5
AN_DOLLAR_DELTA_UNIT = "$ per $1 of underlying"
AN_DOLLAR_GAMMA_UNIT = "$ per 1% move"
AN_DOLLAR_VEGA_UNIT = "$ per 1 vol point"
AN_DOLLAR_THETA_UNIT = "$ per calendar day"
AN_DOLLAR_RHO_UNIT = "$ per 1% rate"

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


def _daily_bar(underlying: str, row: tuple[date, float, float, float, float, float]) -> tables.DailyBar:
    trade_date, open_, high, low, close, volume = row
    return tables.DailyBar(
        provider="IBKR",
        underlying=underlying,
        trade_date=trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        bar_type="1d-TRADES",
        source="readback-test",
        provenance=_prov(f"bar:{underlying}:{trade_date.isoformat()}"),
    )


def _constituent(
    constituent: str,
    weight: float,
    add: date,
    remove: date | None,
    knowledge: date,
) -> tables.IndexConstituent:
    return tables.IndexConstituent(
        index=INDEX,
        constituent=constituent,
        effective_add_date=add,
        effective_remove_date=remove,
        knowledge_date=knowledge,
        vendor="Siblis",
        weight=weight,
    )


def _analytics_cell(
    *,
    delta_band: str,
    target_delta: float,
    log_moneyness: float,
    implied_vol: float,
    delta: float,
    dollar_delta: float,
) -> tables.ProjectedOptionAnalytics:
    return tables.ProjectedOptionAnalytics(
        snapshot_ts=AS_OF,
        provider="IBKR",
        underlying=MEMBER_AAA,
        tenor_label="3m",
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=log_moneyness,
        strike=AN_FORWARD * (1.0 + log_moneyness),
        forward_price=AN_FORWARD,
        implied_vol=implied_vol,
        total_variance=implied_vol * implied_vol * 0.25,
        price=4.2,
        delta=delta,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=dollar_delta,
        dollar_gamma=7.6,
        dollar_vega=0.31,
        dollar_delta_unit=AN_DOLLAR_DELTA_UNIT,
        dollar_gamma_unit=AN_DOLLAR_GAMMA_UNIT,
        dollar_vega_unit=AN_DOLLAR_VEGA_UNIT,
        model_version="svi-readback",
        pricer_version="px-readback",
        source_snapshot_ts=AS_OF,
        provenance=_prov(f"analytics:{delta_band}"),
        dollar_theta=-0.000041,
        dollar_rho=0.0005,
        dollar_theta_unit=AN_DOLLAR_THETA_UNIT,
        dollar_rho_unit=AN_DOLLAR_RHO_UNIT,
    )


def _seed_store(root: Path) -> None:
    store = ParquetStore(root)
    # Daily OHLC bars for the two index members (price-history + price-first ordering oracle).
    store.write("daily_bar", [_daily_bar(MEMBER_AAA, row) for row in AAA_BARS])
    store.write("daily_bar", [_daily_bar(MEMBER_BBB, row) for row in BBB_BARS])
    # Bitemporal membership: AAA in the basket on TRADE_DATE; CCC was removed before it and a
    # FUT member is added after it — both must be absent from the as-of basket (look-ahead gate).
    store.write(
        "index_constituents",
        [
            _constituent(MEMBER_AAA, 0.6, date(2026, 1, 1), None, date(2026, 1, 1)),
            _constituent(MEMBER_BBB, 0.4, date(2026, 1, 1), None, date(2026, 1, 1)),
            _constituent("CCC", 0.0, date(2025, 1, 1), date(2026, 4, 1), date(2026, 1, 1)),
            _constituent("FUT", 0.0, date(2026, 6, 1), None, date(2026, 1, 1)),
        ],
    )
    # Projected-analytics cells for AAA, one maturity, a 30Δ put + a 30Δ call.
    store.write(
        "projected_option_analytics",
        [
            _analytics_cell(
                delta_band="30dp",
                target_delta=AN_PUT_DELTA,
                log_moneyness=AN_PUT_LOGM,
                implied_vol=AN_PUT_IV,
                delta=AN_PUT_DELTA,
                dollar_delta=AN_PUT_DOLLAR_DELTA,
            ),
            _analytics_cell(
                delta_band="30dc",
                target_delta=AN_CALL_DELTA,
                log_moneyness=AN_CALL_LOGM,
                implied_vol=AN_CALL_IV,
                delta=AN_CALL_DELTA,
                dollar_delta=AN_CALL_DOLLAR_DELTA,
            ),
        ],
    )
    # A fitted SVI slice for AAA on TRADE_DATE so the analytics surface_slice is populated.
    store.write(
        "surface_parameters",
        [
            tables.SurfaceParameters(
                snapshot_ts=AS_OF,
                underlying=MEMBER_AAA,
                maturity_years=0.25,
                model_version="svi-readback",
                svi_a=SVI_A,
                svi_b=SVI_B,
                svi_rho=SVI_RHO,
                svi_m=SVI_M,
                svi_sigma=SVI_SIGMA,
                expiry_date=EXPIRY,
                day_count="ACT/365",
                diagnostics=SurfaceFitDiagnostics(rmse=0.0008, n_points=9, arb_free=True),
                source_snapshot_ts=AS_OF,
                provenance=_prov("surface:AAA"),
            )
        ],
    )
    _seed_legacy_store(store)


def _seed_legacy_store(store: ParquetStore) -> None:
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
        "pricing_results",
        [
            tables.PricingResult(
                snapshot_ts=AS_OF,
                contract_key=CALL_100.canonical(),
                pricer_version="px-readback",
                price=3.99,
                delta=0.55,
                gamma=0.02,
                vega=0.10,
                theta=-0.01,
                rho=0.03,
                dollar_delta=PR_DOLLAR_DELTA,
                dollar_gamma=PR_DOLLAR_GAMMA,
                dollar_vega=PR_DOLLAR_VEGA,
                dollar_theta=PR_DOLLAR_THETA,
                dollar_rho=PR_DOLLAR_RHO,
                source_snapshot_ts=AS_OF,
                provenance=_prov("px:AAPL"),
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
                scenario_pnl=SCENARIO_PNL,
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
    # The seed holds a fitted surface for AAPL (the option-pipeline fixture) and for AAA (the
    # 1I analytics fixture); both are listed, sorted.
    assert payload["underlyings"] == [MEMBER_AAA, UNDERLYING]


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
    assert cell["scenario_pnl"] == pytest.approx(SCENARIO_PNL)


def test_risk_portfolio_filter_selects_the_seeded_portfolio(seeded_client: TestClient) -> None:
    hit = seeded_client.get("/api/risk", params={"portfolio_id": PORTFOLIO_ID}).json()
    assert hit["n_aggregates"] == 1
    miss = seeded_client.get("/api/risk", params={"portfolio_id": "nope"}).json()
    assert miss["n_aggregates"] == 0


def test_metrics_carry_a_unit_string_and_the_raw_value_beside_each_dollar(
    seeded_client: TestClient,
) -> None:
    # The BFF metric contract (P0.2 / OQ-1, ADR 0036): every dollar metric the front reads
    # back carries a non-empty unit string of the pinned convention and the raw per-unit
    # Greek beside it — never a bare float. This is the BFF<->infra drift guard.
    payload = seeded_client.get("/api/risk/metrics", params={"underlying": UNDERLYING}).json()
    assert payload["n_results"] == 1
    metrics = payload["results"][0]["metrics"]
    # Gamma quoted per 1% move; theta per calendar day (the pinned defaults).
    assert metrics["gamma"]["unit"] == "$ per 1% move"
    assert metrics["theta"]["unit"] == "$ per calendar day"
    # Every dollar metric has a non-empty unit string and the raw per-unit value beside it.
    for name, raw, dollar in [
        ("delta", 0.55, PR_DOLLAR_DELTA),
        ("gamma", 0.02, PR_DOLLAR_GAMMA),
        ("vega", 0.10, PR_DOLLAR_VEGA),
        ("theta", -0.01, PR_DOLLAR_THETA),
        ("rho", 0.03, PR_DOLLAR_RHO),
    ]:
        metric = metrics[name]
        assert metric["unit"], f"{name} must carry a non-empty unit string"
        assert metric["raw"] == pytest.approx(raw)
        assert metric["dollar"] == pytest.approx(dollar)


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


# --------------------------------------------------------------------------------------
# WS 1I — front-page BFF seams: price-history, constituents, projected analytics, recorded-dates.
# Each test seeds real contract rows (above) and asserts the new router reads *those* values
# back unchanged with their provenance — the BFF<->infra seam, not numbers copied from output.
# --------------------------------------------------------------------------------------


def test_price_history_reads_back_daily_bars(seeded_client: TestClient) -> None:
    payload = seeded_client.get(
        "/api/price-history", params={"underlying": MEMBER_AAA}
    ).json()
    assert payload["underlying"] == MEMBER_AAA
    assert payload["n_bars"] == len(AAA_BARS)
    # Bars come back sorted by trade_date; the 2026-05-29 bar echoes the seeded OHLCV exactly.
    last = payload["bars"][-1]
    assert last["trade_date"] == "2026-05-29"
    assert last["open"] == pytest.approx(AAA_29_OPEN)
    assert last["high"] == pytest.approx(AAA_29_HIGH)
    assert last["low"] == pytest.approx(AAA_29_LOW)
    assert last["close"] == pytest.approx(AAA_29_CLOSE)
    assert last["volume"] == pytest.approx(AAA_29_VOLUME)
    # Provenance carried through to the UI.
    assert last["provenance"]["code_version"] == "readback-test"


def test_price_history_uses_dailybar_ohlc_field_names(seeded_client: TestClient) -> None:
    # Field-name conformance: the payload exposes the DailyBar OHLC contract fields verbatim.
    # A renamed contract field turns this red.
    bar = seeded_client.get(
        "/api/price-history", params={"underlying": MEMBER_AAA}
    ).json()["bars"][0]
    for field in ("trade_date", "open", "high", "low", "close", "volume"):
        assert field in bar, f"DailyBar field {field!r} must be in the payload"


def test_price_history_window_filters_inclusive(seeded_client: TestClient) -> None:
    payload = seeded_client.get(
        "/api/price-history",
        params={"underlying": MEMBER_AAA, "start": "2026-05-29", "end": "2026-05-29"},
    ).json()
    assert payload["n_bars"] == 1
    assert payload["bars"][0]["trade_date"] == "2026-05-29"


def test_price_history_unknown_ticker_is_empty_not_500(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/price-history", params={"underlying": "NOPE"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["underlying"] == "NOPE"
    assert payload["n_bars"] == 0
    assert payload["bars"] == []


def test_price_history_bad_date_is_labeled_400(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/price-history", params={"underlying": MEMBER_AAA, "start": "not-a-date"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_date"


def test_constituents_reads_back_as_of_basket(seeded_client: TestClient) -> None:
    payload = seeded_client.get(
        "/api/constituents", params={"index": INDEX, "as_of": TRADE_DATE.isoformat()}
    ).json()
    assert payload["index"] == INDEX
    assert payload["as_of"] == TRADE_DATE.isoformat()
    symbols = [c["symbol"] for c in payload["constituents"]]
    assert symbols == [MEMBER_AAA, MEMBER_BBB]  # AAA's close (192) > BBB's (45.5): price-first
    aaa = payload["constituents"][0]
    assert aaa["weight"] == pytest.approx(0.6)
    assert aaa["effective_add_date"] == "2026-01-01"
    assert aaa["effective_remove_date"] is None
    assert aaa["latest_close"] == pytest.approx(AAA_29_CLOSE)


def test_constituents_price_first_orders_by_latest_close(seeded_client: TestClient) -> None:
    closes = [
        c["latest_close"]
        for c in seeded_client.get(
            "/api/constituents", params={"index": INDEX, "as_of": TRADE_DATE.isoformat()}
        ).json()["constituents"]
    ]
    # Price-first: latest close descending (names with a bar before any without).
    assert closes == sorted(closes, key=lambda c: -c)


def test_constituents_as_of_excludes_future_members(seeded_client: TestClient) -> None:
    # No look-ahead: a member added after as_of (FUT, add 2026-06-01) is absent, and one removed
    # before it (CCC, removed 2026-04-01) is absent. The basket is the names in force *then*.
    symbols = {
        c["symbol"]
        for c in seeded_client.get(
            "/api/constituents", params={"index": INDEX, "as_of": TRADE_DATE.isoformat()}
        ).json()["constituents"]
    }
    assert symbols == {MEMBER_AAA, MEMBER_BBB}
    assert "FUT" not in symbols
    assert "CCC" not in symbols


def test_constituents_bad_as_of_is_labeled_400(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/constituents", params={"index": INDEX, "as_of": "nope"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_as_of"


def test_analytics_reads_back_surface_and_dollar_greeks(seeded_client: TestClient) -> None:
    payload = seeded_client.get(
        "/api/analytics",
        params={"underlying": MEMBER_AAA, "trade_date": TRADE_DATE.isoformat()},
    ).json()
    assert payload["underlying"] == MEMBER_AAA
    assert payload["n_maturities"] == 1
    maturity = payload["maturities"][0]
    assert maturity["maturity_years"] == pytest.approx(0.25)
    # Smile ordered by delta: the 30Δ put (-0.30) first, the 30Δ call (+0.30) last.
    assert maturity["smile"]["deltas"] == [pytest.approx(AN_PUT_DELTA), pytest.approx(AN_CALL_DELTA)]
    assert maturity["smile"]["implied_vols"] == [
        pytest.approx(AN_PUT_IV),
        pytest.approx(AN_CALL_IV),
    ]
    # The fitted SVI slice for the 3D surface is attached.
    assert maturity["surface_slice"]["svi_b"] == pytest.approx(SVI_B)
    # Dollar Greeks read back on the band points.
    put_point = maturity["points"][0]
    assert put_point["forward_price"] == pytest.approx(AN_FORWARD)
    assert put_point["metrics"]["delta"]["dollar"] == pytest.approx(AN_PUT_DOLLAR_DELTA)


def test_analytics_payload_uses_blueprint_field_names(seeded_client: TestClient) -> None:
    # Field-name conformance (ADR 0029): the analytics payload uses forward_price / implied_vol /
    # log_moneyness / dollar_*. A renamed contract field turns this red.
    point = seeded_client.get(
        "/api/analytics",
        params={"underlying": MEMBER_AAA, "trade_date": TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]
    for field in ("forward_price", "implied_vol", "log_moneyness"):
        assert field in point, f"blueprint field {field!r} must be in the analytics payload"
    # The dollar_* layer is exposed as named metrics carrying the raw per-unit Greek.
    assert set(point["metrics"]) == {"delta", "gamma", "vega", "theta", "rho"}
    assert "raw" in point["metrics"]["delta"] and "dollar" in point["metrics"]["delta"]


def test_dollar_greeks_carry_unit_strings(seeded_client: TestClient) -> None:
    # P0.2 / ADR 0036: every dollar number carries a non-empty unit string with pinned semantics.
    metrics = seeded_client.get(
        "/api/analytics",
        params={"underlying": MEMBER_AAA, "trade_date": TRADE_DATE.isoformat()},
    ).json()["maturities"][0]["points"][0]["metrics"]
    assert metrics["delta"]["unit"] == AN_DOLLAR_DELTA_UNIT
    assert metrics["gamma"]["unit"] == AN_DOLLAR_GAMMA_UNIT
    assert metrics["vega"]["unit"] == AN_DOLLAR_VEGA_UNIT
    assert metrics["theta"]["unit"] == AN_DOLLAR_THETA_UNIT
    assert metrics["rho"]["unit"] == AN_DOLLAR_RHO_UNIT
    for name in ("delta", "gamma", "vega", "theta", "rho"):
        assert metrics[name]["unit"], f"{name} must carry a non-empty unit string"


def test_analytics_unknown_ticker_is_empty_not_500(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/analytics", params={"underlying": "NOPE"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_maturities"] == 0
    assert payload["maturities"] == []


def test_analytics_bad_trade_date_is_labeled_400(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/analytics", params={"underlying": MEMBER_AAA, "trade_date": "nope"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_trade_date"


# -- recorded-dates: sourced from the 1G run ledger, only complete gap-free runs ----------

COMPLETE_DATE_1 = date(2026, 5, 28)
COMPLETE_DATE_2 = date(2026, 5, 29)
PARTIAL_DATE = date(2026, 5, 30)


def _seed_ledger(root: Path) -> None:
    """Two gap-free completed EOD runs + one partial/failed run in the run-state ledger."""
    for trade_date in (COMPLETE_DATE_1, COMPLETE_DATE_2):
        for stage in EOD_STAGES:
            record_stage(
                root,
                StageRun(
                    trade_date=trade_date,
                    stage=stage,
                    outcome=OUTCOME_OK,
                    run_id=f"run-{trade_date.isoformat()}",
                    recorded_ts=AS_OF,
                ),
            )
    # A partial/failed day: only the first two stages, the last recorded failed. Not complete.
    record_stage(
        root,
        StageRun(
            trade_date=PARTIAL_DATE,
            stage=EOD_STAGES[0],
            outcome=OUTCOME_OK,
            run_id="run-partial",
            recorded_ts=AS_OF,
        ),
    )
    record_stage(
        root,
        StageRun(
            trade_date=PARTIAL_DATE,
            stage=EOD_STAGES[1],
            outcome=OUTCOME_FAILED,
            run_id="run-partial",
            recorded_ts=AS_OF,
        ),
    )


@pytest.fixture
def ledger_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over a store whose run-state ledger has 2 complete + 1 partial run."""
    store_root = tmp_path / "data"
    _seed_store(store_root)
    _seed_ledger(store_root)
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying=UNDERLYING,
    )
    runner.JOB_STORE.clear()
    with TestClient(create_app(ctx)) as client:
        yield client


def test_recorded_dates_excludes_incomplete_runs(ledger_client: TestClient) -> None:
    payload = ledger_client.get("/api/recorded-dates", params={"index": INDEX}).json()
    assert payload["count"] == 2
    # Only the two gap-free completed days, newest first; the partial day is excluded.
    assert payload["dates"] == [COMPLETE_DATE_2.isoformat(), COMPLETE_DATE_1.isoformat()]
    assert PARTIAL_DATE.isoformat() not in payload["dates"]


def test_recorded_dates_empty_ledger_is_count_zero(seeded_client: TestClient) -> None:
    # seeded_client's store has no run ledger: a labeled empty state with count 0, never a 500.
    response = seeded_client.get("/api/recorded-dates", params={"index": INDEX})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["dates"] == []


def test_recorded_date_pick_reresolves_membership_as_of(ledger_client: TestClient) -> None:
    # Picking a returned past date drives the as-of re-resolution: the constituent list resolved
    # at that date returns the basket in force then (the front wires the dropdown to as_of).
    recorded = ledger_client.get("/api/recorded-dates", params={"index": INDEX}).json()
    picked = recorded["dates"][0]  # 2026-05-29, a complete day
    basket = ledger_client.get(
        "/api/constituents", params={"index": INDEX, "as_of": picked}
    ).json()
    assert basket["as_of"] == picked
    assert {c["symbol"] for c in basket["constituents"]} == {MEMBER_AAA, MEMBER_BBB}
