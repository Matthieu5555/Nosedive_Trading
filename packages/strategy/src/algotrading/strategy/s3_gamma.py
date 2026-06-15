"""S3 — the gamma-trading strategy object (TARGET §3 S3, course p.107–108).

S3 harvests the **gamma premium** on *one* cheap name: when a single name's implied vol is
low (cheap) and realized vol comes in higher, a delta-neutral **long-gamma** structure scalps
the difference — each delta-band round trip banks the rectangle realized vol carves out, and
the position pays theta to hold it. This module is the strategy *object* that encodes that rule
— not the infra it stands on (the per-name IV-rank signal, the analytics grid, basket risk are
all built and injected); it assembles them into the four things a
:class:`~algotrading.strategy.StrategyContract` names: the premium, the entry signal, the
intended Greeks, and the kill condition.

**Construction (v1).** On the **single cheapest name** (lowest IV rank, resolved as-of — never
a hand-set ticker): a **long ATM call** plus a **short stock leg sized to flatten the call's
net dollar delta** (Δ=0). The call is the long-gamma engine; the short stock is the linear
delta hedge. The course's symmetric alternative — long put + long stock — is the same
long-gamma/Δ=0 structure with the wings swapped; v1 builds the call form and leaves the put
form as a documented, deferred mirror (it changes no rule, only which wing carries the option).

**The scalp cycle (p.108).** As the underlying moves the call's delta drifts, so net delta
leaves zero; :meth:`GammaStrategy.rebalance` re-hedges the stock leg **in delta bands** via the
shared :func:`~algotrading.strategy.delta_hedge_band.decide_delta_hedge` rule (course req #9) —
sell stock as delta rises, buy it back lower, each round trip banking the rectangle — rather
than pinning delta continuously and bleeding the spread. S3 is the second consumer of that
shared band rule (S1 hedges a synthetic forward; S3 hedges with stock).

**The pure / I/O split.** ``GammaStrategy`` is a pure function of its injected ``GammaConfig``
and ``GammaMarketData`` — no store, no clock, no live read in any method (the §6 invariant:
research == backtest == paper == live). The as-of store reads (the cheapest name from the
banked IV-rank signal, the call's grid dollar-delta, the name's spot for hedge sizing) live
behind the :class:`GammaMarketData` protocol; the store-backed adapter that satisfies it for
paper/live is :mod:`algotrading.strategy.gamma_data`.

**Shared failure mode with S1.** S3 and S1 both die on *low realized vol* (the §3 overlap held
on purpose so the book view must surface it). S3's kill is the position-side proxy for "quiet
drift + IV crush": the long-gamma engine's **net dollar gamma collapsing** — once gamma is
gone the structure no longer scalps and only bleeds theta.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from algotrading.infra.contracts import Basket, BasketLeg

from .contract import GreekSign, IntendedGreeks, SignalKind, StrategyContract
from .delta_hedge_band import DeltaHedgeBand, decide_delta_hedge
from .signals import SignalSnapshot
from .strategy import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
    MarketState,
    RebalanceDecision,
)

# --- Grid-contract invariants (not business config) ---------------------------------------
# The ATM call pillar the WS-1F grid emits at the ATM-forward strike (see infra
# ``surfaces/projection.py``). The long-gamma engine is the long call on this pillar, read off
# the call wing (ADR 0048). A coordinate into the analytics grid, fixed by the grid contract —
# not a tunable, so it lives here, not in YAML.
_ATM_CALL_BAND = "atm"
# The wing the call leg's IV is read from (ADR 0048): the call leg off the call surface.
# ``"call"`` is the grid's ``surface_side`` value (``SURFACE_SIDES``).
_SURFACE_CALL = "call"


class GammaConstructionError(ValueError):
    """S3 could not build a delta-flat long-gamma structure, carrying the failing reason.

    Raised (never silently degraded) when ``construct`` cannot honour its contract: no cheap
    name resolves as of the date, or the grid/spot cannot supply the dollar-deltas the stock
    hedge sizing needs. A partial structure that silently skipped the delta-flattening stock leg
    would misrepresent its own risk (a naked long call is directional, not the intended
    delta-neutral gamma scalp), so S3 refuses rather than emit one.
    """

    def __init__(self, as_of: date, reason: str) -> None:
        self.as_of = as_of
        self.reason = reason
        super().__init__(f"S3 gamma construct failed as of {as_of}: {reason}")


@dataclass(frozen=True, slots=True)
class GammaConfig:
    """The economic parameters of an S3 book — injected, never ``.py`` literals (ADR 0028).

    In production these come from the typed platform config: ``index`` from the ``universe``
    bundle (the book context the per-name signals are filed under), ``option_tenor`` from the
    tenor grid, the entry threshold / band from the strategy config. Held here as one frozen
    record so the strategy object stays a pure function of it.

    * ``index`` — the index whose constituents are the candidate universe and whose signal
      partition the per-name IV-rank readings live under (SX5E). S3 trades **one** of its names.
    * ``option_tenor`` — the tenor-grid label the long call sits on.
    * ``entry_iv_rank_max`` — the IV rank at or **below** which a name's vol is "cheap" and
      entry fires. The course ranking: the best entry is *low IV expected to rise*, so a low
      rank is the trigger (the opposite sense to S1's "ρ̄ rich → high is the trigger").
    * ``contracts`` — the long-call size (a positive quantity; the long-gamma engine).
    * ``exit_gamma_floor`` — net dollar-gamma at or below which the long-gamma thesis is judged
      gone and :meth:`GammaStrategy.decide_exit` fires the kill (default ``0.0``).
    * ``delta_band`` — the absolute net dollar-delta band outside which
      :meth:`GammaStrategy.rebalance` emits a stock re-hedge (the p.108 scalp band).
    * ``min_hedge_units`` — stock-hedge sizes below this magnitude are treated as "the call is
      already delta-flat" and the stock leg is omitted (a leg must carry a non-zero quantity,
      and a negligible hedge is noise, not signal).
    """

    index: str
    option_tenor: str
    entry_iv_rank_max: float
    contracts: float = 1.0
    exit_gamma_floor: float = 0.0
    delta_band: float = 0.0
    min_hedge_units: float = 1e-6

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("GammaConfig.index must be non-empty")
        if not self.option_tenor.strip():
            raise ValueError("GammaConfig.option_tenor must be non-empty")
        if self.contracts <= 0:
            raise ValueError(
                f"GammaConfig.contracts must be positive, got {self.contracts}"
            )
        if self.delta_band < 0:
            raise ValueError(
                f"GammaConfig.delta_band must be non-negative, got {self.delta_band}"
            )
        if self.min_hedge_units < 0:
            raise ValueError(
                f"GammaConfig.min_hedge_units must be non-negative, got {self.min_hedge_units}"
            )


@runtime_checkable
class GammaMarketData(Protocol):
    """The as-of data S3 reads to construct — the I/O seam behind the pure strategy.

    Every method is parameterised by ``as_of`` (the look-ahead anchor): an implementor reads
    only data available as of that date. The store-backed implementor lives in
    :mod:`algotrading.strategy.gamma_data`; tests inject a hand-built fake. Keeping these three
    reads behind a protocol is what lets ``GammaStrategy`` stay a pure function and still size a
    real, grid-and-spot-derived stock hedge on the as-of-cheapest name.
    """

    def cheapest_name(self, as_of: date) -> str | None:
        """The single cheapest-vol constituent (lowest banked IV rank) as of ``as_of``.

        ``None`` is a labelled "no per-name IV-rank reading was banked for the index this day" —
        S3 turns it into a :class:`GammaConstructionError` rather than guessing a name to trade.
        """
        ...

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        """Net dollar delta of ``legs`` off the as-of grid, or ``None`` if any cannot price.

        ``None`` is a labelled "the grid could not supply every leg's delta" — S3 turns it into
        a :class:`GammaConstructionError` rather than sizing a hedge against a wrong total.
        """
        ...

    def share_unit_dollar_delta(self, name: str, as_of: date) -> float | None:
        """Dollar delta of **one long share** of ``name`` (≈ its spot), as of ``as_of``.

        The stock hedge's per-unit delta: a share's linear spot delta is ``1 × spot``, so this
        is the name's as-of reference spot. ``None`` when no spot resolves; ``0.0`` (a
        degenerate, un-invertible unit) is treated by the caller as un-sizable.
        """
        ...


def _leg_side(quantity: float) -> str:
    """The ``BasketLeg`` side string that agrees with a signed quantity ("long" / "short")."""
    return "long" if quantity > 0 else "short"


@dataclass(frozen=True, slots=True)
class GammaStrategy:
    """The S3 gamma-trading strategy object — pure over its ``config`` and injected ``data``.

    Implements the :class:`~algotrading.strategy.Strategy` protocol structurally. Entry reads
    per-name IV rank from the injected :class:`~algotrading.strategy.SignalSnapshot` and fires
    on the cheapest name being cheap enough (it does not compute IV rank — the infra signal
    layer does); construction resolves the cheapest name and sizes the stock hedge off the
    injected :class:`GammaMarketData`; exit and rebalance read the held
    :class:`~algotrading.infra.risk.greeks.PositionRisk` lines on the injected
    :class:`~algotrading.strategy.MarketState`. No method touches a clock, a store, or a live
    feed directly, so the same instance fed the same state returns the same step in all four
    contexts (§6).
    """

    config: GammaConfig
    data: GammaMarketData

    @property
    def contract(self) -> StrategyContract:
        """S3's frozen §3 contract: gamma premium, IV-rank signal, long-gamma/flat-delta, kill."""
        return StrategyContract(
            strategy_id="S3-gamma",
            premium_harvested=(
                "gamma premium: realized vol exceeds implied on one cheap name; a long-gamma "
                "delta-neutral structure scalps the difference (each delta-band round trip "
                "banks the realized-vol rectangle)"
            ),
            signal=SignalKind.IV_RANK,
            intended_greeks=IntendedGreeks(
                # Long gamma/vega on the cheap call, net delta flattened by the short stock, and
                # the long-gamma book pays theta — the profile attribution checks P&L against.
                delta=GreekSign.FLAT,
                gamma=GreekSign.LONG,
                vega=GreekSign.LONG,
                theta=GreekSign.SHORT,
            ),
            kill_condition=(
                "quiet drift + IV crush: realized vol stays below implied so the scalp gains "
                "fall short of theta while the long-vol structure bleeds (gain < theta)"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        """Enter when the cheapest name's vol is cheap — ``min IV rank ≤ entry_iv_rank_max``.

        Reads every per-name IV-rank reading in the snapshot and ranks the cheapest (lowest IV
        rank = low IV expected to rise = the course's best entry). A snapshot with no IV-rank
        reading is a labelled absence the strategy holds flat on, never a fabricated zero it
        would trade against.
        """
        readings = signals.all_of(SignalKind.IV_RANK)
        if not readings:
            return EntryDecision(
                EntryAction.NOOP, "no IV-rank reading; holding flat"
            )
        cheapest = min(readings, key=lambda r: r.value)
        if cheapest.value <= self.config.entry_iv_rank_max:
            return EntryDecision(
                EntryAction.ENTER,
                f"cheapest name {cheapest.subject!r} IV rank {cheapest.value} <= entry "
                f"{self.config.entry_iv_rank_max}: vol cheap, long-gamma scalp available",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"cheapest name {cheapest.subject!r} IV rank {cheapest.value} above entry "
            f"{self.config.entry_iv_rank_max}; no cheap vol, holding flat",
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        """Fire the kill when the long-gamma thesis is gone — net dollar-gamma at/below the floor.

        The §3 kill is "quiet drift + IV crush (gain < theta)". The observable proxy on the held
        lines is the book's **net dollar-gamma collapsing**: a long-gamma structure that has lost
        its gamma no longer scalps the realized-vol rectangle it was opened for and only bleeds
        theta. (The realized-vs-implied reading itself arrives with the infra signal layer; this
        is the position-side proxy the spine can act on today.)
        """
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_gamma = sum(line.position_gamma for line in market.position_lines)
        if net_gamma <= self.config.exit_gamma_floor:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net gamma {net_gamma} at/below floor {self.config.exit_gamma_floor}: "
                f"long-gamma thesis gone (no rectangle to scalp, only theta bleed)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net gamma {net_gamma} above floor {self.config.exit_gamma_floor}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        """Build the stamped long-gamma structure: a long ATM call + a delta-flattening stock leg.

        Resolves the single cheapest name (raising if none resolves), builds a long ATM call on
        it (routed to the call wing, ADR 0048), then sizes one short stock leg to flatten the
        call's net dollar delta (Δ=0). A negligible hedge (below ``min_hedge_units``) is omitted —
        the call is already delta-flat. The basket is on the single name and carries S3's
        ``strategy_id`` stamp.
        """
        name = self.data.cheapest_name(as_of)
        if name is None:
            raise GammaConstructionError(
                as_of, f"no cheap name (per-name IV rank) resolved for index {self.config.index!r}"
            )

        call_leg = self._call_leg(name)
        stock_legs = self._stock_hedge_legs(name, call_leg, as_of)

        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=name,
            legs=(call_leg, *stock_legs),
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        """Re-hedge when net dollar delta breaches the band — the p.108 scalp-cycle hook.

        Delegates to the shared :func:`~algotrading.strategy.delta_hedge_band.decide_delta_hedge`
        rule (course req #9): S3 is delta-neutral by rule (``target`` 0) and neutralises a breach
        in delta units (``hedge_ratio`` −1, the booker maps that to share count via the name's
        spot), so the only economic input is the band ``half_width`` carried on the config. The
        rule returns ``0.0`` (hold) inside the band — the course's "don't pin delta continuously,
        it bleeds spread" rule — and ``-net_delta`` on band exit, sized to return delta to zero:
        sell stock as delta rises, buy it back lower, each round trip banking the rectangle.
        """
        if not market.position_lines:
            return RebalanceDecision(0.0, "flat; no delta to hedge")
        net_delta = sum(line.position_delta for line in market.position_lines)
        band = DeltaHedgeBand(target=0.0, half_width=self.config.delta_band)
        instruction = decide_delta_hedge(net_delta, band)
        return RebalanceDecision(instruction.hedge_quantity, instruction.reason)

    def _call_leg(self, name: str) -> BasketLeg:
        """The long ATM call on ``name`` — the long-gamma engine, read off the call wing."""
        return BasketLeg(
            instrument_kind="option",
            side="long",
            quantity=self.config.contracts,
            underlying=name,
            tenor_label=self.config.option_tenor,
            delta_band=_ATM_CALL_BAND,
            surface_side=_SURFACE_CALL,
        )

    def _stock_hedge_legs(
        self, name: str, call_leg: BasketLeg, as_of: date
    ) -> tuple[BasketLeg, ...]:
        """Size and build the short stock leg that flattens the call's net dollar delta.

        ``shares = −call_dollar_delta / share_unit_delta`` (the share count whose linear spot
        delta cancels the call's). A long call carries positive delta, so ``shares`` is negative
        — a short stock leg. Below ``min_hedge_units`` the call is already flat and no leg is
        emitted. Refuses (rather than emit a mis-sized hedge) when the grid cannot price the
        call's delta or no spot resolves for the name.
        """
        call_delta = self.data.net_dollar_delta((call_leg,), as_of)
        if call_delta is None:
            raise GammaConstructionError(
                as_of, "grid could not supply the long call's net dollar delta to hedge"
            )
        share_unit = self.data.share_unit_dollar_delta(name, as_of)
        if share_unit is None:
            raise GammaConstructionError(
                as_of, f"no spot resolved for {name!r}; cannot size the stock hedge"
            )
        if share_unit == 0:
            raise GammaConstructionError(
                as_of, f"{name!r} has zero unit dollar delta (spot 0); cannot size a hedge"
            )

        shares = -call_delta / share_unit
        if abs(shares) < self.config.min_hedge_units:
            return ()

        return (
            BasketLeg(
                instrument_kind="stock",
                side=_leg_side(shares),
                quantity=shares,
                underlying=name,
            ),
        )
