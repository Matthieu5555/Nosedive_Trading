from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.config import MonetizationConfig, ScenarioConfig
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import BookGreeks
from algotrading.infra.pricing import dollar_greeks
from algotrading.infra.pricing.dollar_greeks import UNIT_STRINGS

from .aggregation import aggregate_by_desk
from .greeks import PositionRisk
from .stress_surface import StressSurface, stress_surface

COMPOSITION_VERSION = "composition-1.0.0"

_BOOK_LABEL = "__book__"
_BOOK_INDEX = -1


@dataclass(frozen=True, slots=True)
class BookLayerInput:

    label: str
    lines: tuple[PositionRisk, ...]


@dataclass(frozen=True, slots=True)
class _NetGreeks:

    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    dollar_delta: float
    dollar_gamma: float
    dollar_vega: float
    dollar_theta: float
    dollar_rho: float


def _net_decimal(
    lines: Sequence[PositionRisk], *, book_id: str
) -> tuple[float, float, float, float]:
    groups = aggregate_by_desk(lines, portfolio_id=book_id, desk_of={})
    if not groups:
        return (0.0, 0.0, 0.0, 0.0)
    net = groups[0]
    return (net.net_delta, net.net_gamma, net.net_vega, net.net_theta)


def _net_dollar(
    lines: Sequence[PositionRisk], *, monetization: MonetizationConfig
) -> tuple[float, float, float, float, float]:
    monetized = [
        dollar_greeks(
            delta=line.greeks.delta,
            gamma=line.greeks.gamma,
            vega=line.greeks.vega,
            theta=line.greeks.theta,
            rho=line.greeks.rho,
            spot=line.valuation.spot,
            multiplier=line.valuation.multiplier,
            quantity=line.quantity,
            config=monetization,
        )
        for line in lines
    ]
    return (
        math.fsum(m.dollar_delta for m in monetized),
        math.fsum(m.dollar_gamma for m in monetized),
        math.fsum(m.dollar_vega for m in monetized),
        math.fsum(m.dollar_theta for m in monetized),
        math.fsum(m.dollar_rho for m in monetized),
    )


def _net_greeks(
    lines: Sequence[PositionRisk], *, book_id: str, monetization: MonetizationConfig
) -> _NetGreeks:
    net_delta, net_gamma, net_vega, net_theta = _net_decimal(lines, book_id=book_id)
    dollar_delta, dollar_gamma, dollar_vega, dollar_theta, dollar_rho = _net_dollar(
        lines, monetization=monetization
    )
    return _NetGreeks(
        net_delta=net_delta,
        net_gamma=net_gamma,
        net_vega=net_vega,
        net_theta=net_theta,
        dollar_delta=dollar_delta,
        dollar_gamma=dollar_gamma,
        dollar_vega=dollar_vega,
        dollar_theta=dollar_theta,
        dollar_rho=dollar_rho,
    )


def _row(
    *,
    book_id: str,
    level: str,
    layer_label: str,
    layer_index: int,
    greeks: _NetGreeks,
    monetization: MonetizationConfig,
    valuation_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> BookGreeks:
    return BookGreeks(
        valuation_ts=valuation_ts,
        book_id=book_id,
        level=level,
        layer_label=layer_label,
        layer_index=layer_index,
        net_delta=greeks.net_delta,
        net_gamma=greeks.net_gamma,
        net_vega=greeks.net_vega,
        net_theta=greeks.net_theta,
        dollar_delta=greeks.dollar_delta,
        dollar_gamma=greeks.dollar_gamma,
        dollar_vega=greeks.dollar_vega,
        dollar_theta=greeks.dollar_theta,
        dollar_rho=greeks.dollar_rho,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS[f"dollar_gamma_{monetization.gamma_normalisation}"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        dollar_theta_unit=UNIT_STRINGS[f"dollar_theta_{monetization.theta_day_count}"],
        dollar_rho_unit=UNIT_STRINGS["dollar_rho"],
        composition_version=COMPOSITION_VERSION,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )


def build_book_greeks(
    *,
    book_id: str,
    layers: Sequence[BookLayerInput],
    monetization: MonetizationConfig,
    valuation_ts: datetime,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> tuple[BookGreeks, ...]:
    rows = [
        _row(
            book_id=book_id,
            level="layer",
            layer_label=layer.label,
            layer_index=index,
            greeks=_net_greeks(layer.lines, book_id=book_id, monetization=monetization),
            monetization=monetization,
            valuation_ts=valuation_ts,
            source_snapshot_ts=source_snapshot_ts,
            provenance=provenance,
        )
        for index, layer in enumerate(layers)
    ]
    union = tuple(line for layer in layers for line in layer.lines)
    rows.append(
        _row(
            book_id=book_id,
            level="book",
            layer_label=_BOOK_LABEL,
            layer_index=_BOOK_INDEX,
            greeks=_net_greeks(union, book_id=book_id, monetization=monetization),
            monetization=monetization,
            valuation_ts=valuation_ts,
            source_snapshot_ts=source_snapshot_ts,
            provenance=provenance,
        )
    )
    return tuple(rows)


def book_stress_surface(
    layers: Sequence[BookLayerInput], *, config: ScenarioConfig, steps: int | None = None
) -> StressSurface:
    union = tuple(line for layer in layers for line in layer.lines)
    return stress_surface(union, config, steps=steps)
