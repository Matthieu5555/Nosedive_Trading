"""By-Greek PnL attribution (2C): decompose dPnL into Δ/Γ/Vega/Θ contributions + residual.

The third layer on top of the scenario engine: basket (2A) → reprice/scenario (2B) →
*explain* (here). For one position and one book under one scenario, split the local Taylor
PnL into its named, dollar, book-additive per-Greek contributions and report the
**residual** of their sum against the **full reprice** — the ADR-0006 oracle. The full
reprice is the truth; the Greek decomposition is the explanation; the residual is the
honest accuracy of that explanation, always reported (for a large shock the Taylor story is
*expected* to diverge, and that divergence is the headline number, not an error).

This module owns no term arithmetic of its own: the per-Greek split is :func:`taylor_terms`
in :mod:`scenarios` (the one home, shared with the lumped path), so the split can never
drift from the lumped Taylor. Here we add the per-line and per-book *records*, the
residual/verdict, and the projection into the frozen
:class:`~algotrading.infra.contracts.ScenarioAttribution` seam the BFF / 1I read.

The two decomposition-convention flags (``gamma_normalisation``, ``theta_day_count`` on
:class:`AttributionConfig`) are reporting normalisations on the split only — they move one
term and the residual, never the full reprice. See ADR 0038.
"""

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

# The sentinel ``contract_key`` a book-level record carries, so a book and a per-line record
# never collide in the ``(valuation_ts, portfolio_id, scenario_id, contract_key)`` key. The
# double-underscore form cannot collide with a real ``UNDERLYING|OPT|RIGHT|STRIKE`` key.
BOOK_CONTRACT_KEY = "__book__"

LEVEL_POSITION = "position"
LEVEL_BOOK = "book"


def _verdict(
    residual: float, full_reprice: float, terms: TaylorTerms, config: AttributionConfig
) -> tuple[bool, str]:
    """The residual acceptance verdict and a diagnostic label.

    Accepted when ``|residual| <= max(abs_tol, rel_tol*|full_reprice|)``. A non-finite full
    reprice or contribution is never silent agreement: it is rejected with a labeled
    diagnostic (mirroring :mod:`reconciliation`'s NaN guard), because ``nan <= tol`` is
    ``False`` but would read as "diverged" rather than "uncomputable" without the label.
    """
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
    """One line's by-Greek PnL decomposition under one scenario, with its residual.

    ``terms`` are the dollar per-Greek contributions (book-additive); ``approx_pnl`` is
    their lumped sum; ``full_reprice_pnl`` is the ADR-0006 oracle; ``residual`` is
    ``full_reprice_pnl - approx_pnl``. ``within_tolerance`` is the residual verdict and
    ``diagnostic`` labels a breach or a non-finite input (empty when clean and accepted).
    """

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
    """A book's by-Greek decomposition: the term-wise sum of its lines, plus the residual.

    ``terms`` are the lines' contributions summed term by term (the across-Greeks axis,
    orthogonal to the across-positions ``UnderlyingAttribution``/``FamilyAttribution``);
    ``full_reprice_pnl`` is the lines' full reprices summed; ``residual`` is
    ``full_reprice_pnl - approx_pnl`` and equals the summed per-line residuals
    (book-additivity). ``lines`` preserves the per-line breakdown, ordered by contract key.
    Aggregation uses ``math.fsum`` so the book is invariant under input-position reordering.
    """

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
    """Decompose one line's PnL under one scenario into named contributions + residual.

    ``full_reprice_pnl`` is the oracle (full reprice under the shocked state); the terms are
    the :func:`taylor_terms` split under ``config``; ``residual = full_reprice - approx``.
    """
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
    """Decompose a book's PnL term by term: the across-Greeks sum of its lines.

    Same-contract lots are netted first (:func:`net_lots`), so each contract is one line and
    the ordering is a pure function of the input *set*. Term-wise aggregation uses
    ``math.fsum`` for reorder-invariance. An empty book is zero terms, zero residual, and a
    clean verdict — a fact, not a crash. ``portfolio_id`` is taken from the netted lines
    (empty string for an empty book, where the seam caller supplies it explicitly).
    """
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
    # A book is only accepted when every line is too: one non-finite line must not be masked
    # by a finite book total it happens to sum into.
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
    """Project a decomposition into the frozen ``ScenarioAttribution`` contract."""
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
    """Project a per-line decomposition into the ``ScenarioAttribution`` seam (level=position).

    Mirrors :func:`scenarios.scenario_result`: the stamp is injected, never read from a
    clock here, so the projection is reproducible.
    """
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
    """Project a book decomposition into the ``ScenarioAttribution`` seam (level=book).

    The book sentinel rides in ``contract_key`` (:data:`BOOK_CONTRACT_KEY`). ``portfolio_id``
    falls back to the one carried on the book record; pass it explicitly for an empty book,
    which carries no lines to derive it from.
    """
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


# --------------------------------------------------------------------------- #
# Realized day-over-day attribution (TARGET §5.2)                             #
# --------------------------------------------------------------------------- #
# The 2C seam decomposes a *hypothetical* scenario shock. This decomposes the **realized**
# P&L of a held line between two daily snapshots: yesterday's start-of-day Greeks (t-1)
# explain the move into today (t), and the residual is measured against the full reprice
# — exactly the same honesty meter, now over an observed move instead of a shock.
#
# **Look-ahead discipline (the §6 quant-guard bar).** The attribution of day t uses only
# the Greeks known at the *start* of the day (carried on the start-of-day line) and the
# realized move t-1 -> t; it never reads today's Greeks, and the function reads no clock.
# The oracle is the honest reprice ``price(today) - price(yesterday)`` of the *held*
# (start-of-day) line — a fill that changes the position is trade P&L, a separate axis,
# not market attribution, so the scale is held at the start-of-day holding.
#
# The named terms run **through Volga** (Δ/Γ/Vega/Θ/Rho/Vanna/Volga — TARGET §2.5); what
# the deterministic Greeks cannot name stays in the residual, which is itself the next
# signal (§5.2's "residual diagnosis"). Charm is *not* an attribution term: it is a risk
# display Greek; the time decay of value is carried by Θ.


class RealizedAttributionError(Exception):
    """A realized attribution input was malformed (mismatched contract, or missing end state)."""

    def __init__(self, contract_key: str, reason: str) -> None:
        self.contract_key = contract_key
        self.reason = reason
        super().__init__(f"realized attribution for {contract_key!r}: {reason}")


@dataclass(frozen=True, slots=True)
class RealizedMove:
    """The realized t-1 -> t market move of one contract, in absolute units.

    ``d_spot`` is a spot change, ``d_vol`` an absolute vol change, ``d_rate`` an absolute
    (decimal) change in the implied rate, and ``d_time`` the calendar-time roll in years
    (``start_T - end_T``, positive as time passes) — the same sign as the scenario
    ``time_shock`` so :func:`~algotrading.infra.risk.scenarios.terms_from_move` treats a
    day's roll-down as a loss of time value, matching the full reprice.
    """

    d_spot: float
    d_vol: float
    d_time: float
    d_rate: float

    @classmethod
    def between(
        cls, start: ContractValuationInput, end: ContractValuationInput
    ) -> RealizedMove:
        """The move from a start-of-day valuation to the end-of-day valuation of one contract."""
        return cls(
            d_spot=end.spot - start.spot,
            d_vol=end.volatility - start.volatility,
            d_time=start.maturity_years - end.maturity_years,
            d_rate=end.implied_rate - start.implied_rate,
        )


@dataclass(frozen=True, slots=True)
class RealizedLineAttribution:
    """One held line's realized day-over-day PnL decomposition, with its residual.

    ``terms`` are the dollar per-Greek contributions of the realized move evaluated at the
    *start-of-day* Greeks; ``full_reprice_pnl`` is the realized oracle
    ``(price(end) - price(start)) * scale``; ``residual = full_reprice_pnl - approx_pnl``.
    ``within_tolerance``/``diagnostic`` are the same residual verdict as the scenario path.
    """

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
    """A book's realized decomposition: the term-wise sum of its lines, plus the residual.

    Mirrors :class:`BookAttribution` on the realized axis: ``terms`` is the lines'
    contributions summed term by term (``math.fsum``, so reorder-invariant),
    ``full_reprice_pnl`` the summed realized reprices, ``residual`` their difference and
    equal to the summed per-line residuals. A book is accepted only when every line is.
    """

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
    """Decompose one held line's realized day-over-day PnL into named terms + residual.

    ``start`` is the start-of-day line (its Greeks are the t-1 Greeks the move is read
    against — the look-ahead anchor); ``end`` is the same contract's end-of-day market
    state. The move (:class:`RealizedMove`) and the start-of-day Greeks feed the one
    arithmetic home :func:`~algotrading.infra.risk.scenarios.terms_from_move`, and the
    residual is taken against the full reprice oracle. Raises
    :class:`RealizedAttributionError` if ``end`` is a different contract than ``start`` —
    a mis-join, not something to silently attribute across instruments.
    """
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
    """Decompose a book's realized day-over-day PnL: the term-wise sum of its lines.

    ``starts`` are the start-of-day lines (netted per contract, exactly as the scenario
    book does, so the ordering is a pure function of the input *set*); ``ends`` maps each
    contract key to its end-of-day market state. Every netted contract must have an end
    state — a missing one is a :class:`RealizedAttributionError`, never a silently dropped
    line. An empty book is zero terms, zero residual, and a clean verdict.
    """
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
