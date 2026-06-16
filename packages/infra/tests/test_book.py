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


_L_CALL2 = _line(CALL_100, 2.0)
_L_PUT_1 = _line(PUT_100, -1.0)
_L_CALL3 = _line(CALL_105, 3.0)
_L_PUT15 = _line(PUT_95, 1.5)
_LAYER_A = BookLayerInput("A", (_L_CALL2, _L_PUT_1))
_LAYER_B = BookLayerInput("B", (_L_CALL3,))
_LAYER_C = BookLayerInput("C", (_L_PUT15,))


def _stamp() -> ProvenanceStamp:
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


def test_book_greeks_equal_sum_of_layers() -> None:
    rows = _book([_LAYER_A, _LAYER_B])
    combined = _combined(rows)
    union = [*_LAYER_A.lines, *_LAYER_B.lines]

    expected_net_delta = math.fsum(line.position_delta for line in union)
    expected_net_gamma = math.fsum(line.position_gamma for line in union)
    expected_dollar_delta = math.fsum(_dollar_delta_of(line) for line in union)

    assert combined.net_delta == pytest.approx(expected_net_delta)
    assert combined.net_gamma == pytest.approx(expected_net_gamma)
    assert combined.dollar_delta == pytest.approx(expected_dollar_delta)
    assert combined.dollar_gamma_unit == "$ per 1% move"
    assert combined.dollar_theta_unit == "$ per calendar day"


def test_book_greeks_equal_flat_union_aggregate() -> None:
    rows = _book([_LAYER_A, _LAYER_B, _LAYER_C])
    combined = _combined(rows)
    layers = _layers(rows)
    union = [*_LAYER_A.lines, *_LAYER_B.lines, *_LAYER_C.lines]
    flat = aggregate_by_desk(union, portfolio_id="BK1", desk_of={})[0]

    assert combined.net_delta == flat.net_delta
    assert combined.net_gamma == flat.net_gamma
    assert combined.net_vega == flat.net_vega
    assert combined.net_theta == flat.net_theta
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == pytest.approx(
            math.fsum(getattr(layer, field) for layer in layers)
        )


_BASE = (_L_CALL2, _L_PUT_1, _L_CALL3, _L_PUT15)


@settings(max_examples=40)
@given(assignment=st.lists(st.integers(min_value=0, max_value=2), min_size=4, max_size=4))
def test_book_greeks_additive_property(assignment: list[int]) -> None:
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


def test_book_composition_reorder_invariant() -> None:
    forward = _combined(_book([_LAYER_A, _LAYER_B, _LAYER_C]))
    reversed_ = _combined(_book([_LAYER_C, _LAYER_B, _LAYER_A]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(forward, field) == pytest.approx(getattr(reversed_, field))


def test_combined_pnl_surface_is_sum_of_layer_surfaces() -> None:
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
    config = _scenario_config()
    combined = book_stress_surface([_LAYER_A, _LAYER_B], config=config)
    union = (*_LAYER_A.lines, *_LAYER_B.lines)
    union_surface = stress_surface(union, config)
    assert combined.pnl_grid == union_surface.pnl_grid
    centre_i = len(combined.pnl_grid) // 2
    centre_j = len(combined.pnl_grid[0]) // 2
    assert combined.pnl_grid[centre_i][centre_j] == pytest.approx(0.0, abs=1e-9)


def test_no_decorrelation_optimiser() -> None:
    single = _combined(_book([_LAYER_A]))
    doubled = _combined(_book([_LAYER_A, BookLayerInput("A-again", _LAYER_A.lines)]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(doubled, field) == pytest.approx(2.0 * getattr(single, field))


def test_book_contract_roundtrip_and_stamp(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ParquetStore(tmp_path)
    rows = _book([_LAYER_A, _LAYER_B])
    store.write("book_greeks", list(rows))
    back = store.read("book_greeks")

    key = lambda r: (r.level, r.layer_label)  # noqa: E731
    assert sorted(back, key=key) == sorted(rows, key=key)

    combined = _combined(rows)
    assert combined.provenance.stamp_hash
    assert "scenarios" in combined.provenance.config_hashes
    assert combined.composition_version == "composition-1.0.0"


def test_empty_book_is_a_single_zero_combined_row() -> None:
    rows = _book([])
    assert len(rows) == 1
    combined = _combined(rows)
    assert not _layers(rows)
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == 0.0


def test_single_layer_book_combined_equals_that_layer() -> None:
    rows = _book([_LAYER_A])
    combined = _combined(rows)
    (layer,) = _layers(rows)
    for field in _DECIMAL + _DOLLAR:
        assert getattr(combined, field) == pytest.approx(getattr(layer, field))


def test_layer_with_zero_positions_contributes_nothing() -> None:
    with_empty = _combined(_book([_LAYER_A, BookLayerInput("empty", ())]))
    without = _combined(_book([_LAYER_A]))
    for field in _DECIMAL + _DOLLAR:
        assert getattr(with_empty, field) == pytest.approx(getattr(without, field))
