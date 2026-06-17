from __future__ import annotations

import copy
import math
from collections.abc import Sequence

import numpy as np
import pytest
from algotrading.infra.risk.decorrelation import (
    DECORRELATION_VERSION,
    DEFAULT_TAIL_FRACTION,
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
        scenario_version="test-surface",
        spot_axis=tuple(float(i) for i in range(len(rows))),
        vol_axis=tuple(float(j) for j in range(n_cols)),
        pnl_grid=rows,
    )


def test_stressed_pnl_correlation_identical_layers_is_one() -> None:
    grid = [[1.0, -2.0, 3.0], [-4.0, 5.0, -6.0]]
    matrix = stressed_pnl_correlation_matrix([_surface(grid), _surface(grid)])
    np.testing.assert_allclose(matrix[0][1], 1.0, atol=1e-12)
    np.testing.assert_allclose(matrix[1][0], 1.0, atol=1e-12)
    np.testing.assert_allclose([matrix[0][0], matrix[1][1]], [1.0, 1.0])


def test_stressed_pnl_correlation_anticorrelated_layers_is_negative_one() -> None:
    grid = [[1.0, -2.0, 3.0], [-4.0, 5.0, -6.0]]
    negated = [[-v for v in row] for row in grid]
    matrix = stressed_pnl_correlation_matrix([_surface(grid), _surface(negated)])
    np.testing.assert_allclose(matrix[0][1], -1.0, atol=1e-12)


def test_stressed_pnl_correlation_hand_computed_2x2() -> None:
    surface_a = _surface([[1.0, 3.0]])
    surface_b = _surface([[2.0, 8.0]])
    matrix = stressed_pnl_correlation_matrix([surface_a, surface_b])
    expected = 1.0
    np.testing.assert_allclose(
        matrix[0][1],
        expected,
        atol=1e-12,
        err_msg="x=(1,3) mean 2 dev (-1,1); y=(2,8) mean 5 dev (-3,3); "
        "cov=(3+3)/2=3 sx=1 sy=3 r=3/(1*3)=1.0",
    )


def test_stressed_pnl_correlation_hand_computed_partial() -> None:
    surface_a = _surface([[0.0, 1.0, 2.0]])
    surface_b = _surface([[0.0, 0.0, 3.0]])
    matrix = stressed_pnl_correlation_matrix([surface_a, surface_b])
    expected = 0.8660254037844387
    np.testing.assert_allclose(
        matrix[0][1],
        expected,
        atol=1e-9,
        err_msg="x dev (-1,0,1) y dev (-1,-1,2); cov=Σ(1+0+2)/3=1; "
        "sx=sqrt(2/3) sy=sqrt(6/3)=sqrt2; r=1/(sqrt(2/3)*sqrt2)=sqrt(3)/2",
    )


def test_stressed_pnl_correlation_flat_layer_is_nan() -> None:
    flat = _surface([[5.0, 5.0, 5.0]])
    varying = _surface([[1.0, 2.0, 3.0]])
    matrix = stressed_pnl_correlation_matrix([flat, varying])
    assert math.isnan(matrix[0][0])
    assert math.isnan(matrix[0][1])
    assert math.isnan(matrix[1][0])
    np.testing.assert_allclose(matrix[1][1], 1.0)


def test_shared_tail_overlap_same_worst_nodes_is_one() -> None:
    grid_a = [[-10.0, -9.0, 1.0, 2.0]]
    grid_b = [[-8.0, -7.0, 5.0, 6.0]]
    matrix = shared_tail_overlap_matrix(
        [_surface(grid_a), _surface(grid_b)], tail_fraction=0.5
    )
    np.testing.assert_allclose(matrix[0][1], 1.0)
    np.testing.assert_allclose(matrix[0][0], 1.0)


def test_shared_tail_overlap_disjoint_worst_nodes_is_zero() -> None:
    grid_a = [[-10.0, -9.0, 1.0, 2.0]]
    grid_b = [[5.0, 6.0, -8.0, -7.0]]
    matrix = shared_tail_overlap_matrix(
        [_surface(grid_a), _surface(grid_b)], tail_fraction=0.5
    )
    np.testing.assert_allclose(matrix[0][1], 0.0)


def test_shared_failure_mode_is_visibly_detected() -> None:
    s1 = _surface(
        [
            [-50.0, -40.0, 5.0],
            [-45.0, -38.0, 6.0],
            [10.0, 11.0, 12.0],
        ]
    )
    s3 = _surface(
        [
            [-30.0, -25.0, 8.0],
            [-28.0, -24.0, 9.0],
            [14.0, 15.0, 16.0],
        ]
    )
    tail = shared_tail_overlap_matrix([s1, s3], tail_fraction=0.4)
    assert tail[0][1] >= 0.5, "S1/S3 must overlap on their low-vol worst nodes"

    s1_vega = (-100.0, 0.0, 0.0, 0.0, 0.0)
    s3_vega = (-120.0, 0.0, 0.0, 0.0, 0.0)
    factor = factor_overlap_matrix([s1_vega, s3_vega])
    np.testing.assert_allclose(
        factor[0][1], 1.0, atol=1e-12, err_msg="both short vega -> cosine 1.0"
    )


def test_factor_overlap_parallel_greek_vectors_is_one() -> None:
    matrix = factor_overlap_matrix([(1.0, 2.0, 3.0, 0.0, 0.0), (2.0, 4.0, 6.0, 0.0, 0.0)])
    np.testing.assert_allclose(matrix[0][1], 1.0, atol=1e-12)


def test_factor_overlap_orthogonal_is_zero() -> None:
    matrix = factor_overlap_matrix([(1.0, 0.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0, 0.0)])
    np.testing.assert_allclose(matrix[0][1], 0.0, atol=1e-12)


def test_factor_overlap_opposite_is_negative_one() -> None:
    matrix = factor_overlap_matrix([(1.0, 2.0, 3.0, 0.0, 0.0), (-1.0, -2.0, -3.0, 0.0, 0.0)])
    np.testing.assert_allclose(matrix[0][1], -1.0, atol=1e-12)


def test_factor_overlap_zero_norm_layer_is_nan() -> None:
    matrix = factor_overlap_matrix([(0.0, 0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0, 1.0, 1.0)])
    assert math.isnan(matrix[0][0])
    assert math.isnan(matrix[0][1])
    np.testing.assert_allclose(matrix[1][1], 1.0)


def test_marginal_risk_contribution_leave_one_out() -> None:
    surface_a = _surface([[-3.0, 1.0], [2.0, 4.0]])
    surface_b = _surface([[-1.0, -5.0], [2.0, 1.0]])
    contributions = marginal_risk_contributions([surface_a, surface_b])
    book_worst = -4.0
    np.testing.assert_allclose(
        contributions[0],
        book_worst - (-5.0),
        atol=1e-12,
        err_msg="sum nodes (-4,-4,4,5) worst=-4; B-alone worst min(-1,-5,2,1)=-5; -4-(-5)=1",
    )
    np.testing.assert_allclose(
        contributions[1],
        book_worst - (-3.0),
        atol=1e-12,
        err_msg="A-alone worst min(-3,1,2,4)=-3; -4-(-3)=-1",
    )


def test_diagnostics_are_read_only() -> None:
    labels = ["a", "b"]
    surfaces = [_surface([[-1.0, 2.0], [3.0, 4.0]]), _surface([[5.0, -6.0], [7.0, 8.0]])]
    vectors = [[1.0, 2.0, 3.0, 4.0, 5.0], [-1.0, -2.0, -3.0, -4.0, -5.0]]
    labels_before = copy.deepcopy(labels)
    surfaces_before = copy.deepcopy(surfaces)
    vectors_before = copy.deepcopy(vectors)

    result = compute_decorrelation_diagnostics(
        layer_labels=labels,
        layer_surfaces=surfaces,
        layer_greek_vectors=vectors,
    )

    assert labels == labels_before
    assert surfaces == surfaces_before
    assert vectors == vectors_before
    assert result.layer_labels == ("a", "b")


def test_no_optimiser_present() -> None:
    import algotrading.infra.risk.decorrelation as module

    public_names = [name for name in dir(module) if not name.startswith("_")]
    forbidden = ("optimi", "reweight", "minimi", "maximi", "select_")
    for name in public_names:
        lowered = name.lower()
        assert not any(token in lowered for token in forbidden), name

    labels = ["first", "second", "third"]
    surfaces = [_surface([[float(i)]]) for i in range(3)]
    vectors = [[float(i), 0.0, 0.0, 0.0, 0.0] for i in range(1, 4)]
    result = compute_decorrelation_diagnostics(
        layer_labels=labels,
        layer_surfaces=surfaces,
        layer_greek_vectors=vectors,
    )
    assert list(result.layer_labels) == labels


def test_realized_correlation_is_gated_not_fabricated() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"],
        layer_surfaces=[_surface([[1.0, 2.0]]), _surface([[3.0, 4.0]])],
        layer_greek_vectors=[[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0]],
        realized_series=None,
    )
    assert isinstance(result.realized_correlation_unavailable_reason, str)
    assert result.realized_correlation_unavailable_reason
    assert isinstance(result.marginal_sharpe_unavailable_reason, str)
    assert result.marginal_sharpe_unavailable_reason
    assert not hasattr(result, "realized_correlation")
    assert result.version == DECORRELATION_VERSION


def test_realized_reason_clears_when_series_supplied() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"],
        layer_surfaces=[_surface([[1.0, 2.0]]), _surface([[3.0, 4.0]])],
        layer_greek_vectors=[[1.0, 0.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0, 0.0]],
        realized_series=[[0.1, 0.2], [0.3, 0.4]],
    )
    assert result.realized_correlation_unavailable_reason is None
    assert result.marginal_sharpe_unavailable_reason is None


def test_single_layer_book_matrices_are_one_by_one() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=["solo"],
        layer_surfaces=[_surface([[-1.0, 2.0, 3.0]])],
        layer_greek_vectors=[[1.0, 0.0, 0.0, 0.0, 0.0]],
    )
    assert result.stressed_pnl_correlation == ((1.0,),)
    assert result.shared_tail_overlap == ((1.0,),)
    assert result.factor_overlap == ((1.0,),)
    assert len(result.marginal_risk_contribution) == 1


def test_single_constant_layer_correlation_is_nan() -> None:
    matrix = stressed_pnl_correlation_matrix([_surface([[2.0, 2.0]])])
    assert math.isnan(matrix[0][0])


def test_empty_book_returns_empty_tuples() -> None:
    result = compute_decorrelation_diagnostics(
        layer_labels=[],
        layer_surfaces=[],
        layer_greek_vectors=[],
    )
    assert result.stressed_pnl_correlation == ()
    assert result.shared_tail_overlap == ()
    assert result.factor_overlap == ()
    assert result.marginal_risk_contribution == ()
    assert result.layer_labels == ()


def test_non_finite_surface_is_rejected() -> None:
    with pytest.raises(DecorrelationInputError):
        stressed_pnl_correlation_matrix([_surface([[1.0, math.inf]])])


def test_non_finite_greek_vector_is_rejected() -> None:
    with pytest.raises(DecorrelationInputError):
        factor_overlap_matrix([[math.nan, 0.0, 0.0, 0.0, 0.0]])


def test_mismatched_lengths_rejected() -> None:
    with pytest.raises(DecorrelationInputError):
        compute_decorrelation_diagnostics(
            layer_labels=["a", "b"],
            layer_surfaces=[_surface([[1.0]])],
            layer_greek_vectors=[[1.0, 0.0, 0.0, 0.0, 0.0]],
        )


def test_differing_node_counts_rejected() -> None:
    with pytest.raises(DecorrelationInputError):
        stressed_pnl_correlation_matrix([_surface([[1.0, 2.0]]), _surface([[1.0]])])


def test_invalid_tail_fraction_rejected() -> None:
    with pytest.raises(DecorrelationInputError):
        shared_tail_overlap_matrix([_surface([[1.0, 2.0]])], tail_fraction=0.0)
    with pytest.raises(DecorrelationInputError):
        shared_tail_overlap_matrix([_surface([[1.0, 2.0]])], tail_fraction=1.5)


def test_tail_fraction_defaults_to_module_constant() -> None:
    grid = [[-float(i) for i in range(20)]]
    explicit = shared_tail_overlap_matrix(
        [_surface(grid), _surface(grid)], tail_fraction=DEFAULT_TAIL_FRACTION
    )
    result = compute_decorrelation_diagnostics(
        layer_labels=["a", "b"],
        layer_surfaces=[_surface(grid), _surface(grid)],
        layer_greek_vectors=[[1.0, 0, 0, 0, 0], [1.0, 0, 0, 0, 0]],
    )
    assert result.shared_tail_overlap == explicit
