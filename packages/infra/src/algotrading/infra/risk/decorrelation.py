from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .stress_surface import StressSurface

DECORRELATION_VERSION = "decorrelation-1.0.0"

DEFAULT_TAIL_FRACTION = 0.1

_REALIZED_CORRELATION_UNAVAILABLE = (
    "no banked per-layer realized P&L series for a composed live book"
)
_MARGINAL_SHARPE_UNAVAILABLE = (
    "no banked per-layer realized P&L series for a composed live book; "
    "marginal Sharpe needs a return distribution, not a stress surface"
)


class DecorrelationInputError(Exception):
    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class DecorrelationDiagnostics:
    layer_labels: tuple[str, ...]
    stressed_pnl_correlation: tuple[tuple[float, ...], ...]
    shared_tail_overlap: tuple[tuple[float, ...], ...]
    factor_overlap: tuple[tuple[float, ...], ...]
    marginal_risk_contribution: tuple[float, ...]
    realized_correlation_unavailable_reason: str | None
    marginal_sharpe_unavailable_reason: str | None
    version: str


def _flatten(surface: StressSurface) -> np.ndarray:
    rows = [list(row) for row in surface.pnl_grid]
    flat = [value for row in rows for value in row]
    array = np.asarray(flat, dtype=np.float64)
    if array.size and not np.all(np.isfinite(array)):
        raise DecorrelationInputError("pnl_grid", flat, "contains a non-finite value")
    return array


def _flattened_matrix(layer_surfaces: Sequence[StressSurface]) -> np.ndarray:
    flattened = [_flatten(surface) for surface in layer_surfaces]
    if not flattened:
        return np.empty((0, 0), dtype=np.float64)
    lengths = {array.size for array in flattened}
    if len(lengths) != 1:
        raise DecorrelationInputError(
            "pnl_grid", sorted(lengths), "layer surfaces have differing node counts"
        )
    return np.vstack(flattened)


def _square_to_tuples(matrix: np.ndarray) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(value) for value in row) for row in matrix)


def stressed_pnl_correlation_matrix(
    layer_surfaces: Sequence[StressSurface],
) -> tuple[tuple[float, ...], ...]:
    stacked = _flattened_matrix(layer_surfaces)
    n_layers = stacked.shape[0]
    if n_layers == 0:
        return ()
    if stacked.shape[1] < 2:
        return _square_to_tuples(np.full((n_layers, n_layers), np.nan))
    with np.errstate(invalid="ignore", divide="ignore"):
        correlation = np.corrcoef(stacked)
    correlation = np.atleast_2d(correlation)
    constant = np.array(
        [np.ptp(stacked[i]) == 0.0 for i in range(n_layers)], dtype=bool
    )
    for i in range(n_layers):
        for j in range(n_layers):
            if i == j:
                correlation[i, j] = np.nan if constant[i] else 1.0
            elif constant[i] or constant[j]:
                correlation[i, j] = np.nan
    return _square_to_tuples(correlation)


def _worst_node_indices(values: np.ndarray, *, count: int) -> frozenset[int]:
    order = np.argsort(values, kind="stable")
    return frozenset(int(index) for index in order[:count])


def shared_tail_overlap_matrix(
    layer_surfaces: Sequence[StressSurface], *, tail_fraction: float
) -> tuple[tuple[float, ...], ...]:
    if not 0.0 < tail_fraction <= 1.0:
        raise DecorrelationInputError(
            "tail_fraction", tail_fraction, "must be in the half-open interval (0, 1]"
        )
    stacked = _flattened_matrix(layer_surfaces)
    n_layers = stacked.shape[0]
    if n_layers == 0:
        return ()
    n_nodes = stacked.shape[1]
    count = max(1, math.ceil(tail_fraction * n_nodes))
    worst = [_worst_node_indices(stacked[i], count=count) for i in range(n_layers)]
    overlap = np.empty((n_layers, n_layers), dtype=np.float64)
    for i in range(n_layers):
        for j in range(n_layers):
            if i == j:
                overlap[i, j] = 1.0
                continue
            union = worst[i] | worst[j]
            overlap[i, j] = (
                len(worst[i] & worst[j]) / len(union) if union else math.nan
            )
    return _square_to_tuples(overlap)


def factor_overlap_matrix(
    layer_greek_vectors: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    n_layers = len(layer_greek_vectors)
    if n_layers == 0:
        return ()
    vectors = np.asarray([list(vector) for vector in layer_greek_vectors], dtype=np.float64)
    if vectors.size and not np.all(np.isfinite(vectors)):
        raise DecorrelationInputError(
            "layer_greek_vectors", vectors.tolist(), "contains a non-finite value"
        )
    norms = np.linalg.norm(vectors, axis=1)
    overlap = np.empty((n_layers, n_layers), dtype=np.float64)
    for i in range(n_layers):
        for j in range(n_layers):
            if norms[i] == 0.0 or norms[j] == 0.0:
                overlap[i, j] = math.nan
            else:
                cosine = float(np.dot(vectors[i], vectors[j]) / (norms[i] * norms[j]))
                overlap[i, j] = max(-1.0, min(1.0, cosine))
    return _square_to_tuples(overlap)


def _book_worst_loss(stacked: np.ndarray) -> float:
    if stacked.shape[0] == 0 or stacked.shape[1] == 0:
        return 0.0
    return float(np.min(stacked.sum(axis=0)))


def marginal_risk_contributions(
    layer_surfaces: Sequence[StressSurface],
) -> tuple[float, ...]:
    stacked = _flattened_matrix(layer_surfaces)
    n_layers = stacked.shape[0]
    if n_layers == 0:
        return ()
    book_worst = _book_worst_loss(stacked)
    contributions: list[float] = []
    for i in range(n_layers):
        without = np.delete(stacked, i, axis=0)
        contributions.append(book_worst - _book_worst_loss(without))
    return tuple(contributions)


def compute_decorrelation_diagnostics(
    *,
    layer_labels: Sequence[str],
    layer_surfaces: Sequence[StressSurface],
    layer_greek_vectors: Sequence[Sequence[float]],
    tail_fraction: float = DEFAULT_TAIL_FRACTION,
    realized_series: Sequence[Sequence[float]] | None = None,
) -> DecorrelationDiagnostics:
    n_layers = len(layer_labels)
    if len(layer_surfaces) != n_layers or len(layer_greek_vectors) != n_layers:
        raise DecorrelationInputError(
            "layer_labels",
            (n_layers, len(layer_surfaces), len(layer_greek_vectors)),
            "labels, surfaces and greek vectors must have matching lengths",
        )
    realized_reason = (
        _REALIZED_CORRELATION_UNAVAILABLE if realized_series is None else None
    )
    marginal_sharpe_reason = (
        _MARGINAL_SHARPE_UNAVAILABLE if realized_series is None else None
    )
    return DecorrelationDiagnostics(
        layer_labels=tuple(layer_labels),
        stressed_pnl_correlation=stressed_pnl_correlation_matrix(layer_surfaces),
        shared_tail_overlap=shared_tail_overlap_matrix(
            layer_surfaces, tail_fraction=tail_fraction
        ),
        factor_overlap=factor_overlap_matrix(layer_greek_vectors),
        marginal_risk_contribution=marginal_risk_contributions(layer_surfaces),
        realized_correlation_unavailable_reason=realized_reason,
        marginal_sharpe_unavailable_reason=marginal_sharpe_reason,
        version=DECORRELATION_VERSION,
    )
