"""Attribution BFF seam: persist a real ``ScenarioAttribution`` row, read it back as the
front's waterfall payload.

The seam that matters (mirrors ``test_readback_api.py`` discipline): we *persist* a real
``scenario_attributions`` contract row through ``ParquetStore.write`` — the exact table the
attribution engine emits — and assert the router projects *those* values into the per-term
dollar payload + residual + verdict, unchanged. The independent oracle is "what we wrote in":
the per-Greek dollar contributions and residual are hand-chosen numbers placed on the row, and
the assertions check the router surfaces them verbatim. The BFF re-decomposes nothing — a
``ValueError`` would fire here if the serializer summed or repriced instead of passing through.

A renamed contract field turns the field-name conformance test red (the BFF<->infra drift guard).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import tables
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 5, 29)
PORTFOLIO_ID = "pf-attribution"
SCENARIO_ID = "spot-down-10"
BOOK_CONTRACT_KEY = "__book__"
POSITION_CONTRACT_KEY = "AAPL|OPT|C|100.0"

# Hand-chosen dollar contributions written into the book record. The router must echo these exact
# numbers back per term, in the ADR-0030 order, with the residual as its own bar. The independent
# oracle is the inputs we wrote, not numbers copied from BFF output. The residual is the engine's
# carried full_reprice - approx_pnl; we set the row's fields so approx_pnl + residual ==
# full_reprice_pnl holds by construction (the BFF does not recompute it).
BOOK_DELTA = 42_000.0
BOOK_GAMMA = -3_500.0
BOOK_VEGA = 12_500.0
BOOK_THETA = -1_200.0
BOOK_APPROX = BOOK_DELTA + BOOK_GAMMA + BOOK_VEGA + BOOK_THETA  # 49_800.0
BOOK_FULL_REPRICE = 50_250.0
BOOK_RESIDUAL = BOOK_FULL_REPRICE - BOOK_APPROX  # 450.0 — the honesty meter
RESIDUAL_ABS_TOL = 100.0
RESIDUAL_REL_TOL = 0.001
# |450| > max(100, 0.001*50250=50.25) → the engine ruled this breached; we persist that verdict.
BOOK_WITHIN_TOLERANCE = False

# A per-position record under its own contract_key, for the §5.8 drill target.
POS_DELTA = 21_000.0
POS_GAMMA = -1_750.0
POS_VEGA = 6_250.0
POS_THETA = -600.0
POS_APPROX = POS_DELTA + POS_GAMMA + POS_VEGA + POS_THETA
POS_FULL_REPRICE = POS_APPROX + 10.0
POS_RESIDUAL = POS_FULL_REPRICE - POS_APPROX  # 10.0, within tolerance


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="attribution-readback-test",
        config_hashes={"cfg": "cfg-attribution"},
        source_records=(source_ref("scenario_attributions", "sess-attr", source),),
        source_timestamps=(AS_OF,),
    )


def _attribution(
    *,
    level: str,
    contract_key: str,
    delta_pnl: float,
    gamma_pnl: float,
    vega_pnl: float,
    theta_pnl: float,
    approx_pnl: float,
    full_reprice_pnl: float,
    residual: float,
    within_tolerance: bool,
) -> tables.ScenarioAttribution:
    return tables.ScenarioAttribution(
        valuation_ts=AS_OF,
        portfolio_id=PORTFOLIO_ID,
        scenario_id=SCENARIO_ID,
        contract_key=contract_key,
        level=level,
        spot_shock=-0.10,
        vol_shock=0.0,
        time_shock=0.0,
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        vega_pnl=vega_pnl,
        theta_pnl=theta_pnl,
        approx_pnl=approx_pnl,
        full_reprice_pnl=full_reprice_pnl,
        residual=residual,
        within_tolerance=within_tolerance,
        residual_abs_tol=RESIDUAL_ABS_TOL,
        residual_rel_tol=RESIDUAL_REL_TOL,
        scenario_version="scn-1",
        attribution_version="attr-1",
        source_snapshot_ts=AS_OF,
        provenance=_prov(f"{level}:{contract_key}"),
    )


def _seed_store(root: Path) -> None:
    store = ParquetStore(root)
    store.write(
        "scenario_attributions",
        [
            _attribution(
                level="book",
                contract_key=BOOK_CONTRACT_KEY,
                delta_pnl=BOOK_DELTA,
                gamma_pnl=BOOK_GAMMA,
                vega_pnl=BOOK_VEGA,
                theta_pnl=BOOK_THETA,
                approx_pnl=BOOK_APPROX,
                full_reprice_pnl=BOOK_FULL_REPRICE,
                residual=BOOK_RESIDUAL,
                within_tolerance=BOOK_WITHIN_TOLERANCE,
            ),
            _attribution(
                level="position",
                contract_key=POSITION_CONTRACT_KEY,
                delta_pnl=POS_DELTA,
                gamma_pnl=POS_GAMMA,
                vega_pnl=POS_VEGA,
                theta_pnl=POS_THETA,
                approx_pnl=POS_APPROX,
                full_reprice_pnl=POS_FULL_REPRICE,
                residual=POS_RESIDUAL,
                within_tolerance=True,
            ),
        ],
    )


@pytest.fixture
def seeded_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over the BFF wired to a store pre-seeded with real attribution rows."""
    store_root = tmp_path / "data"
    _seed_store(store_root)
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying="AAPL",
    )
    with TestClient(create_app(ctx)) as client:
        yield client


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over a store with no attribution rows for the (book, date)."""
    store_root = tmp_path / "data"
    ctx = AppContext(
        store_root=store_root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(store_root),
        default_underlying="AAPL",
    )
    with TestClient(create_app(ctx)) as client:
        yield client


def test_attribution_reads_back_book_terms_residual_verdict(seeded_client: TestClient) -> None:
    payload = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": PORTFOLIO_ID},
    ).json()
    assert payload["found"] is True
    assert payload["level"] == "book"
    assert payload["portfolio_id"] == PORTFOLIO_ID
    # Per-term dollar contributions, in the ADR-0030 dPnL order, each echoed verbatim. The oracle
    # is the inputs we wrote into the row — a BFF that re-summed would not match these signed dollars.
    terms = {term["name"]: term for term in payload["terms"]}
    assert [term["name"] for term in payload["terms"]] == ["Delta", "Gamma", "Vega", "Theta"]
    assert terms["Delta"]["dollars"] == pytest.approx(BOOK_DELTA)
    assert terms["Gamma"]["dollars"] == pytest.approx(BOOK_GAMMA)
    assert terms["Vega"]["dollars"] == pytest.approx(BOOK_VEGA)
    assert terms["Theta"]["dollars"] == pytest.approx(BOOK_THETA)
    # The residual is its own bar (the honesty meter), echoed verbatim — never folded into a term.
    assert payload["residual"]["dollars"] == pytest.approx(BOOK_RESIDUAL)
    # The verdict is the engine's tolerance ruling against its echoed bounds.
    assert payload["verdict"]["within_tolerance"] is False
    assert payload["verdict"]["residual_abs_tol"] == pytest.approx(RESIDUAL_ABS_TOL)
    assert payload["verdict"]["residual_rel_tol"] == pytest.approx(RESIDUAL_REL_TOL)


def test_attribution_payload_equals_engine_output_no_redecompose(seeded_client: TestClient) -> None:
    # The BFF re-decomposes nothing: the served dollars equal the engine's terms for the same
    # input, and the engine identity approx_pnl + residual == full_reprice_pnl is *carried*, not
    # recomputed (the BFF does not re-sum the bars). The oracle is the row we wrote.
    payload = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": PORTFOLIO_ID},
    ).json()
    served_sum = sum(term["dollars"] for term in payload["terms"])
    assert served_sum == pytest.approx(BOOK_APPROX)
    assert payload["approx_pnl"] == pytest.approx(BOOK_APPROX)
    assert payload["full_reprice_pnl"] == pytest.approx(BOOK_FULL_REPRICE)
    # The engine's residual identity holds on the served payload, proving the residual was carried
    # (full_reprice - approx) rather than dropped or refolded.
    assert payload["approx_pnl"] + payload["residual"]["dollars"] == pytest.approx(
        payload["full_reprice_pnl"]
    )


def test_attribution_terms_carry_dollar_unit_strings(seeded_client: TestClient) -> None:
    # §5.1/§2.5: every bar carries its dollar unit string; the residual carries its own.
    payload = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": PORTFOLIO_ID},
    ).json()
    for term in payload["terms"]:
        assert term["unit"], f"{term['name']} must carry a non-empty unit string"
        assert "$" in term["unit"]
    assert payload["residual"]["unit"]
    assert "$" in payload["residual"]["unit"]


def test_attribution_uses_contract_field_names(seeded_client: TestClient) -> None:
    # Field-name conformance (mirror test_readback_api.py): a renamed ScenarioAttribution dollar
    # field turns this red, because the serializer reads it by name to build the terms.
    payload = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": PORTFOLIO_ID},
    ).json()
    for key in ("terms", "residual", "verdict", "approx_pnl", "full_reprice_pnl", "level"):
        assert key in payload, f"attribution payload must carry {key!r}"
    assert {"name", "dollars", "unit"} == set(payload["terms"][0])
    assert {"dollars", "unit"} == set(payload["residual"])
    # Provenance carried through to the UI, like every other readback seam.
    assert payload["provenance"]["code_version"] == "attribution-readback-test"


def test_attribution_position_drill_selects_that_contract(seeded_client: TestClient) -> None:
    # The §5.8 drill target: level=position + the leg's contract_key returns *that* line's record.
    payload = seeded_client.get(
        "/api/attribution",
        params={
            "trade_date": TRADE_DATE.isoformat(),
            "portfolio_id": PORTFOLIO_ID,
            "level": "position",
            "contract_key": POSITION_CONTRACT_KEY,
        },
    ).json()
    assert payload["found"] is True
    assert payload["level"] == "position"
    assert payload["contract_key"] == POSITION_CONTRACT_KEY
    terms = {term["name"]: term["dollars"] for term in payload["terms"]}
    assert terms["Delta"] == pytest.approx(POS_DELTA)
    assert payload["residual"]["dollars"] == pytest.approx(POS_RESIDUAL)
    assert payload["verdict"]["within_tolerance"] is True


def test_attribution_default_level_is_the_book_not_a_position(seeded_client: TestClient) -> None:
    # With no level given the default is the book aggregate (the book sentinel), never a leg.
    payload = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": PORTFOLIO_ID},
    ).json()
    assert payload["level"] == "book"
    assert payload["contract_key"] == BOOK_CONTRACT_KEY


def test_attribution_empty_is_labelled_200_not_500(empty_client: TestClient) -> None:
    response = empty_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": "nope"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is False
    assert payload["terms"] == []
    # Still labelled: the residual unit travels so the panel renders an honest empty waterfall.
    assert payload["residual"]["dollars"] is None
    assert payload["residual"]["unit"]


def test_attribution_unknown_portfolio_is_empty_not_500(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/attribution",
        params={"trade_date": TRADE_DATE.isoformat(), "portfolio_id": "does-not-exist"},
    )
    assert response.status_code == 200
    assert response.json()["found"] is False


def test_attribution_bad_trade_date_is_labelled_400(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/attribution", params={"trade_date": "not-a-date", "portfolio_id": PORTFOLIO_ID}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_trade_date"
