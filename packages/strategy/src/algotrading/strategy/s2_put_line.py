"""S2 — the index short-put production line (TARGET §3 S2, course p.128–130 "Allocation Factory").

S2 harvests the **index variance risk premium on the left tail**: index downside implied vol
runs persistently richer than realized, so a *systematic* line that sells one OTM index put per
day collects that premium as theta while it carries a short-vega, short-gamma, long-delta book.
It is the deliberate **opposite tail to S1** — together they are a relative-value position on
index-vs-single-name vol. This module is the strategy *object* that encodes the line's rules —
not the infra it stands on (the index option chain + the delta-band grid are built and the
booked rolling position is read back as injected state); it assembles them into the four things
a :class:`~algotrading.strategy.StrategyContract` names: the premium, the entry signal, the
intended Greeks, and the kill condition.

**The line, not a trade.** Unlike S1/S3 (one delta-neutral structure), S2 is a *rolling line*:

* **Daily sell** — :meth:`PutLineStrategy.decide_sell` fires when the premium is on offer
  (index ``RV − IV`` at/below the configured ceiling: implied richer than realized) **and** the
  line is under its capacity cap. ``construct`` then emits the one short ~25Δ, ~30-day index put
  to add (at the steered strike distance), routed to the **put wing** (ADR 0048).
* **Line capacity** — a typed config cap on open contracts (course: 30, rolling so one expires
  daily); :meth:`PutLineStrategy.line_at_capacity` is the pure rule, ADR 0028 config not a
  literal.
* **Steering** — the strike distance (the put delta band, ≈ 2.5 % / 3 % / 4 % below market) is a
  config knob that controls assignment frequency; moving it is the steering lever, deterministic
  given config (a rule, not discretion).
* **Kill** — a sharp sustained drawdown (the short left tail) flattens the whole line.

**The pure / I/O split.** ``PutLineStrategy`` is a pure function of its ``PutLineConfig`` plus
the injected :class:`~algotrading.strategy.SignalSnapshot` (the entry signal) and
:class:`~algotrading.strategy.MarketState` (the held line) — no store, no clock, no live read in
any method (the §6 invariant: research == backtest == paper == live). It needs **no** store-backed
data adapter: construction is config-only (the candidate strike is a grid coordinate, resolved
downstream), the signal arrives through the existing
:func:`~algotrading.strategy.signal_snapshot_from_store` bridge, and the open-contract count for
the capacity gate is derived by the caller from the booked line.

**Cross-lane seam.** The *enforcing* kill switch and the up-front margin/assignment sizing (the
course's InvWC number) live in ``execution-operational-hardening`` (§5.9/§6) — S2 is their first
consumer. This object *emits* a flatten decision off a position-side proxy (net delta breaching a
configured ceiling — the short puts going in-the-money as spot falls); the true drawdown /
vol-regime trigger and the enforcement are execution's, not built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import Basket, BasketLeg

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
# The put leg is read off the put surface (ADR 0048): the ≈25Δ OTM put band (e.g. ``"24dp"``)
# on the put wing. ``"put"`` is the grid's ``surface_side`` value (``SURFACE_SIDES``).
_SURFACE_PUT = "put"


@dataclass(frozen=True, slots=True)
class PutLineConfig:
    """The economic parameters of an S2 line — injected, never ``.py`` literals (ADR 0028).

    In production these come from the typed platform config: ``index`` / ``put_tenor`` from the
    universe bundle, the capacity / steering / thresholds from the strategy config. Held here as
    one frozen record so the strategy object stays a pure function of it.

    * ``index`` — the index the line sells puts on (SX5E).
    * ``put_tenor`` — the ~30-day tenor-grid label the daily put sits on.
    * ``put_delta_band`` — the **steered** strike distance: the OTM put delta band the line sells
      (e.g. ``"24dp"`` ≈ 25Δ ≈ 3 % below market). Moving it deeper (further OTM, a lower Δ band)
      lowers assignment frequency — the course's 2.5 / 3 / 4 % steering lever. Validated to be a
      put-wing band (ends with ``"p"``) so it routes coherently to the put surface.
    * ``line_capacity`` — the cap on open contracts (course: 30, rolling so one expires daily);
      :meth:`PutLineStrategy.line_at_capacity` enforces it. A positive integer.
    * ``contracts_per_day`` — the size sold each day (a positive quantity; one put per day = 1.0).
    * ``max_rv_minus_iv`` — the ``RV − IV`` spread at or **below** which implied is judged richer
      than realized and the daily sell fires. ``0.0`` (default) means "implied at least as rich
      as realized"; a negative value demands a wider premium cushion before selling.
    * ``exit_delta_ceiling`` — the net dollar-delta at or **above** which
      :meth:`PutLineStrategy.decide_exit` flattens the line (the position-side proxy for the
      drawdown kill: the short puts going ITM as spot falls drives net delta up). ``None``
      (default) means "no position-side proxy here — defer the flatten to the execution kill
      switch" (§5.9/§6). When set it must be positive (a short-put line carries positive delta).
    """

    index: str
    put_tenor: str
    put_delta_band: str
    line_capacity: int
    contracts_per_day: float = 1.0
    max_rv_minus_iv: float = 0.0
    exit_delta_ceiling: float | None = None

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValueError("PutLineConfig.index must be non-empty")
        if not self.put_tenor.strip():
            raise ValueError("PutLineConfig.put_tenor must be non-empty")
        if not self.put_delta_band.endswith("p"):
            raise ValueError(
                f"PutLineConfig.put_delta_band must be a put-wing band (end with 'p'), "
                f"got {self.put_delta_band!r}"
            )
        if self.line_capacity <= 0:
            raise ValueError(
                f"PutLineConfig.line_capacity must be positive, got {self.line_capacity}"
            )
        if self.contracts_per_day <= 0:
            raise ValueError(
                f"PutLineConfig.contracts_per_day must be positive, got {self.contracts_per_day}"
            )
        if self.exit_delta_ceiling is not None and self.exit_delta_ceiling <= 0:
            raise ValueError(
                f"PutLineConfig.exit_delta_ceiling must be positive when set, "
                f"got {self.exit_delta_ceiling}"
            )


@dataclass(frozen=True, slots=True)
class PutLineStrategy:
    """The S2 index short-put line strategy object — pure over its ``config`` plus injected state.

    Implements the :class:`~algotrading.strategy.Strategy` protocol structurally, and adds the
    line-specific :meth:`decide_sell` (the daily decision = signal gate ∧ capacity gate) and
    :meth:`line_at_capacity` (the capacity rule). Entry reads the index ``RV − IV`` from the
    injected :class:`~algotrading.strategy.SignalSnapshot`; the kill reads the held
    :class:`~algotrading.infra.risk.greeks.PositionRisk` lines on the injected
    :class:`~algotrading.strategy.MarketState`; construction is config-only. No method touches a
    clock, a store, or a live feed directly, so the same instance fed the same state returns the
    same step in all four contexts (§6).
    """

    config: PutLineConfig

    @property
    def contract(self) -> StrategyContract:
        """S2's frozen §3 contract: left-tail variance premium, RV−IV signal, short-vol, kill."""
        return StrategyContract(
            strategy_id="S2-index-put-line",
            premium_harvested=(
                "index left-tail variance risk premium: index downside implied vol runs richer "
                "than realized, harvested as theta by a systematic short-put line"
            ),
            signal=SignalKind.IV_VS_REALIZED,
            intended_greeks=IntendedGreeks(
                # A short OTM put line: short downside vega and gamma, positive (earned) theta,
                # and a deliberately carried long delta — the short left tail, the opposite of
                # S1's flat-delta book. Attribution checks P&L lands in vega/theta, the delta is
                # the risk that kills it.
                delta=GreekSign.LONG,
                gamma=GreekSign.SHORT,
                vega=GreekSign.SHORT,
                theta=GreekSign.LONG,
            ),
            kill_condition=(
                "sharp sustained drawdown: spot falls through the put strikes and the short left "
                "tail hits (the line carries the loss); a vol-regime spike compounds it"
            ),
        )

    def decide_entry(self, as_of: date, signals: SignalSnapshot) -> EntryDecision:
        """Decide whether the premium is on offer today — ``RV − IV ≤ max_rv_minus_iv``.

        Reads the index's ``RV − IV`` reading (falling back to an index-level reading with no
        subject); a value at/below the ceiling means implied is richer than realized — the
        premium the line harvests is available, so the signal gate opens. A missing reading is a
        labelled absence the strategy holds flat on, never a fabricated zero it would sell against.

        This is the **signal** half of the daily decision; :meth:`decide_sell` ANDs it with the
        capacity gate. The §6 harness calls this (it is pure of position state), so the
        four-context invariance holds.
        """
        reading = signals.latest(
            SignalKind.IV_VS_REALIZED, subject=self.config.index
        ) or signals.latest(SignalKind.IV_VS_REALIZED)
        if reading is None:
            return EntryDecision(
                EntryAction.NOOP, "no IV-vs-realized reading; holding flat"
            )
        if reading.value <= self.config.max_rv_minus_iv:
            return EntryDecision(
                EntryAction.ENTER,
                f"RV-IV {reading.value} <= {self.config.max_rv_minus_iv}: index downside IV "
                f"rich vs realized, left-tail premium on offer",
            )
        return EntryDecision(
            EntryAction.NOOP,
            f"RV-IV {reading.value} above {self.config.max_rv_minus_iv}; implied not rich "
            f"enough vs realized, holding flat",
        )

    def line_at_capacity(self, open_contracts: float) -> bool:
        """Whether the line is full — ``open_contracts ≥ line_capacity``.

        The pure capacity rule (course: 30 open, rolling so one expires daily). ``open_contracts``
        is the count the caller derives from the booked rolling line (the fills/position store);
        at capacity the line stops adding even when the signal gate is open.
        """
        return open_contracts >= self.config.line_capacity

    def decide_sell(
        self, as_of: date, signals: SignalSnapshot, *, open_contracts: float
    ) -> EntryDecision:
        """The daily sell decision: the signal gate **and** the capacity gate.

        ``ENTER`` (add one day's put) only when the premium is on offer
        (:meth:`decide_entry`) **and** the line is under capacity
        (:meth:`line_at_capacity`); otherwise ``NOOP`` carrying which gate held it. This is the
        operational decision the paper/live line driver calls each day, deriving
        ``open_contracts`` from the booked line; it is pure of its arguments, so it is
        context-invariant exactly like the protocol methods.
        """
        if self.line_at_capacity(open_contracts):
            return EntryDecision(
                EntryAction.NOOP,
                f"line at capacity ({open_contracts} >= {self.config.line_capacity}); "
                f"not selling",
            )
        return self.decide_entry(as_of, signals)

    def decide_exit(self, market: MarketState) -> ExitDecision:
        """Flatten the line when the short left tail hits — net delta at/above the ceiling.

        The §3 kill is "sharp sustained drawdown". The position-side proxy on the held lines is
        the line's **net dollar delta climbing**: as spot falls through the put strikes the short
        puts go in-the-money and their (positive) delta rises toward the full notional — the loss
        the short left tail was always going to carry. With no ``exit_delta_ceiling`` configured
        the strategy holds and defers the flatten to the execution kill switch (§5.9/§6, the true
        drawdown / vol-regime trigger); with one set it emits the flatten the spine can act on.
        """
        if self.config.exit_delta_ceiling is None:
            return ExitDecision(
                ExitAction.HOLD,
                "no position-side kill proxy configured; deferring flatten to the execution "
                "kill switch",
            )
        if not market.position_lines:
            return ExitDecision(ExitAction.HOLD, "flat; nothing to exit")
        net_delta = sum(line.position_delta for line in market.position_lines)
        if net_delta >= self.config.exit_delta_ceiling:
            return ExitDecision(
                ExitAction.FLATTEN,
                f"net delta {net_delta} at/above ceiling {self.config.exit_delta_ceiling}: "
                f"short puts going ITM, the left tail is hitting (drawdown kill)",
            )
        return ExitDecision(
            ExitAction.HOLD,
            f"net delta {net_delta} below ceiling {self.config.exit_delta_ceiling}; holding",
        )

    def construct(self, as_of: date, *, basket_id: str) -> Basket:
        """Build the stamped one-put add: a single short ~25Δ OTM index put at the steered band.

        Config-only — the put is one short leg at the configured ``put_delta_band`` (the steered
        strike distance) and ``put_tenor``, on the put wing (ADR 0048), sized ``contracts_per_day``.
        Each ``construct`` adds **one day's** put to the rolling line (the line is the accumulation
        of these across days, capacity-capped by :meth:`decide_sell`). The basket carries S2's
        ``strategy_id`` stamp.
        """
        put_leg = BasketLeg(
            instrument_kind="option",
            side="short",
            quantity=-self.config.contracts_per_day,
            underlying=self.config.index,
            tenor_label=self.config.put_tenor,
            delta_band=self.config.put_delta_band,
            surface_side=_SURFACE_PUT,
        )
        return Basket(
            basket_id=basket_id,
            trade_date=as_of,
            underlying=self.config.index,
            legs=(put_leg,),
            strategy_id=self.contract.strategy_id,
        )

    def rebalance(self, market: MarketState) -> RebalanceDecision:
        """No band hedge — S2 carries its short-put delta intentionally (the short left tail).

        Unlike S1/S3, S2 is *not* delta-neutral by rule: the carried long delta is the strategy
        (the deliberate opposite tail to S1), so there is nothing to re-hedge. Returns the spine's
        zero-quantity no-op so the hook stays uniform across S1–S5.
        """
        return RebalanceDecision(
            0.0, "S2 carries its short-put delta intentionally; no band hedge"
        )
