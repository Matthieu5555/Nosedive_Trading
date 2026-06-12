"""Strategy composition — book over ordered layers (WS 2D, ``risk/book.py``).

The book is a *view* that layers and sums sub-strategies; it never re-solves them. The named
obligations (tasks/2D-strategy-composition.md test surface, TESTING.md): book Greeks are additive
three ways (independent hand sum / per-layer sum / flat union aggregate), reorder-invariant; the
combined PnL surface is the node-wise sum of the per-layer full-reprice surfaces; no decorrelation
optimiser reweights or drops the selection; the contract round-trips through storage with a complete
stamp. Expected values are independently derived (direct re-summation), not read from the code under
test.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from algotrading.core.config import MonetizationConfig, ScenarioConfig, StressSurfaceConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref
from algotrading.infra.contracts import BookGreeks
from algotrading.infra.pricing import dollar_greeks
from algotrading.infra.risk import (
    BookLayerInput,
    PositionRisk,
    aggregate_by_desk,
    book_stress_surface,
    build_book_greeks,
    position_risk,
)
from algotrading.infra.risk.stress_surface import stress_surface
from algotrading.infra.storage import ParquetStore
from fixtures.positions import CALL_100, CALL_105, PUT_95, PUT_100
from fixtures.records import make_stamp
from hypothesis import given, settings
from hypothesis import strategies as st

_MON = MonetizationConfig(version="mon-test")
_VAL_TS = datetime(2026, 6, 7, 20, 0, tzinfo=UTC)
_DECIMAL = ("net_delta", "net_gamma", "net_vega", "net_theta")
_DOLLAR = ("dollar_delta", "dollar_gamma", "dollar_vega", "dollar_theta", "dollar_rho")


def _line(valuation: object, qty: float) -> PositionRisk:
    return position_risk(portfolio_id="book-test", quantity=qty, valuation=valuation)


# Four distinct contracts (no key collisions, so layer partitions never collapse a lot).
_L_CALL2 = _line(CALL_100, 2.0)
_L_PUT_1 = _line(PUT_100, -1.0)
_L_CALL3 = _line(CALL_105, 3.0)
_L_PUT15 = _line(PUT_95, 1.5)
_LAYER_A = BookLayerInput("A", (_L_CALL2, _L_PUT_1))
_LAYER_B = BookLayerInput("B", (_L_CALL3,))
_LAYER_C = BookLayerInput("C", (_L_PUT15,))


def _stamp() -> ProvenanceStamp:
    # The "scenarios" config-hash key is load-bearing: the round-trip test asserts the
    # stamp names the scenario config that shaped the book rows.
    return make_stamp(
        (source_ref("market_state_snapshots", "book-test"),),
        config_hashes={"scenarios": "cfg-scn-0"},
    )


def _book(layers: list[BookLayerInput]) -> tuple[BookGreeks, ...]:
    return build_book_greeks(
        book_id="BK1",
        layers=layers,
        monetization=_MON,
        valuation_ts=_VAL_TS,
        source_snapshot_ts=_VAL_TS,
        provenance=_stamp(),
    )


def _combined(rows: tuple[BookGreeks, ...]) -> BookGreeks:
    return next(r for r in rows if r.level == "book")


def _layers(rows: tuple[BookGreeks, ...]) -> list[BookGreeks]:
    return [r for r in rows if r.level == "layer"]


def _dollar_delta_of(line: PositionRisk) -> float:
    return dollar_greeks(
        delta=line.greeks.delta,
        gamma=line.greeks.gamma,
        vega=line.greeks.vega,
        theta=line.greeks.theta,
        rho=line.greeks.rho,
        spot=line.valuation.spot,
        multiplier=line.valuation.multiplier,
        quantity=line.quantity,
        config=_MON,
    ).dollar_delta


def _scenario_config() -> ScenarioConfig:
    return ScenarioConfig(
        version="scn-book-test",
        spot_shocks=(-0.05, 0.05),
        vol_shocks=(0.05,),
        stress_surface=StressSurfaceConfig(
            version="ss-book-test",
            spot_shock_abs=0.5,
            vol_shock_abs=0.5,
            spot_steps=3,
            vol_steps=3,
        ),
    )


# -- independent oracle: combined equals the direct re-sum over all positions ----------------
def test_book_greeks_equal_sum_of_layers() -> None:
    """The combined book Greek equals the independent sum over every position (decimal + dollar).

    Oracle (re-derived here, not read from the code under test): the union is
    ``A=[2x AAPL C100, -1x AAPL P100], B=[3x AAPL C105]``. Combined net_delta is
    ``sum(line.position_delta)`` over those three lines; combined dollar_delta is
    ``sum(dollar_greeks(line).dollar_delta)`` over them — each line monetized at its own spot.
    """
    rows = _book([_LAYER_A, _LAYER_B])
    combined = _combined(rows)
    union = [*_LAYER_A.lines, *_LAYER_B.lines]

    expected_net_delta = math.fsum(line.position_delta for line in union)
    expected_net_gamma = math.fsum(line.position_gamma for line in union)
    expected_dollar_delta = math.fsum(_dollar_delta_of(line) for line in union)

    assert combined.net_delta == pytest.approx(expected_net_delta)
    assert combined.net_gamma == pytest.approx(expected_net_gamma)
    assert combined.dollar_delta == pytest.approx(expected_dollar_delta)
    # the dollar numbers are unit-tagged from the canonical home (per-1% gamma, per-365 theta)
    assert combined.dollar_gamma_unit == "$ per 1% move"
    assert combined.dollar_theta_unit == "$ per calendar day"


# -- three-ways-one-number: per-layer sum == flat union aggregate ----------------------------
def test_book_greeks_equal_flat_union_aggregate() -> None:
    """Combined (layer-then-sum) equals the flat aggregate over the union — exact, all Greeks."""
    rows = _book([_LAYER_A, _LAYER_B, _LAYER_C])
    combined = _combined(rows)
    layers = _layers(rows)
    union = [*_LAYER_A.lines, *_LAYER_B.lines, *_LAYER_C.lines]
    flat = aggregate_by_desk(union, portfolio_id="BK1", desk_of={})[0]

    # (a) combined == flat union aggregate, exact (same arithmetic substrate)
    assert combined.net_delta == flat.net_delta
    assert combined.net_gamma == flat.net_gamma
    assert combined.net_vega == flat.net_vega
    assert combined.net_theta == flat.net_theta
    # (b) combined == sum of per-layer rows (decimal AND dollar)
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == pytest.approx(
            math.fsum(getattr(layer, field) for layer in layers)
        )


# -- property: additive over any random partition of a position set --------------------------
_BASE = (_L_CALL2, _L_PUT_1, _L_CALL3, _L_PUT15)


@settings(max_examples=40)
@given(assignment=st.lists(st.integers(min_value=0, max_value=2), min_size=4, max_size=4))
def test_book_greeks_additive_property(assignment: list[int]) -> None:
    """Over a random layer partition of a fixed position set, combined == flat aggregate."""
    buckets: dict[int, list[PositionRisk]] = {}
    for line, layer_idx in zip(_BASE, assignment, strict=True):
        buckets.setdefault(layer_idx, []).append(line)
    layers = [BookLayerInput(f"L{k}", tuple(v)) for k, v in sorted(buckets.items())]

    combined = _combined(_book(layers))
    flat = aggregate_by_desk(list(_BASE), portfolio_id="BK1", desk_of={})[0]
    assert combined.net_delta == pytest.approx(flat.net_delta)
    assert combined.net_gamma == pytest.approx(flat.net_gamma)
    assert combined.net_vega == pytest.approx(flat.net_vega)
    assert combined.net_theta == pytest.approx(flat.net_theta)


# -- order-free: reordering layers leaves the combined Greeks identical -----------------------
def test_book_composition_reorder_invariant() -> None:
    """Reordering the layers cannot change the combined Greeks (order-free aggregate)."""
    forward = _combined(_book([_LAYER_A, _LAYER_B, _LAYER_C]))
    reversed_ = _combined(_book([_LAYER_C, _LAYER_B, _LAYER_A]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(forward, field) == pytest.approx(getattr(reversed_, field))


# -- combined PnL surface is the node-wise sum of the per-layer full-reprice surfaces ---------
def test_combined_pnl_surface_is_sum_of_layer_surfaces() -> None:
    """At every grid node the combined PnL equals the sum of the per-layer surfaces; finite."""
    config = _scenario_config()
    combined = book_stress_surface([_LAYER_A, _LAYER_B], config=config)
    surf_a = stress_surface(_LAYER_A.lines, config)
    surf_b = stress_surface(_LAYER_B.lines, config)

    assert len(combined.pnl_grid) == len(surf_a.pnl_grid)
    for i, row in enumerate(combined.pnl_grid):
        for j, cell in enumerate(row):
            assert math.isfinite(cell)
            assert cell == pytest.approx(surf_a.pnl_grid[i][j] + surf_b.pnl_grid[i][j])


def test_combined_pnl_uses_full_reprice_not_taylor() -> None:
    """The combined surface is the full reprice over the union (centre node reprices to 0 PnL)."""
    config = _scenario_config()
    combined = book_stress_surface([_LAYER_A, _LAYER_B], config=config)
    union = (*_LAYER_A.lines, *_LAYER_B.lines)
    union_surface = stress_surface(union, config)
    # identical to the direct full reprice of the union — not a Greek-multiplier shortcut
    assert combined.pnl_grid == union_surface.pnl_grid
    # the centre cell is the zero-shock reprice (book vs itself), exactly 0.0
    centre_i = len(combined.pnl_grid) // 2
    centre_j = len(combined.pnl_grid[0]) // 2
    assert combined.pnl_grid[centre_i][centre_j] == pytest.approx(0.0, abs=1e-9)


# -- guard: composition honours the operator's selection exactly (no optimiser) ---------------
def test_no_decorrelation_optimiser() -> None:
    """Composing never reweights/drops/dedups the selection — a repeated layer doubles the book."""
    single = _combined(_book([_LAYER_A]))
    doubled = _combined(_book([_LAYER_A, BookLayerInput("A-again", _LAYER_A.lines)]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(doubled, field) == pytest.approx(2.0 * getattr(single, field))


# -- seam: the book contract round-trips through storage with a complete stamp ----------------
def test_book_contract_roundtrip_and_stamp(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """BookGreeks rows write + read back equal, and carry a complete provenance stamp."""
    store = ParquetStore(tmp_path)
    rows = _book([_LAYER_A, _LAYER_B])
    store.write("book_greeks", list(rows))
    back = store.read("book_greeks")

    key = lambda r: (r.level, r.layer_label)  # noqa: E731
    assert sorted(back, key=key) == sorted(rows, key=key)

    combined = _combined(rows)
    assert combined.provenance.stamp_hash  # non-empty content hash
    assert "scenarios" in combined.provenance.config_hashes
    assert combined.composition_version == "composition-1.0.0"


# -- edge cases (the floor) -------------------------------------------------------------------
def test_empty_book_is_a_single_zero_combined_row() -> None:
    """A book with no layers is one labeled zero-valued combined row — never a crash."""
    rows = _book([])
    assert len(rows) == 1
    combined = _combined(rows)
    assert not _layers(rows)
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == 0.0


def test_single_layer_book_combined_equals_that_layer() -> None:
    """A one-layer book's combined row equals that layer exactly."""
    rows = _book([_LAYER_A])
    combined = _combined(rows)
    (layer,) = _layers(rows)
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == pytest.approx(getattr(layer, field))


def test_layer_with_zero_positions_contributes_nothing() -> None:
    """An empty layer is a labeled zero row and does not change the combined aggregate."""
    with_empty = _combined(_book([_LAYER_A, BookLayerInput("empty", ())]))
    without = _combined(_book([_LAYER_A]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(with_empty, field) == pytest.approx(getattr(without, field))
