from __future__ import annotations

import copy
import math
from collections.abc import Sequence

import numpy as np
import pytest
from algotrading.infra.risk.decorrelation import (
    DecorrelationInputError,
    compute_decorrelation_diagnostics,
    factor_overlap_matrix,
    marginal_risk_contributions,
    shared_tail_overlap_matrix,
    stressed_pnl_correlation_matrix,
)
from algotrading.infra.risk.stress_surface import StressSurface


def _surface(grid: Sequence[Sequence[float]]) -> StressSurface:
    rows = tuple(tuple(float(v) for v in row) for row in grid)
    n_cols = len(rows[0]) if rows else 0
    return StressSurface(
        scenario_version="oracle-surface",
        spot_axis=tuple(float(i) for i in range(len(rows))),
        vol_axis=tuple(float(j) for j in range(n_cols)),
        pnl_grid=rows,
    )


def test_correlation_independent_oracle_negative() -> None:
    surface_a = _surface([[2.0, 4.0, 6.0, 8.0]])
    surface_b = _surface([[8.0, 5.0, 6.0, 1.0]])
    matrix = stressed_pnl_correlation_matrix([surface_a, surface_b])
    expected = -5.0 / math.sqrt(32.5)
    np.testing.assert_allclose(
        matrix[0][1],
        expected,
        atol=1e-12,
        err_msg=(
            "A=(2,4,6,8) mean5 dev(-3,-1,1,3); B=(8,5,6,1) mean5 dev(3,0,1,-4); "
            "cov=mean(-9,0,1,-12)=-5; var_a=mean(9,1,1,9)=5; var_b=mean(9,0,1,16)=6.5; "
            "r=-5/sqrt(5*6.5)=-5/sqrt(32.5)=-0.877058019307"
        ),
    )
    np.testing.assert_allclose(matrix[1][0], expected, atol=1e-12)
    np.testing.assert_allclose([matrix[0][0], matrix[1][1]], [1.0, 1.0], atol=1e-12)


def test_shared_tail_overlap_independent_oracle_jaccard() -> None:
    surface_a = _surface([[-9.0, -7.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]])
    surface_b = _surface([[0.0, -8.0, -3.0, 2.0, 2.0, 2.0, 2.0, 2.0]])
    matrix = shared_tail_overlap_matrix([surface_a, surface_b], tail_fraction=0.25)
    expected = 1.0 / 3.0
    np.testing.assert_allclose(
        matrix[0][1],
        expected,
        atol=1e-12,
        err_msg=(
            "8 nodes, tail_fraction 0.25 -> count=ceil(2)=2; "
            "A worst2 nodes {0,1}; B worst2 nodes {1,2}; "
            "Jaccard = |{1}| / |{0,1,2}| = 1/3"
        ),
    )


def test_factor_overlap_independent_oracle_cosine() -> None:
    matrix = factor_overlap_matrix([(3.0, 4.0, 0.0, 0.0, 0.0), (4.0, 0.0, 3.0, 0.0, 0.0)])
    expected = 12.0 / 25.0
    np.testing.assert_allclose(
        matrix[0][1],
        expected,
        atol=1e-12,
        err_msg=(
            "u=(3,4,0,0,0) v=(4,0,3,0,0); dot=12; |u|=5 |v|=5; cos=12/(5*5)=0.48"
        ),
    )
    np.testing.assert_allclose(matrix[1][0], expected, atol=1e-12)


def test_marginal_risk_contribution_independent_oracle_three_layers() -> None:
    surface_a = _surface([[-2.0, 1.0]])
    surface_b = _surface([[-1.0, -3.0]])
    surface_c = _surface([[4.0, -1.0]])
    contributions = marginal_risk_contributions([surface_a, surface_b, surface_c])
    np.testing.assert_allclose(
        contributions,
        (1.0, -3.0, 0.0),
        atol=1e-12,
        err_msg=(
            "colsum=(1,-3) book_worst=min=-3; "
            "drop A: (3,-4) worst-4 -> -3-(-4)=1; "
            "drop B: (2,0) worst0 -> -3-0=-3; "
            "drop C: (-3,-2) worst-3 -> -3-(-3)=0"
        ),
    )


def test_diagnostics_are_byte_identical_and_order_preserving() -> None:
    labels = ["alpha", "beta", "gamma"]
    surfaces = [
        _surface([[-2.0, 1.0], [3.0, 4.0]]),
        _surface([[5.0, -6.0], [7.0, 8.0]]),
        _surface([[0.0, -1.0], [2.0, -9.0]]),
    ]
    vectors = [
        [1.0, 2.0, 3.0, 4.0, 5.0],
        [-1.0, 2.0, -3.0, 4.0, -5.0],
        [9.0, 8.0, 7.0, 6.0, 5.0],
    ]
    labels_before = copy.deepcopy(labels)
    surfaces_before = copy.deepcopy(surfaces)
    vectors_before = copy.deepcopy(vectors)

    result = compute_decorrelation_diagnostics(
        layer_labels=labels,
        layer_surfaces=surfaces,
        layer_greek_vectors=vectors,
    )

    assert labels == labels_before
    assert vectors == vectors_before
    assert len(surfaces) == len(surfaces_before)
    for after, before in zip(surfaces, surfaces_before, strict=True):
        assert after.pnl_grid == before.pnl_grid
        assert after.spot_axis == before.spot_axis
        assert after.vol_axis == before.vol_axis
        assert after.scenario_version == before.scenario_version
    assert result.layer_labels == ("alpha", "beta", "gamma")
    assert len(result.stressed_pnl_correlation) == 3
    assert len(result.shared_tail_overlap) == 3
    assert len(result.factor_overlap) == 3
    assert len(result.marginal_risk_contribution) == 3


def test_diagnostics_are_deterministic_under_repeated_calls() -> None:
    surfaces = [_surface([[-2.0, 1.0], [3.0, 4.0]]), _surface([[5.0, -6.0], [7.0, 8.0]])]
    vectors = [[1.0, 2.0, 3.0, 4.0, 5.0], [-1.0, 2.0, -3.0, 4.0, -5.0]]
    first = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"], layer_surfaces=surfaces, layer_greek_vectors=vectors
    )
    second = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"], layer_surfaces=surfaces, layer_greek_vectors=vectors
    )
    assert first == second


def test_module_does_not_read_wall_clock_or_store() -> None:
    import inspect

    import algotrading.infra.risk.decorrelation as module

    source = inspect.getsource(module)
    for forbidden in ("datetime.now", "date.today", "time.time", "ParquetStore", ".read("):
        assert forbidden not in source, (
            f"point-in-time analytic must not reference {forbidden!r}"
        )


def test_gating_absent_realized_yields_no_fabricated_values() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"],
        layer_surfaces=[_surface([[1.0, 2.0]]), _surface([[3.0, 5.0]])],
        layer_greek_vectors=[[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0]],
        realized_series=None,
    )
    assert result.realized_correlation_unavailable_reason is not None
    assert result.realized_correlation_unavailable_reason.strip()
    assert result.marginal_sharpe_unavailable_reason is not None
    assert result.marginal_sharpe_unavailable_reason.strip()
    for attribute in ("realized_correlation", "marginal_sharpe", "marginal_sharpe_ratio"):
        assert not hasattr(result, attribute)


def test_single_layer_book_all_diagonal_ones() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=["solo"],
        layer_surfaces=[_surface([[-3.0, 1.0, 5.0]])],
        layer_greek_vectors=[[2.0, 0.0, 0.0, 0.0, 0.0]],
    )
    assert result.stressed_pnl_correlation == ((1.0,),)
    assert result.shared_tail_overlap == ((1.0,),)
    assert result.factor_overlap == ((1.0,),)
    np.testing.assert_allclose(
        result.marginal_risk_contribution,
        (-3.0,),
        atol=1e-12,
        err_msg="solo book worst = min(-3,1,5) = -3; drop it -> empty worst 0; -3-0=-3",
    )


def test_empty_book_is_empty_not_nan() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=[], layer_surfaces=[], layer_greek_vectors=[]
    )
    assert result.stressed_pnl_correlation == ()
    assert result.shared_tail_overlap == ()
    assert result.factor_overlap == ()
    assert result.marginal_risk_contribution == ()
    assert result.layer_labels == ()


def test_flat_zero_surface_correlation_is_all_nan() -> None:
    matrix = stressed_pnl_correlation_matrix(
        [_surface([[0.0, 0.0, 0.0]]), _surface([[0.0, 0.0, 0.0]])]
    )
    for i in range(2):
        for j in range(2):
            assert math.isnan(matrix[i][j]), f"flat layers give nan at [{i}][{j}]"


def test_zero_norm_greek_vectors_both_nan_pairwise() -> None:
    matrix = factor_overlap_matrix(
        [(0.0, 0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0, 0.0)]
    )
    for i in range(2):
        for j in range(2):
            assert math.isnan(matrix[i][j]), f"zero-norm pair gives nan at [{i}][{j}]"


def test_single_node_surface_correlation_is_nan() -> None:
    matrix = stressed_pnl_correlation_matrix([_surface([[5.0]]), _surface([[9.0]])])
    assert len(matrix) == 2
    for row in matrix:
        for cell in row:
            assert math.isnan(cell)


def test_non_finite_inputs_rejected_each_function() -> None:
    with pytest.raises(DecorrelationInputError):
        stressed_pnl_correlation_matrix([_surface([[1.0, math.inf]])])
    with pytest.raises(DecorrelationInputError):
        shared_tail_overlap_matrix([_surface([[1.0, -math.inf]])], tail_fraction=0.5)
    with pytest.raises(DecorrelationInputError):
        marginal_risk_contributions([_surface([[math.nan, 1.0]])])
    with pytest.raises(DecorrelationInputError):
        factor_overlap_matrix([(math.nan, 0.0, 0.0, 0.0, 0.0)])
