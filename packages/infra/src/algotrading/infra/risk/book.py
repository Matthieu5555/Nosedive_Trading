"""Strategy composition — a *book* over ordered sub-strategy layers (WS 2D).

A **book** is an operator's named, ordered set of sub-strategies (each a 2A position set / basket)
layered into one. This module builds the two combined views the spec asks for, both **pure** and
both layered on the already-built reducers — no second aggregation home, no second $-Greek home:

* :func:`build_book_greeks` — the combined net Greeks (decimal **and** dollar, unit-tagged) plus the
  per-layer breakdown, as flat :class:`~algotrading.infra.contracts.BookGreeks` rows. The combined
  row is the **additive sum** of the layer rows (ADR 0006), provably equal to the flat aggregate
  over the union of all positions — so the book is a *view* that layers and sums, never a re-solve.
* :func:`book_stress_surface` — the combined stressed PnL surface, the **full reprice** of the union
  of all layers' positions over the same 2B spot×vol grid (:func:`stress_surface`), additive across
  layers at every node.

The substrate is the canonical :class:`~.greeks.PositionRisk` line: decimal nets come from
:func:`~.aggregation.aggregate_by_desk` (the existing reducer, netting the whole set into one
group), dollars from :func:`~.pricing.dollar_greeks` per line summed (book-additive, each at
its own spot). "Decorrelated" is the operator's intent, not a computation here: 2D composes and sums
exactly what is selected — it never reweights, drops, or reorders to reduce correlation.
"""

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

# Brands the composition conventions stamped onto every book row's ``config_hashes`` provenance and
# carried in ``composition_version``. Bump on any deliberate change to how a book is composed.
COMPOSITION_VERSION = "composition-1.0.0"

# The combined-row sentinels — a book row's label/index, kept distinct from any operator layer label
# so the combined row never collides with a per-layer row in the (valuation_ts, book_id, level,
# layer_label) primary key (mirrors 2C's book-sentinel pattern).
_BOOK_LABEL = "__book__"
_BOOK_INDEX = -1


@dataclass(frozen=True, slots=True)
class BookLayerInput:
    """One layer of a book: an operator label and the sub-strategy's resolved risk lines.

    ``lines`` are already-priced :class:`PositionRisk` lines (the actor resolves a 2A basket to
    these upstream); the book layers and sums them without re-solving — a layer's own numbers are
    identical inside and outside any book.
    """

    label: str
    lines: tuple[PositionRisk, ...]


@dataclass(frozen=True, slots=True)
class _NetGreeks:
    """Internal: the four decimal nets + the five dollar sums + their unit strings for one row."""

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
    """Net the whole line set into one (delta, gamma, vega, theta) — via the existing reducer.

    Uses :func:`aggregate_by_desk` with an empty desk map so every line lands in the single
    ``desk:unassigned`` group (the one-group net over the set); an empty set nets to zeros. Same-
    contract lots collapse first (``net_lots``), so additivity holds across any layer partition.
    """
    groups = aggregate_by_desk(lines, portfolio_id=book_id, desk_of={})
    if not groups:
        return (0.0, 0.0, 0.0, 0.0)
    net = groups[0]
    return (net.net_delta, net.net_gamma, net.net_vega, net.net_theta)


def _net_dollar(
    lines: Sequence[PositionRisk], *, monetization: MonetizationConfig
) -> tuple[float, float, float, float, float]:
    """Book-additive dollar Greeks: monetize each line at its own spot, then ``math.fsum``.

    Each line's dollar Greeks come from the single canonical home
    (:func:`pricing.dollar_greeks`, per-1% gamma / per-365 theta by config) — not a second copy —
    and the book is their additive sum (the ADR-0006 book-additive property).
    """
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
    """Compose the book: one ``BookGreeks`` row per layer (in order) + the combined ``"book"`` row.

    The combined row's net Greeks are the aggregate over the **union** of every layer's lines; by
    additivity (ADR 0006) this equals the sum of the per-layer rows (the test asserts the identity
    exactly). Pure: no store, no clock, no config read — ``valuation_ts``/``provenance`` injected.
    An empty book (no layers) returns a single zero-valued combined row, labeled — never a crash.
    """
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
    """The combined stressed PnL surface — the full reprice of the union over the 2B grid.

    Reuses :func:`stress_surface` (the explicit-state full reprice, ADR 0006 — never a Greek-
    multiplier Taylor shortcut) over the union of every layer's lines. PnL is additive across layers
    at every node, so this equals the node-wise sum of the per-layer surfaces (the test asserts it).
    An empty book reprices to a flat-zero surface over the configured axes.
    """
    union = tuple(line for layer in layers for line in layer.lines)
    return stress_surface(union, config, steps=steps)
