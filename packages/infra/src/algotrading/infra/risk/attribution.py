from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import ScenarioAttribution
from algotrading.infra.pricing import price

from .config import AttributionConfig
from .greeks import PositionRisk, net_lots
from .scenarios import Scenario, TaylorTerms, full_reprice_pnl, taylor_terms, terms_from_move
from .valuation import ContractValuationInput, pricing_state_for

BOOK_CONTRACT_KEY = "__book__"

LEVEL_POSITION = "position"
LEVEL_BOOK = "book"


def _verdict(
    residual: float, full_reprice: float, terms: TaylorTerms, config: AttributionConfig
) -> tuple[bool, str]:
    finite_terms = all(
        math.isfinite(value)
        for value in (
            terms.delta_pnl,
            terms.gamma_pnl,
            terms.vega_pnl,
            terms.theta_pnl,
            terms.rho_pnl,
            terms.vanna_pnl,
            terms.volga_pnl,
        )
    )
    if not (finite_terms and math.isfinite(full_reprice) and math.isfinite(residual)):
        return False, "non-finite full reprice or contribution — attribution uncomputable"
    bound = max(config.residual_abs_tol, config.residual_rel_tol * abs(full_reprice))
    if abs(residual) <= bound:
        return True, ""
    return False, f"residual {residual:.6g} exceeds tolerance {bound:.6g}"


@dataclass(frozen=True, slots=True)
class LineAttribution:

    scenario: Scenario
    line: PositionRisk
    terms: TaylorTerms
    full_reprice_pnl: float
    residual: float
    within_tolerance: bool
    diagnostic: str
    config: AttributionConfig

    @property
    def approx_pnl(self) -> float:
        return self.terms.total

    @property
    def contract_key(self) -> str:
        return self.line.contract_key

    @property
    def portfolio_id(self) -> str:
        return self.line.portfolio_id


@dataclass(frozen=True, slots=True)
class BookAttribution:

    scenario: Scenario
    portfolio_id: str
    terms: TaylorTerms
    full_reprice_pnl: float
    residual: float
    within_tolerance: bool
    diagnostic: str
    lines: tuple[LineAttribution, ...]
    config: AttributionConfig

    @property
    def approx_pnl(self) -> float:
        return self.terms.total


def attribute_line(
    line: PositionRisk,
    scenario: Scenario,
    config: AttributionConfig,
    *,
    steps: int | None = None,
) -> LineAttribution:
    terms = taylor_terms(
        line.greeks, spot=line.valuation.spot, scale=line.scale, scenario=scenario, config=config
    )
    full_reprice = full_reprice_pnl(line, scenario, steps=steps)
    residual = full_reprice - terms.total
    within_tolerance, diagnostic = _verdict(residual, full_reprice, terms, config)
    return LineAttribution(
        scenario=scenario,
        line=line,
        terms=terms,
        full_reprice_pnl=full_reprice,
        residual=residual,
        within_tolerance=within_tolerance,
        diagnostic=diagnostic,
        config=config,
    )


def attribute_book(
    lines: Iterable[PositionRisk],
    scenario: Scenario,
    config: AttributionConfig,
    *,
    steps: int | None = None,
) -> BookAttribution:
    netted = net_lots(lines)
    attributions = tuple(attribute_line(line, scenario, config, steps=steps) for line in netted)
    portfolio_id = netted[0].portfolio_id if netted else ""
    book_terms = TaylorTerms(
        delta_pnl=math.fsum(a.terms.delta_pnl for a in attributions),
        gamma_pnl=math.fsum(a.terms.gamma_pnl for a in attributions),
        vega_pnl=math.fsum(a.terms.vega_pnl for a in attributions),
        theta_pnl=math.fsum(a.terms.theta_pnl for a in attributions),
        rho_pnl=math.fsum(a.terms.rho_pnl for a in attributions),
        vanna_pnl=math.fsum(a.terms.vanna_pnl for a in attributions),
        volga_pnl=math.fsum(a.terms.volga_pnl for a in attributions),
    )
    full_reprice = math.fsum(a.full_reprice_pnl for a in attributions)
    residual = full_reprice - book_terms.total
    within_tolerance, diagnostic = _verdict(residual, full_reprice, book_terms, config)
    if any(not a.within_tolerance for a in attributions):
        within_tolerance = False
        if not diagnostic:
            diagnostic = "one or more lines breached tolerance or were non-finite"
    return BookAttribution(
        scenario=scenario,
        portfolio_id=portfolio_id,
        terms=book_terms,
        full_reprice_pnl=full_reprice,
        residual=residual,
        within_tolerance=within_tolerance,
        diagnostic=diagnostic,
        lines=attributions,
        config=config,
    )


def _attribution_result(
    *,
    level: str,
    portfolio_id: str,
    contract_key: str,
    scenario: Scenario,
    terms: TaylorTerms,
    full_reprice_pnl: float,
    residual: float,
    within_tolerance: bool,
    config: AttributionConfig,
    valuation_ts: datetime,
    scenario_version: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ScenarioAttribution:
    return ScenarioAttribution(
        valuation_ts=valuation_ts,
        portfolio_id=portfolio_id,
        scenario_id=scenario.scenario_id,
        contract_key=contract_key,
        level=level,
        spot_shock=scenario.spot_shock,
        vol_shock=scenario.vol_shock,
        time_shock=scenario.time_shock,
        delta_pnl=terms.delta_pnl,
        gamma_pnl=terms.gamma_pnl,
        vega_pnl=terms.vega_pnl,
        theta_pnl=terms.theta_pnl,
        rho_pnl=terms.rho_pnl,
        vanna_pnl=terms.vanna_pnl,
        volga_pnl=terms.volga_pnl,
        approx_pnl=terms.total,
        full_reprice_pnl=full_reprice_pnl,
        residual=residual,
        within_tolerance=within_tolerance,
        residual_abs_tol=config.residual_abs_tol,
        residual_rel_tol=config.residual_rel_tol,
        scenario_version=scenario_version,
        attribution_version=config.version,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )


def line_attribution_result(
    attribution: LineAttribution,
    *,
    valuation_ts: datetime,
    scenario_version: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ScenarioAttribution:
    return _attribution_result(
        level=LEVEL_POSITION,
        portfolio_id=attribution.portfolio_id,
        contract_key=attribution.contract_key,
        scenario=attribution.scenario,
        terms=attribution.terms,
        full_reprice_pnl=attribution.full_reprice_pnl,
        residual=attribution.residual,
        within_tolerance=attribution.within_tolerance,
        config=attribution.config,
        valuation_ts=valuation_ts,
        scenario_version=scenario_version,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )


def book_attribution_result(
    attribution: BookAttribution,
    *,
    valuation_ts: datetime,
    scenario_version: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
    portfolio_id: str | None = None,
) -> ScenarioAttribution:
    pid = portfolio_id if portfolio_id is not None else attribution.portfolio_id
    return _attribution_result(
        level=LEVEL_BOOK,
        portfolio_id=pid,
        contract_key=BOOK_CONTRACT_KEY,
        scenario=attribution.scenario,
        terms=attribution.terms,
        full_reprice_pnl=attribution.full_reprice_pnl,
        residual=attribution.residual,
        within_tolerance=attribution.within_tolerance,
        config=attribution.config,
        valuation_ts=valuation_ts,
        scenario_version=scenario_version,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )


class RealizedAttributionError(Exception):

    def __init__(self, contract_key: str, reason: str) -> None:
        self.contract_key = contract_key
        self.reason = reason
        super().__init__(f"realized attribution for {contract_key!r}: {reason}")


@dataclass(frozen=True, slots=True)
class RealizedMove:

    d_spot: float
    d_vol: float
    d_time: float
    d_rate: float

    @classmethod
    def between(
        cls, start: ContractValuationInput, end: ContractValuationInput
    ) -> RealizedMove:
        return cls(
            d_spot=end.spot - start.spot,
            d_vol=end.volatility - start.volatility,
            d_time=start.maturity_years - end.maturity_years,
            d_rate=end.implied_rate - start.implied_rate,
        )


@dataclass(frozen=True, slots=True)
class RealizedLineAttribution:

    start: PositionRisk
    end: ContractValuationInput
    move: RealizedMove
    terms: TaylorTerms
    full_reprice_pnl: float
    residual: float
    within_tolerance: bool
    diagnostic: str
    config: AttributionConfig

    @property
    def approx_pnl(self) -> float:
        return self.terms.total

    @property
    def contract_key(self) -> str:
        return self.start.contract_key

    @property
    def portfolio_id(self) -> str:
        return self.start.portfolio_id


@dataclass(frozen=True, slots=True)
class RealizedBookAttribution:

    portfolio_id: str
    terms: TaylorTerms
    full_reprice_pnl: float
    residual: float
    within_tolerance: bool
    diagnostic: str
    lines: tuple[RealizedLineAttribution, ...]
    config: AttributionConfig

    @property
    def approx_pnl(self) -> float:
        return self.terms.total


def attribute_realized_line(
    start: PositionRisk,
    end: ContractValuationInput,
    config: AttributionConfig,
    *,
    steps: int | None = None,
) -> RealizedLineAttribution:
    if start.contract_key != end.contract_key:
        raise RealizedAttributionError(
            start.contract_key, f"end-of-day state is for a different contract {end.contract_key!r}"
        )
    move = RealizedMove.between(start.valuation, end)
    end_state = pricing_state_for(end)
    end_price = (price(end_state, steps=steps) if steps is not None else price(end_state)).price
    full_reprice = (end_price - start.greeks.price) * start.scale
    terms = terms_from_move(
        start.greeks,
        scale=start.scale,
        d_spot=move.d_spot,
        d_vol=move.d_vol,
        d_time=move.d_time,
        d_rate=move.d_rate,
        config=config,
    )
    residual = full_reprice - terms.total
    within_tolerance, diagnostic = _verdict(residual, full_reprice, terms, config)
    return RealizedLineAttribution(
        start=start,
        end=end,
        move=move,
        terms=terms,
        full_reprice_pnl=full_reprice,
        residual=residual,
        within_tolerance=within_tolerance,
        diagnostic=diagnostic,
        config=config,
    )


def attribute_realized_book(
    starts: Iterable[PositionRisk],
    ends: Mapping[str, ContractValuationInput],
    config: AttributionConfig,
    *,
    steps: int | None = None,
) -> RealizedBookAttribution:
    netted = net_lots(starts)
    attributions: list[RealizedLineAttribution] = []
    for line in netted:
        end = ends.get(line.contract_key)
        if end is None:
            raise RealizedAttributionError(line.contract_key, "no end-of-day state supplied")
        attributions.append(attribute_realized_line(line, end, config, steps=steps))
    portfolio_id = netted[0].portfolio_id if netted else ""
    book_terms = TaylorTerms(
        delta_pnl=math.fsum(a.terms.delta_pnl for a in attributions),
        gamma_pnl=math.fsum(a.terms.gamma_pnl for a in attributions),
        vega_pnl=math.fsum(a.terms.vega_pnl for a in attributions),
        theta_pnl=math.fsum(a.terms.theta_pnl for a in attributions),
        rho_pnl=math.fsum(a.terms.rho_pnl for a in attributions),
        vanna_pnl=math.fsum(a.terms.vanna_pnl for a in attributions),
        volga_pnl=math.fsum(a.terms.volga_pnl for a in attributions),
    )
    full_reprice = math.fsum(a.full_reprice_pnl for a in attributions)
    residual = full_reprice - book_terms.total
    within_tolerance, diagnostic = _verdict(residual, full_reprice, book_terms, config)
    if any(not a.within_tolerance for a in attributions):
        within_tolerance = False
        if not diagnostic:
            diagnostic = "one or more lines breached tolerance or were non-finite"
    return RealizedBookAttribution(
        portfolio_id=portfolio_id,
        terms=book_terms,
        full_reprice_pnl=full_reprice,
        residual=residual,
        within_tolerance=within_tolerance,
        diagnostic=diagnostic,
        lines=tuple(attributions),
        config=config,
    )
