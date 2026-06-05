"""Typed data contracts — the only objects that cross a layer/workstream boundary.

This package is the frozen seam M0 hands every other workstream. Import the dataclass
you need, ``validate``/``validate_record``, ``table_for_contract``/``spec_for_table``,
and the two protocols the merge hinges on:

* :class:`StorageRepository` — the storage port every store satisfies and every
  consumer reads/writes through (no module reaches into Parquet/DuckDB directly).
* :class:`BrokerSession` — the broker-agnostic market-data seam M5's adapters
  implement and M4's actor drives.

The registry's introspection machinery (``REGISTRY``, ``resolved_field_types`` and
friends) is deliberately *not* re-exported: it is how the storage codec and validators
are built, not something a consumer should reassemble against. A change to these
definitions is a request routed through M0, never an in-place edit, because every
field ripples to the other workstreams.
"""

from __future__ import annotations

from .broker import BrokerSession, BrokerTick, content_event_id
from .bundles import ForwardDiagnostics, IvDiagnostics, SurfaceFitDiagnostics
from .errors import ContractError, ContractValidationError, UnknownTableError
from .instrument_key import (
    EVENT_TIMESTAMP_FIELDS,
    OPTION_RIGHTS,
    InstrumentKey,
    broker_contract_id_from_canonical,
)
from .ports import StorageRepository
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
    TriageRecord,
)
from .validation import validate, validate_record

__all__ = [
    "EVENT_TIMESTAMP_FIELDS",
    "OPTION_RIGHTS",
    "BrokerSession",
    "BrokerTick",
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
    "StorageRepository",
    "SurfaceFitDiagnostics",
    "SurfaceGrid",
    "SurfaceParameters",
    "TableSpec",
    "TriageRecord",
    "UnknownTableError",
    "broker_contract_id_from_canonical",
    "content_event_id",
    "spec_for_table",
    "table_for_contract",
    "validate",
    "validate_record",
]
