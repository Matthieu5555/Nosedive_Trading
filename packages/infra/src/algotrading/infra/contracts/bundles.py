from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForwardDiagnostics:

    method: str
    candidate_count: int
    residual_mad: float
    quality_label: str


@dataclass(frozen=True, slots=True)
class IvDiagnostics:

    converged: bool
    iterations: int
    residual: float
    status: str


@dataclass(frozen=True, slots=True)
class SurfaceFitDiagnostics:

    rmse: float
    n_points: int
    arb_free: bool
    bound_hits: tuple[str, ...] | None = None
    converged: bool | None = None
