"""Small diagnostic bundles attached to derived records.

These travel inside a contract as evidence for *why* a number came out the way it
did — which strikes fed a forward, whether the solver converged, how well a
surface fit. They are kept as their own frozen dataclasses (not loose dicts) so
the fields are typed and discoverable, and serialized as a JSON column so the
storage tables stay flat.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForwardDiagnostics:
    """Why a forward estimate was chosen for a maturity."""

    method: str
    candidate_count: int
    residual_mad: float
    quality_label: str


@dataclass(frozen=True, slots=True)
class IvDiagnostics:
    """How the implied-volatility inversion behaved for one contract."""

    converged: bool
    iterations: int
    residual: float
    status: str


@dataclass(frozen=True, slots=True)
class SurfaceFitDiagnostics:
    """How well an SVI slice fit the observed implied-vol points."""

    rmse: float
    n_points: int
    arb_free: bool
