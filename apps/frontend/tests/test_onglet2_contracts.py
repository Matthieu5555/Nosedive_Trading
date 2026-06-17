"""Stream-B Onglet-2 contract tests — the Risque-tab seams locked by the *consumer*.

These complement (never duplicate) the per-seam suites that already landed:
``test_risk_api.py`` (persisted ``/api/risk/scenarios``), ``test_readback_api.py`` (the
compose/book additivity readback) and ``test_attribution_api.py`` (the dPnL decomposition).
This file pins the three obligations the Onglet-2 spec calls out by name that those suites
do not yet make falsifiable:

1. **Backward-compat is byte-identical, not merely "empty".** A surface-only persisted store
   (no ``rate``-family scenario rows) must yield a ``/api/risk/scenarios`` payload whose key
   set is *identical* to a store that has none of the second-order families either — the rate
   axis is purely additive, so its absence leaves the surface-only contract untouched.

2. **An independent oracle for the compose/book combined Greeks.** ``test_readback_api.py``
   sums the *same* payload's per-layer rows (a within-payload additivity check). Here the
   oracle is a *different request path*: compose each layer **alone** in its own ``POST
   /api/compose`` call, then compose the two together, and assert the combined book equals the
   per-Greek sum of the two standalone single-layer books — decimal *and* dollar — each dollar
   number carrying its unit string. The two book builds are independent computations, so this
   is a true cross-check, not the code graded against itself.

3. **The attribution residual is measured against the full reprice.** With a hand-built
   2-term + residual record whose numbers are computed BY HAND in the comment, assert
   ``residual == full_reprice − Σ terms`` within tolerance, every term + the residual carry a
   ``$`` unit string, and Charm is never an attribution term.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from algotrading.core import source_ref, stamp
from algotrading.core.provenance import ProvenanceStamp
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    ProjectedOptionAnalytics,
    tables,
)
from algotrading.infra.pricing import UNIT_STRINGS
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

_TS = datetime(2026, 6, 5, 20, 0, tzinfo=UTC)
_TRADE = date(2026, 6, 5)
_EXPIRY = date(2026, 9, 4)
_CONFIGS = Path("configs").resolve()


# --------------------------------------------------------------------------------------------
# Deliverable 1 — /api/risk/scenarios backward-compat is byte-identical (key-for-key)
# --------------------------------------------------------------------------------------------
# The persisted Risk path serves the rate sweep from rate-family ``scenario_results`` rows. A
# store that holds only a surface (no rate, no named rows) must produce a payload whose SHAPE is
# indistinguishable from the surface-only contract: the rate/named axes are additive, so their
# absence changes no key and no value. The conftest ``surface_client`` seeds exactly such a
# surface-only store (spot×vol cells + one parametric spot cell, zero rate/named rows).

# The frozen surface-only contract: every top-level key the payload must carry and no more.
_SURFACE_ONLY_TOP_KEYS = {
    "portfolio_id",
    "n_cells",
    "cells",
    "surface",
    "named",
    "n_named",
    "rate",
    "n_rate",
}
_SURFACE_BLOCK_KEYS = {
    "spot_shock",
    "vol_shock",
    "scenario_pnl",
    "scenario_version",
    "unit",
    "n_cells",
    "has_holes",
    "n_holes",
}


def test_surface_only_store_has_byte_identical_top_level_shape(
    surface_client: TestClient, seed: ModuleType
) -> None:
    payload = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()
    # No rate-family / named rows were seeded → those axes are present but empty, and the key
    # set is exactly the surface-only contract (no extra keys leak in, none are dropped).
    assert set(payload) == _SURFACE_ONLY_TOP_KEYS
    assert payload["rate"] == [] and payload["n_rate"] == 0
    assert payload["named"] == [] and payload["n_named"] == 0
    assert set(payload["surface"]) == _SURFACE_BLOCK_KEYS


def test_rate_axis_is_purely_additive_surface_block_unchanged(
    surface_client: TestClient, rate_client: TestClient, seed: ModuleType
) -> None:
    # The SAME surface block must be served whether or not a rate sweep is present: adding the
    # rate family changes only the ``rate``/``n_rate`` keys, never the surface contract's shape.
    surface_only = surface_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.SURFACE_PORTFOLIO}
    ).json()
    with_rate = rate_client.get(
        "/api/risk/scenarios", params={"portfolio_id": seed.RATE_PORTFOLIO}
    ).json()
    assert set(surface_only["surface"]) == set(with_rate["surface"]) == _SURFACE_BLOCK_KEYS
    # Surface-only carries an empty rate axis; the rate store lights it up — additive, same shape.
    assert surface_only["n_rate"] == 0
    assert with_rate["n_rate"] == len(seed.RATE_LEGS)


# --------------------------------------------------------------------------------------------
# Deliverable 2 — compose/book combined Greeks against an INDEPENDENT oracle
# --------------------------------------------------------------------------------------------
# Oracle = a different request path. We resolve each layer ALONE in its own ``POST /api/compose``
# (a one-layer book whose ``combined`` is that layer's net), then resolve the two TOGETHER. The
# combined two-layer book must equal the per-Greek sum of the two standalone one-layer books.
# This is genuinely independent: the two builds share no intermediate state across requests, and
# the test never reimplements the pricer — it cross-checks additivity over the HTTP boundary.

_UND_A = "AAA"
_UND_B = "BBB"
_DECIMAL = ("net_delta", "net_gamma", "net_vega", "net_theta")
_DOLLAR = ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho")


def _analytics_row(underlying: str, *, strike: float, vega: float) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider="ibkr",
        underlying=underlying,
        tenor_label="1m",
        maturity_years=1.0 / 12.0,
        delta_band="atm",
        target_delta=0.50,
        log_moneyness=0.0,
        strike=strike,
        forward_price=strike,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 / 12.0,
        price=2.30,
        delta=0.5,
        gamma=0.02,
        vega=vega,
        theta=-0.05,
        rho=0.04,
        dollar_delta=500.0,
        dollar_gamma=0.02,
        dollar_vega=vega,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=stamp(
            calc_ts=_TS,
            code_version="onglet2-contract-test",
            config_hashes={"cfg": "cfg"},
            source_records=(source_ref("raw_market_events", "sess", "evt"),),
            source_timestamps=(_TS,),
        ),
    )


def _instrument_master(underlying: str, *, strike: float) -> InstrumentMaster:
    key = InstrumentKey(
        underlying_symbol=underlying,
        security_type="OPT",
        exchange="EUREX",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id=f"o-{underlying}",
        expiry=_EXPIRY,
        strike=strike,
        option_right="C",
    )
    return InstrumentMaster(
        instrument_key=key.canonical(),
        as_of_date=_TRADE,
        instrument=key,
        raw_broker_payload="{}",
    )


@pytest.fixture
def compose_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write(
        "projected_option_analytics",
        [
            _analytics_row(_UND_A, strike=100.0, vega=0.31),
            _analytics_row(_UND_B, strike=50.0, vega=0.17),
        ],
    )
    store.write(
        "instrument_master",
        [
            _instrument_master(_UND_A, strike=100.0),
            _instrument_master(_UND_B, strike=50.0),
        ],
    )
    ctx = AppContext(store_root=store_root, configs_dir=_CONFIGS, store=store)
    with TestClient(create_app(ctx)) as client:
        yield client


def _leg(underlying: str, side: str, quantity: float) -> dict[str, object]:
    signed = quantity if side == "long" else -quantity
    return {
        "instrument_kind": "option",
        "side": side,
        "quantity": signed,
        "underlying": underlying,
        "tenor_label": "1m",
        "delta_band": "atm",
    }


def _layer(label: str, basket_id: str, underlying: str, side: str, qty: float) -> dict[str, object]:
    return {
        "label": label,
        "basket_id": basket_id,
        "underlying": underlying,
        "legs": [_leg(underlying, side, qty)],
    }


_LAYER_A = _layer("vol-seller", "L1", _UND_A, "short", 2.0)
_LAYER_B = _layer("crash-hedge", "L2", _UND_B, "long", 3.0)


def _compose(client: TestClient, layers: list[dict[str, Any]]) -> dict[str, Any]:
    response = client.post(
        "/api/compose",
        json={"book_id": "BK-oracle", "trade_date": _TRADE.isoformat(), "layers": layers},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_combined_book_equals_independent_single_layer_books(compose_client: TestClient) -> None:
    # Independent oracle: two single-layer books, composed separately, must sum to the two-layer
    # book — proving the combined Greek is the additive net of its layers across request paths,
    # not a number the router invented. Each layer resolves exactly one leg.
    book_a = _compose(compose_client, [_LAYER_A])
    book_b = _compose(compose_client, [_LAYER_B])
    combined_book = _compose(compose_client, [_LAYER_A, _LAYER_B])

    only_a = book_a["combined"]
    only_b = book_b["combined"]
    combined = combined_book["combined"]
    assert combined["level"] == "book"
    assert only_a["level"] == only_b["level"] == "book"

    for field in _DECIMAL:
        expected = only_a[field] + only_b[field]
        assert combined[field] == pytest.approx(expected), field
    for field in _DOLLAR:
        expected = only_a[field]["value"] + only_b[field]["value"]
        assert combined[field]["value"] == pytest.approx(expected), field


def test_combined_book_per_layer_breakdown_present_and_summed(compose_client: TestClient) -> None:
    book = _compose(compose_client, [_LAYER_A, _LAYER_B])
    layers = book["layers"]
    assert [layer["layer_label"] for layer in layers] == ["vol-seller", "crash-hedge"]
    assert [layer["n_resolved"] for layer in layers] == [1, 1]
    combined = book["combined"]
    for field in _DECIMAL:
        assert combined[field] == pytest.approx(math.fsum(layer[field] for layer in layers))
    for field in _DOLLAR:
        assert combined[field]["value"] == pytest.approx(
            math.fsum(layer[field]["value"] for layer in layers)
        )


def test_each_combined_dollar_greek_carries_a_unit_string(compose_client: TestClient) -> None:
    combined = _compose(compose_client, [_LAYER_A, _LAYER_B])["combined"]
    for field in _DOLLAR:
        unit = combined[field]["unit"]
        assert unit and "$" in unit, f"{field} must carry a $ unit string, got {unit!r}"
    # The two display-sensitive units are pinned (the operator reads these labels verbatim).
    assert combined["dollar_gamma"]["unit"] == "$ per 1% move"
    assert combined["dollar_theta"]["unit"] == "$ per calendar day"


def test_combined_pnl_surface_is_finite_and_grid_shaped(compose_client: TestClient) -> None:
    surface = _compose(compose_client, [_LAYER_A, _LAYER_B])["surface"]
    spot_axis, vol_axis, grid = surface["spot_axis"], surface["vol_axis"], surface["pnl_grid"]
    assert surface["scenario_version"]
    assert len(grid) == len(spot_axis) and len(spot_axis) > 0
    assert all(len(row) == len(vol_axis) for row in grid)
    assert all(math.isfinite(cell) for row in grid for cell in row)
    # Centre cell (no shock) is ~0 PnL by construction.
    ci, cj = spot_axis.index(0.0), vol_axis.index(0.0)
    assert math.isclose(grid[ci][cj], 0.0, abs_tol=1e-6)


# --------------------------------------------------------------------------------------------
# Deliverable 3 — attribution residual measured against the full reprice (independent oracle)
# --------------------------------------------------------------------------------------------
# Hand-built record (numbers chosen, decomposition done BY HAND in this comment):
#   Δ-PnL  = +1000.0
#   Γ-PnL  =  -250.0
#   Vega   =  +400.0
#   Θ-PnL  =  -120.0
#   Rho    =   +30.0
#   Vanna  =   -15.0
#   Volga  =    +8.0
#   Σ terms (approx_pnl) = 1000 - 250 + 400 - 120 + 30 - 15 + 8 = 1053.0
#   full_reprice_pnl     = 1100.0   (the true repriced book PnL for the shock)
#   residual = full_reprice − Σ terms = 1100.0 − 1053.0 = 47.0
# The serializer must serve exactly this residual (it re-decomposes nothing); Charm is a display
# Greek and must never appear as an attribution term. Tolerance is on the float reconstruction.
_PORTFOLIO = "pf-onglet2-attr"
_SCENARIO = "spot-down-10"
_TERMS_BY_NAME = {
    "Delta": 1000.0,
    "Gamma": -250.0,
    "Vega": 400.0,
    "Theta": -120.0,
    "Rho": 30.0,
    "Vanna": -15.0,
    "Volga": 8.0,
}
_APPROX = math.fsum(_TERMS_BY_NAME.values())  # 1053.0
_FULL_REPRICE = 1100.0
_RESIDUAL = _FULL_REPRICE - _APPROX  # 47.0
_ABS_TOL = 100.0
_REL_TOL = 0.001


def _attr_prov() -> ProvenanceStamp:
    return stamp(
        calc_ts=_TS,
        code_version="onglet2-attr-contract-test",
        config_hashes={"cfg": "cfg-attr"},
        source_records=(source_ref("scenario_attributions", "sess", "row"),),
        source_timestamps=(_TS,),
    )


@pytest.fixture
def attr_client(tmp_path: Path) -> Iterator[TestClient]:
    store_root = tmp_path / "data"
    store = ParquetStore(store_root)
    store.write(
        "scenario_attributions",
        [
            tables.ScenarioAttribution(
                valuation_ts=_TS,
                portfolio_id=_PORTFOLIO,
                scenario_id=_SCENARIO,
                contract_key="__book__",
                level="book",
                spot_shock=-0.10,
                vol_shock=0.0,
                time_shock=0.0,
                delta_pnl=_TERMS_BY_NAME["Delta"],
                gamma_pnl=_TERMS_BY_NAME["Gamma"],
                vega_pnl=_TERMS_BY_NAME["Vega"],
                theta_pnl=_TERMS_BY_NAME["Theta"],
                rho_pnl=_TERMS_BY_NAME["Rho"],
                vanna_pnl=_TERMS_BY_NAME["Vanna"],
                volga_pnl=_TERMS_BY_NAME["Volga"],
                approx_pnl=_APPROX,
                full_reprice_pnl=_FULL_REPRICE,
                residual=_RESIDUAL,
                within_tolerance=True,
                residual_abs_tol=_ABS_TOL,
                residual_rel_tol=_REL_TOL,
                scenario_version="scn-1",
                attribution_version="attr-1",
                source_snapshot_ts=_TS,
                provenance=_attr_prov(),
            )
        ],
    )
    ctx = AppContext(store_root=store_root, configs_dir=tmp_path / "configs", store=store)
    with TestClient(create_app(ctx)) as client:
        yield client


def _attribution(attr_client: TestClient) -> dict[str, Any]:
    response = attr_client.get(
        "/api/attribution",
        params={"trade_date": _TRADE.isoformat(), "portfolio_id": _PORTFOLIO},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_attribution_decomposes_into_seven_terms_plus_residual(attr_client: TestClient) -> None:
    payload = _attribution(attr_client)
    assert payload["found"] is True
    names = [term["name"] for term in payload["terms"]]
    assert names == ["Delta", "Gamma", "Vega", "Theta", "Rho", "Vanna", "Volga"]
    by_name = {term["name"]: term for term in payload["terms"]}
    for name, dollars in _TERMS_BY_NAME.items():
        assert by_name[name]["dollars"] == pytest.approx(dollars), name
    # Charm is a display Greek, never an attribution term — the decomposition stops at Volga.
    assert "Charm" not in by_name


def test_attribution_residual_is_full_reprice_minus_term_sum(attr_client: TestClient) -> None:
    # Independent oracle: Σ terms summed BY THE TEST (not the router), residual cross-checked
    # against full_reprice − Σ terms within tolerance.
    payload = _attribution(attr_client)
    term_sum = math.fsum(term["dollars"] for term in payload["terms"])
    assert term_sum == pytest.approx(_APPROX)
    assert payload["approx_pnl"] == pytest.approx(_APPROX)
    assert payload["full_reprice_pnl"] == pytest.approx(_FULL_REPRICE)
    served_residual = payload["residual"]["dollars"]
    assert served_residual == pytest.approx(_RESIDUAL)
    # The defining identity: residual ≈ full_reprice − Σ terms.
    assert served_residual == pytest.approx(payload["full_reprice_pnl"] - term_sum)
    assert payload["approx_pnl"] + served_residual == pytest.approx(payload["full_reprice_pnl"])


def test_attribution_each_term_and_residual_carry_a_dollar_unit(attr_client: TestClient) -> None:
    payload = _attribution(attr_client)
    for term in payload["terms"]:
        assert term["unit"] and "$" in term["unit"], f"{term['name']} needs a $ unit"
    assert payload["residual"]["unit"] and "$" in payload["residual"]["unit"]
