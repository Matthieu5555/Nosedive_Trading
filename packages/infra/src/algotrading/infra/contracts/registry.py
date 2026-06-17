from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from .errors import UnknownTableError
from .tables import (
    Basket,
    BookGreeks,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
    DailyBar,
    DiscoveryCacheRow,
    ForwardCurvePoint,
    IndexConstituent,
    InstrumentMaster,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    ProjectedOptionAnalytics,
    QcResult,
    RawMarketEvent,
    ResidualObservation,
    RiskAggregate,
    RiskFreeRatePoint,
    ScenarioAttribution,
    ScenarioResult,
    StrategySignal,
    SurfaceGrid,
    SurfaceParameters,
    TriageRecord,
)


@dataclass(frozen=True, slots=True)
class TableSpec:

    name: str
    contract: type
    primary_key: tuple[str, ...]
    layer: str
    append_only: bool
    requires_provenance: bool
    requires_source_snapshot_ts: bool
    positive_fields: tuple[str, ...]
    non_negative_fields: tuple[str, ...]
    provider_partitioned: bool = False
    cold_compactable: bool = False


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
    "daily_bar": TableSpec(
        name="daily_bar",
        contract=DailyBar,
        primary_key=("provider", "underlying", "trade_date"),
        layer="raw",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=False,
        positive_fields=("open", "high", "low", "close"),
        non_negative_fields=("volume",),
        provider_partitioned=True,
        cold_compactable=True,
    ),
    "rates": TableSpec(
        name="rates",
        contract=RiskFreeRatePoint,
        primary_key=("currency", "pillar_tenor", "as_of"),
        layer="raw",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=("maturity_years",),
        non_negative_fields=(),
    ),
    "index_constituents": TableSpec(
        name="index_constituents",
        contract=IndexConstituent,
        primary_key=("index", "constituent", "effective_add_date", "knowledge_date"),
        layer="reference",
        append_only=True,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
        provider_partitioned=False,
    ),
    "discovery_conid_cache": TableSpec(
        name="discovery_conid_cache",
        contract=DiscoveryCacheRow,
        primary_key=("underlying", "as_of_date"),
        layer="reference",
        append_only=True,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
        provider_partitioned=False,
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
        positive_fields=("forward_price", "maturity_years"),
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
        non_negative_fields=("implied_vol", "total_variance"),
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
    "projected_option_analytics": TableSpec(
        name="projected_option_analytics",
        contract=ProjectedOptionAnalytics,
        primary_key=(
            "provider", "snapshot_ts", "underlying", "tenor_label", "delta_band", "surface_side",
        ),
        layer="analytics",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=("maturity_years", "strike", "forward_price"),
        non_negative_fields=("implied_vol", "total_variance", "gamma", "vega", "price"),
        provider_partitioned=True,
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
    "broker_positions": TableSpec(
        name="broker_positions",
        contract=BrokerPosition,
        primary_key=("as_of_ts", "account_id", "conid"),
        layer="portfolio",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "broker_cash_balances": TableSpec(
        name="broker_cash_balances",
        contract=BrokerCashBalance,
        primary_key=("as_of_ts", "account_id", "currency"),
        layer="portfolio",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "broker_fills": TableSpec(
        name="broker_fills",
        contract=BrokerFill,
        primary_key=("account_id", "execution_id"),
        layer="portfolio",
        append_only=True,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=("quantity", "price"),
    ),
    "baskets": TableSpec(
        name="baskets",
        contract=Basket,
        primary_key=("basket_id", "trade_date", "underlying"),
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
    "scenario_attributions": TableSpec(
        name="scenario_attributions",
        contract=ScenarioAttribution,
        primary_key=("valuation_ts", "portfolio_id", "scenario_id", "contract_key"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "residual_observations": TableSpec(
        name="residual_observations",
        contract=ResidualObservation,
        primary_key=("as_of_date", "portfolio_id", "level"),
        layer="derived",
        append_only=True,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "book_greeks": TableSpec(
        name="book_greeks",
        contract=BookGreeks,
        primary_key=("valuation_ts", "book_id", "level", "layer_label"),
        layer="derived",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
    ),
    "strategy_signals": TableSpec(
        name="strategy_signals",
        contract=StrategySignal,
        primary_key=("snapshot_ts", "provider", "signal_kind", "subject", "tenor_label"),
        layer="signals",
        append_only=False,
        requires_provenance=True,
        requires_source_snapshot_ts=True,
        positive_fields=(),
        non_negative_fields=(),
        provider_partitioned=True,
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
        primary_key=("run_id", "source", "name", "underlying", "target_key"),
        layer="qc",
        append_only=False,
        requires_provenance=False,
        requires_source_snapshot_ts=False,
        positive_fields=(),
        non_negative_fields=(),
    ),
}


_CONTRACT_TO_TABLE: dict[type, str] = {spec.contract: name for name, spec in REGISTRY.items()}


def spec_for_table(table: str) -> TableSpec:
    try:
        return REGISTRY[table]
    except KeyError:
        raise UnknownTableError(table) from None


def table_for_contract(contract: type) -> str:
    try:
        return _CONTRACT_TO_TABLE[contract]
    except KeyError:
        raise UnknownTableError(contract.__name__) from None


def unwrap_optional(annotation: object) -> tuple[object, bool]:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        is_optional = len(args) != len(get_args(annotation))
        if len(args) == 1:
            return args[0], is_optional
        return annotation, is_optional
    return annotation, False


def resolved_field_types(contract: type) -> dict[str, object]:
    hints = get_type_hints(contract)
    return {field.name: hints[field.name] for field in dataclasses.fields(contract)}


def numeric_field_names(contract: type) -> tuple[str, ...]:
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner in (int, float):
            names.append(name)
    return tuple(names)


def optional_numeric_field_names(contract: type) -> tuple[str, ...]:
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, is_optional = unwrap_optional(annotation)
        if is_optional and inner in (int, float):
            names.append(name)
    return tuple(names)


def datetime_field_names(contract: type) -> tuple[str, ...]:
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner is datetime:
            names.append(name)
    return tuple(names)


def date_field_names(contract: type) -> tuple[str, ...]:
    names: list[str] = []
    for name, annotation in resolved_field_types(contract).items():
        inner, _ = unwrap_optional(annotation)
        if inner is date:
            names.append(name)
    return tuple(names)
