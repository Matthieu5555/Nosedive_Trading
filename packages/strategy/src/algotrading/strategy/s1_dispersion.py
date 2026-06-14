"""S1 — the dispersion strategy object (TARGET §3 S1, the flagship week goal).

S1 harvests the **correlation premium**: when index-level implied vol is rich relative to
the implied vols of the index's heaviest constituents on the same tenor, average implied
correlation ρ̄ is high (Eq 23), and a book that is **long single-name vol** and **short the
index** monetises the gap as the names decorrelate. This module is the strategy *object* that
encodes that rule — not the infra it stands on (membership, the analytics grid, basket risk,
the parity forward are all built and injected); it assembles them into the four things a
:class:`~algotrading.strategy.StrategyContract` names: the premium, the entry signal, the
intended Greeks, and the kill condition.

**Construction (v1).** Long ATM straddles on the **point-in-time top-N SX5E constituents by
index weight** (resolved as-of through ``top_n_by_weight`` — never a hand-set list), plus a
**short index leg sized to flatten the basket's net dollar delta**. Until index-futures capture
lands (1D, parked), that short leg is a **synthetic short forward from the index option chain**
(short ATM call + long ATM put at the one ATM-forward strike), priced off the put–call parity
the pipeline already trusts. Each straddle prices its **call leg off the call wing and its put
leg off the put wing** (ADR 0048 ``surface_side`` routing) — S1 is the first consumer of the
per-side surfaces that routing was built for; the synthetic forward stays on the ``combined``
surface (the forward-backing reference, ADR 0048 §3).

**The pure / I/O split.** ``DispersionStrategy`` is a pure function of its injected
``DispersionConfig`` and ``DispersionMarketData`` — no store, no clock, no live read in any
method (the §6 invariant: research == backtest == paper == live). The as-of store reads
(membership ranking, grid dollar-deltas for hedge sizing) live behind the
:class:`DispersionMarketData` protocol; the store-backed adapter that satisfies it for paper/
live is :mod:`algotrading.strategy.dispersion_data`.

**v1 boundary.** v1 shorts the *forward* (delta only) and stays net long vol; v2 (short the
index *straddle* → a pure correlation spread) is an explicit upgrade, out of scope here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    Basket,
    BasketLeg,
)
from algotrading.infra.universe import BasketMember

from .contract import GreekSign, IntendedGreeks, SignalKind, StrategyContract
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
# The two ATM straddle pillars the WS-1F grid emits at the one ATM-forward strike: ``atm`` is
# the ATM *call*, ``atmp`` the ATM *put* (see infra ``surfaces/projection.py``). A long
# straddle is the long pair. These are coordinates into the analytics grid, fixed by the grid
# contract — not a tunable, so they live here, not in YAML.
_ATM_CALL_BAND = "atm"
_ATM_PUT_BAND = "atmp"
# The wing each straddle leg's IV is read from (ADR 0048): the call leg off the call surface,
# the put leg off the put surface. ``"put"``/``"call"`` are the grid's ``surface_side`` values
# (``SURFACE_SIDES``); referenced as the named constants the contract validates against.
_SURFACE_CALL = "call"
_SURFACE_PUT = "put"


class DispersionConstructionError(ValueError):
    """S1 could not build a delta-flat dispersion basket, carrying the failing reason.

    Raised (never silently degraded) when ``construct`` cannot honour its contract: no
    constituents resolve as of the date, or the grid cannot supply the dollar-deltas the
    hedge sizing needs. A partial basket that silently skipped the delta-flattening leg would
    misrepresent its own risk, so S1 refuses rather than emit one.
    """

    def __init__(self, as_of: date, reason: str) -> None:
        self.as_of = as_of
        self.reason = reason
        super().__init__(f"S1 dispersion construct failed as of {as_of}: {reason}")


@dataclass(frozen=True, slots=True)
class DispersionConfig:
    """The economic parameters of an S1 book — injected, never ``.py`` literals (ADR 0028).

    In production these come from the typed platform config: ``index`` and ``top_n`` from the
    ``universe`` bundle (``UniverseConfig.dispersion_top_n``), ``straddle_tenor`` from the
    tenor grid, the thresholds/band from the strategy config. Held here as one frozen record so
    the strategy object stays a pure function of it.

    * ``index`` — the index whose constituents the book trades and whose chain backs the
      synthetic forward (SX5E).
    * ``top_n`` — how many of the index's heaviest names (by as-of index weight) the straddle
      basket spans; the ``top_n_by_weight`` selection size.
    * ``straddle_tenor`` — the tenor-grid label the straddles (and the synthetic forward) sit
      on; both legs of every straddle and the forward share it so the book is one-tenor.
    * ``entry_threshold`` — ρ̄ at or above which implied correlation is "rich" and entry fires.
    * ``contracts_per_name`` — the straddle size per constituent (a positive quantity; the same
      count on the call and put pillar so each name carries a balanced straddle).
    * ``exit_vega_floor`` — net dollar-vega at or below which the long-vol thesis is judged
      gone and :meth:`DispersionStrategy.decide_exit` fires the kill (default ``0.0``).
    * ``delta_band`` — the absolute net dollar-delta band outside which
      :meth:`DispersionStrategy.rebalance` emits a re-hedge.
    * ``min_hedge_units`` — synthetic-forward sizes below this magnitude are treated as "the
      straddles are already delta-flat" and the forward leg is omitted (a forward leg must
      carry a non-zero quantity, and a negligible hedge is noise, not signal).
    """

    index: str
    top_n: int
    straddle_tenor: str
    entry_threshold: float
    contracts_per_name: float = 1.0
    exit_vega_floor: float = 0.0
    delta_band: float = 0.0
    min_hedge_units: float = 1e-6

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("DispersionConfig.index must be non-empty")
        if self.top_n <= 0:
            raise ValueError(f"DispersionConfig.top_n must be positive, got {self.top_n}")
        if not self.straddle_tenor.strip():
            raise ValueError("DispersionConfig.straddle_tenor must be non-empty")
        if self.contracts_per_name <= 0:
            raise ValueError(
                f"DispersionConfig.contracts_per_name must be positive, "
                f"got {self.contracts_per_name}"
            )
        if self.delta_band < 0:
            raise ValueError(
                f"DispersionConfig.delta_band must be non-negative, got {self.delta_band}"
            )
        if self.min_hedge_units < 0:
            raise ValueError(
                f"DispersionConfig.min_hedge_units must be non-negative, "
                f"got {self.min_hedge_units}"
            )


@runtime_checkable
class DispersionMarketData(Protocol):
    """The as-of data S1 reads to construct — the I/O seam behind the pure strategy.

    Every method is parameterised by ``as_of`` (the look-ahead anchor): an implementor reads
    only data available as of that date. The store-backed implementor lives in
    :mod:`algotrading.strategy.dispersion_data`; tests inject a hand-built fake. Keeping these
    three reads behind a protocol is what lets ``DispersionStrategy`` stay a pure function and
    still size a real, grid-derived hedge.
    """

    def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
        """The point-in-time top-``n`` constituents by index weight, ranked, as of ``as_of``."""
        ...

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        """Net dollar delta of ``legs`` off the as-of grid, or ``None`` if any cannot price.

        ``None`` is a labelled "the grid could not supply every leg's delta" — S1 turns it into
        a :class:`DispersionConstructionError` rather than sizing a hedge against a wrong total.
        """
        ...

    def forward_unit_dollar_delta(self, as_of: date) -> float | None:
        """Dollar delta of **one** synthetic short-forward unit on the index, as of ``as_of``.

        One unit is short one ATM call + long one ATM put at the ATM-forward strike on
        ``index`` — a synthetic short forward, whose dollar delta is ≈ −(spot × multiplier).
        ``None`` when the index ATM pillars cannot price; ``0.0`` (a degenerate, un-invertible
        unit) is treated by the caller as un-sizable.
        """
        ...


def _leg_side(quantity: float) -> str:
    """The ``BasketLeg`` side string that agrees with a signed quantity ("long" / "short")."""
    return "long" if quantity > 0 else "short"


@dataclass(frozen=True, slots=True)
class DispersionStrategy:
    """The S1 dispersion strategy object — pure over its ``config`` and injected ``data``.

    Implements the :class:`~algotrading.strategy.Strategy` protocol structurally. Entry reads
    ρ̄ from the injected :class:`~algotrading.strategy.SignalSnapshot` (it does not solve Eq 23 —
    the infra signal layer does); construction resolves the top-N names and sizes the hedge off
    the injected :class:`DispersionMarketData`; exit and rebalance read the held
    :class:`~algotrading.infra.risk.greeks.PositionRisk` lines on the injected
    :class:`~algotrading.strategy.MarketState`. No method touches a clock, a store, or a live
    feed directly, so the same instance fed the same state returns the same step in all four
    contexts (§6).
    """

    config: DispersionConfig
    data: DispersionMarketData

    @property
    def contract(self) -> StrategyContract:
        """S1's frozen §3 contract: correlation premium, ρ̄ signal, long-vol/flat-delta, kill."""
        return StrategyContract(
            strategy_id="S1-dispersion",
            premium_harvested=(
                "correlation premium: index ATM IV rich vs the constituent ATM IVs on the "
                "same tenor (high implied correlation ρ̄)"
            ),
            signal=SignalKind.IMPLIED_CORRELATION,
            intended_greeks=IntendedGreeks(
                # Long single-name gamma/vega, net delta flattened by the short forward, and
                # the long-vol book pays theta — the profile attribution checks P&L against.
                delta=GreekSign.FLAT,
                gamma=GreekSign.LONG,
                vega=GreekSign.LONG,
                theta=GreekSign.SHORT,
            ),
            kill_condition=(
                "the names re-correlate: realized correlation rises and single-name vol "
                "falls together while the long-vol book bleeds theta"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        """Enter when implied correlation ρ̄ is rich — ``ρ̄ ≥ entry_threshold``.

        Reads the ρ̄ reading published for the index (falling back to an index-level reading
        with no subject); a missing reading is a labelled absence the strategy holds flat on,
        never a fabricated zero it would trade against.
        """
        reading = signals.latest(
            SignalKind.IMPLIED_CORRELATION, subject=self.config.index
        ) or signals.latest(SignalKind.IMPLIED_CORRELATION)
        if reading is None:
            return EntryDecision(
                EntryAction.NOOP, "no implied-correlation reading; holding flat"
            )
        if reading.value >= self.config.entry_threshold:
            return EntryDecision(
                EntryAction.ENTER,
                f"rho_bar {reading.value} >= entry {self.config.entry_threshold}: index IV "
                f"rich vs constituents, correlation premium available",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"rho_bar {reading.value} below entry {self.config.entry_threshold}; holding flat",
        )

    def decide_exit(self, market: MarketState) -> ExitDecision:
        """Fire the kill when the long-vol thesis is gone — net dollar-vega at/below the floor.

        The §3 kill is "the names re-correlate: realized correlation up, single-name vol down".
        The observable proxy on the held lines is the book's **net dollar-vega collapsing**: a
        long-single-name-vol book that has lost its vega no longer holds the premium it was
        opened for. (The realized-correlation reading itself arrives with the infra signal
        layer; this is the position-side proxy the spine can act on today.)
        """
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_vega = sum(line.position_vega for line in market.position_lines)
        if net_vega <= self.config.exit_vega_floor:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net vega {net_vega} at/below floor {self.config.exit_vega_floor}: "
                f"long-vol thesis gone (single-name vol collapsed)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net vega {net_vega} above floor {self.config.exit_vega_floor}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        """Build the stamped dispersion basket: top-N straddles + a delta-flattening forward.

        Resolves the point-in-time top-N names (raising if none resolve), builds a long ATM
        straddle per name with the call leg routed to the call wing and the put leg to the put
        wing (ADR 0048), then sizes one synthetic short-forward index leg-pair to flatten the
        straddles' net dollar delta. A negligible hedge (below ``min_hedge_units``) is omitted —
        the straddles are already delta-flat. The basket carries S1's ``strategy_id`` stamp.
        """
        members = self.data.top_n_members(as_of)
        if not members:
            raise DispersionConstructionError(
                as_of, f"no constituents resolved for index {self.config.index!r}"
            )

        straddle_legs = self._straddle_legs(members)
        forward_legs = self._forward_legs(straddle_legs, as_of)

        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=self.config.index,
            legs=straddle_legs + forward_legs,
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        """Re-hedge when net dollar delta breaches the band — the delta-hedge-band hook.

        Returns the signed quantity of the hedge instrument to trade to bring net delta back to
        zero (``-net_delta``), or ``0.0`` (no trade) inside the band. The productionised band
        rule is the shared ``strategy-delta-hedge-band`` lane; this is S1's uniform hook over
        it, matching the spine's "zero quantity == no trade" convention.
        """
        if not market.position_lines:
            return RebalanceDecision(0.0, "flat; no delta to hedge")
        net_delta = sum(line.position_delta for line in market.position_lines)
        if abs(net_delta) <= self.config.delta_band:
            return RebalanceDecision(
                0.0, f"net delta {net_delta} inside band {self.config.delta_band}; no hedge"
            )
        return RebalanceDecision(
            -net_delta,
            f"net delta {net_delta} outside band {self.config.delta_band}; "
            f"hedging {-net_delta}",
        )

    def _straddle_legs(self, members: Sequence[BasketMember]) -> tuple[BasketLeg, ...]:
        """A long ATM straddle per name — the call pillar on the call wing, the put on the put."""
        q = self.config.contracts_per_name
        legs: list[BasketLeg] = []
        for member in members:
            legs.append(
                BasketLeg(
                    instrument_kind="option",
                    side="long",
                    quantity=q,
                    underlying=member.constituent,
                    tenor_label=self.config.straddle_tenor,
                    delta_band=_ATM_CALL_BAND,
                    surface_side=_SURFACE_CALL,
                )
            )
            legs.append(
                BasketLeg(
                    instrument_kind="option",
                    side="long",
                    quantity=q,
                    underlying=member.constituent,
                    tenor_label=self.config.straddle_tenor,
                    delta_band=_ATM_PUT_BAND,
                    surface_side=_SURFACE_PUT,
                )
            )
        return tuple(legs)

    def _forward_legs(
        self, straddle_legs: Sequence[BasketLeg], as_of: date
    ) -> tuple[BasketLeg, ...]:
        """Size and build the synthetic short-forward index leg-pair that flattens net delta.

        ``forward_units = −net_straddle_delta / forward_unit_delta`` (the count of synthetic-
        forward units whose dollar delta cancels the straddles'). One unit is short an ATM call
        + long an ATM put on the index, so ``f`` units is a call leg of quantity ``−f`` and a
        put leg of ``+f`` (a positive ``f`` is the short forward; a negative ``f`` flips both
        sides). Below ``min_hedge_units`` the straddles are already flat and no leg is emitted.
        """
        net_delta = self.data.net_dollar_delta(straddle_legs, as_of)
        if net_delta is None:
            raise DispersionConstructionError(
                as_of, "grid could not supply the straddle legs' net dollar delta to hedge"
            )
        unit_delta = self.data.forward_unit_dollar_delta(as_of)
        if unit_delta is None:
            raise DispersionConstructionError(
                as_of, "grid could not supply the synthetic forward's unit dollar delta"
            )
        if unit_delta == 0:
            raise DispersionConstructionError(
                as_of, "synthetic forward has zero unit dollar delta; cannot size a hedge"
            )

        forward_units = -net_delta / unit_delta
        if abs(forward_units) < self.config.min_hedge_units:
            return ()

        call_qty = -forward_units
        put_qty = forward_units
        return (
            BasketLeg(
                instrument_kind="option",
                side=_leg_side(call_qty),
                quantity=call_qty,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band=_ATM_CALL_BAND,
                surface_side=SURFACE_SIDE_COMBINED,
            ),
            BasketLeg(
                instrument_kind="option",
                side=_leg_side(put_qty),
                quantity=put_qty,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band=_ATM_PUT_BAND,
                surface_side=SURFACE_SIDE_COMBINED,
            ),
        )
