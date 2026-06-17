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


@dataclass(frozen=True, slots=True)
class RatesDiagnostics:
    """Per-pillar provenance/quality metadata for an ingested risk-free rate point (ADR 0054).

    `instrument` and `source` echo the typed-config pillar definition the point was ingested under;
    `source_day_count` / `source_compounding` record what the source PUBLISHED, before the on-ingest
    conversion to the canonical continuous/ACT-365 `rate` carried on the table. `quality_label` is a
    capture-quality flag (`good` | `fair` | `poor`).
    """

    source: str
    instrument: str
    source_day_count: str
    source_compounding: str
    quality_label: str
