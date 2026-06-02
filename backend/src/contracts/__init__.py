"""Typed data contracts — the only objects that cross a workstream boundary.

Import the dataclass you need, ``validate``/``validate_record``, and — if you hold
an object and need its table — ``table_for_contract``/``spec_for_table`` from here.
That is the whole public seam. The registry's introspection machinery
(``REGISTRY``, ``resolved_field_types`` and friends) is deliberately *not*
re-exported: it is how the storage codec and validators are built, not something a
consumer should reassemble against. A consumer that finds itself reaching for it
wants a new method on the seam, routed through Workstream A, not the internals.

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
    broker_contract_id_from_canonical,
)
from .registry import (
    TableSpec,
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
    "broker_contract_id_from_canonical",
    "spec_for_table",
    "table_for_contract",
    "validate",
    "validate_record",
]
