"""The table registry: one row of metadata per contract.

This is the single place that knows, for each table family, its primary key, its
storage layer, whether it is append-only, whether it must carry provenance and a
source-snapshot back-reference, and which numeric fields must stay positive or
non-negative. Validation and the storage codec both read from here, so the rules
live once and cannot drift between the two.

Field *types* (which columns are numeric, which are timestamps, which are nested
objects) are derived from the dataclass type hints rather than re-listed, so
adding a field to a contract cannot silently desync a hand-maintained list.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from .errors import UnknownTableError
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


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Everything the platform needs to know about one table family."""

    name: str
    contract: type
    primary_key: tuple[str, ...]
    layer: str
    append_only: bool
    requires_provenance: bool
    requires_source_snapshot_ts: bool
    positive_fields: tuple[str, ...]
    non_negative_fields: tuple[str, ...]


REGISTRY: dict[str, TableSpec] = {
    "instrument_master": TableSpec(
        name="instrument_master",
        contract=InstrumentMaster,
        primary_key=("instrument_key", "as_of_date"),
        layer="raw",
        append_only=True,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "raw_market_events": TableSpec(
        name="raw_market_events",
        contract=RawMarketEvent,
        primary_key=("session_id", "event_id"),
        layer="raw",
        append_only=True,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "market_state_snapshots": TableSpec(
        name="market_state_snapshots",
        contract=MarketStateSnapshot,
        primary_key=("snapshot_ts", "instrument_key"),
        layer="snapshot",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=False,
        positive_fields=("reference_spot",),
        non_negative_fields=("bid", "ask", "spread_pct", "completeness"),
    ),
    "forward_curve": TableSpec(
        name="forward_curve",
        contract=ForwardCurvePoint,
        primary_key=("snapshot_ts", "underlying", "maturity_years"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=("forward", "maturity_years"),
        non_negative_fields=(),
    ),
    "iv_points": TableSpec(
        name="iv_points",
        contract=IvPoint,
        primary_key=("snapshot_ts", "contract_key"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=("iv", "total_variance"),
    ),
    "surface_parameters": TableSpec(
        name="surface_parameters",
        contract=SurfaceParameters,
        primary_key=("snapshot_ts", "underlying", "maturity_years", "model_version"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=("maturity_years", "svi_b", "svi_sigma"),
        non_negative_fields=(),
    ),
    "surface_grid": TableSpec(
        name="surface_grid",
        contract=SurfaceGrid,
        primary_key=("snapshot_ts", "underlying", "maturity_years", "moneyness_bucket"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=("maturity_years",),
        non_negative_fields=("total_variance",),
    ),
    "pricing_results": TableSpec(
        name="pricing_results",
        contract=PricingResult,
        primary_key=("snapshot_ts", "contract_key", "pricer_version"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=("gamma", "vega"),
    ),
    "positions": TableSpec(
        name="positions",
        contract=Position,
        primary_key=("valuation_ts", "portfolio_id", "contract_key"),
        layer="portfolio",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "risk_aggregates": TableSpec(
        name="risk_aggregates",
        contract=RiskAggregate,
        primary_key=("valuation_ts", "portfolio_id", "group_key"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "scenario_results": TableSpec(
        name="scenario_results",
        contract=ScenarioResult,
        primary_key=("valuation_ts", "portfolio_id", "scenario_id", "contract_key"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "qc_results": TableSpec(
        name="qc_results",
        contract=QcResult,
        primary_key=("run_id", "check_name", "target_key"),
        layer="qc",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "triage_records": TableSpec(
        name="triage_records",
        contract=TriageRecord,
        primary_key=("run_id", "source", "name", "target_key"),
        layer="qc",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
}


# Map each contract class back to its table name, for callers that hold an object
# and need its table without hard-coding the name.
_CONTRACT_TO_TABLE: dict[type, str] = {spec.contract: name for name, spec in REGISTRY.items()}


def spec_for_table(table: str) -> TableSpec:
    """Return the spec for a table name, or raise ``UnknownTableError``."""
    try:
        return REGISTRY[table]
    except KeyError:
        raise UnknownTableError(table) from None


def table_for_contract(contract: type) -> str:
    """Return the table name for a contract class, or raise ``UnknownTableError``."""
    try:
        return _CONTRACT_TO_TABLE[contract]
    except KeyError:
        raise UnknownTableError(contract.__name__) from None


def unwrap_optional(annotation: object) -> tuple[object, bool]:
    """Strip ``Optional``/``X | None`` to its inner type and an is-optional flag."""
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        is_optional = len(args) != len(get_args(annotation))
        if len(args) == 1:
            return args[0], is_optional
        return annotation, is_optional
    return annotation, False


def resolved_field_types(contract: type) -> dict[str, object]:
    """Return ``{field_name: resolved_type}`` for a contract's dataclass fields."""
    hints = get_type_hints(contract)
    return {field.name: hints[field.name] for field in dataclasses.fields(contract)}


def numeric_field_names(contract: type) -> tuple[str, ...]:
    """Names of fields typed as ``float`` or ``int`` (excluding ``bool``)."""
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner in (int, float):
            names.append(name)
    return tuple(names)


def datetime_field_names(contract: type) -> tuple[str, ...]:
    """Names of fields typed as ``datetime`` (timezone-aware-required fields)."""
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner is datetime:
            names.append(name)
    return tuple(names)


def date_field_names(contract: type) -> tuple[str, ...]:
    """Names of fields typed as ``date`` but not ``datetime``."""
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner is date:
            names.append(name)
    return tuple(names)
