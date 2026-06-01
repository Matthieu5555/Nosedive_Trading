"""Typed data contracts — the only objects that cross a workstream boundary.

Import the dataclass you need, the registry metadata, and ``validate`` from here.
Nobody outside Workstream A edits these definitions; a needed change is a request
routed to A, not an in-place edit, because every field ripples to four other
workstreams.
"""

from __future__ import annotations

from .bundles import ForwardDiagnostics, IvDiagnostics, SurfaceFitDiagnostics
from .errors import ContractError, ContractValidationError, UnknownTableError
from .instrument_key import (
    EVENT_TIMESTAMP_FIELDS,
    OPTION_RIGHTS,
    InstrumentKey,
)
from .registry import (
    REGISTRY,
    TableSpec,
    datetime_field_names,
    numeric_field_names,
    resolved_field_types,
    spec_for_table,
    table_for_contract,
)
from .tables import (
    ForwardCurvePoint,
    InstrumentMaster,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    QcResult,
    RawMarketEvent,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
)
from .validation import validate, validate_record

__all__ = [
    "EVENT_TIMESTAMP_FIELDS",
    "OPTION_RIGHTS",
    "REGISTRY",
    "ContractError",
    "ContractValidationError",
    "ForwardCurvePoint",
    "ForwardDiagnostics",
    "InstrumentKey",
    "InstrumentMaster",
    "IvDiagnostics",
    "IvPoint",
    "MarketStateSnapshot",
    "Position",
    "PricingResult",
    "QcResult",
    "RawMarketEvent",
    "RiskAggregate",
    "ScenarioResult",
    "SurfaceFitDiagnostics",
    "SurfaceGrid",
    "SurfaceParameters",
    "TableSpec",
    "UnknownTableError",
    "datetime_field_names",
    "numeric_field_names",
    "resolved_field_types",
    "spec_for_table",
    "table_for_contract",
    "validate",
    "validate_record",
]
