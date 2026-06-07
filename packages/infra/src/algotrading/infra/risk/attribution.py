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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import ScenarioAttribution

from .config import AttributionConfig
from .greeks import PositionRisk, net_lots
from .scenarios import Scenario, TaylorTerms, full_reprice_pnl, taylor_terms

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
        for value in (terms.delta_pnl, terms.gamma_pnl, terms.vega_pnl, terms.theta_pnl)
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
