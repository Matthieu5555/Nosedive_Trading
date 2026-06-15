"""The store-backed :class:`GammaMarketData` — S3's as-of I/O adapter for paper/live.

:class:`~algotrading.strategy.s3_gamma.GammaStrategy` is pure over an injected
:class:`~algotrading.strategy.s3_gamma.GammaMarketData`; this module is the implementor that
satisfies that protocol against the persisted store, so a paper or live context can run the
*same* strategy object over real data. It is the only place S3's data path touches the store,
the persisted signal layer, or the basket risker — all of which are already built infra; the
adapter only composes them (it adds no risk math of its own).

Every read is keyed by ``as_of`` (the look-ahead anchor): the cheapest name from the banked
``strategy_signals`` IV-rank partition, the call's dollar-delta through a ``trade_date``-narrowed
``store.read`` fed into the pure ``basket_risk``, and the name's spot from the as-of grid's
forward price. The adapter holds no clock — the calling context supplies the date — so it
preserves the strategy's research == backtest == paper == live invariant.

**Spot source.** The stock hedge's per-share delta is the name's spot. The pipeline pins
``carry == 0`` (``surfaces/projection.py``: "carry == 0: forward == spot"), so the as-of grid's
``forward_price`` *is* the as-of spot — read from the same ATM-call cell the call leg prices
against, needing no second table. A later reprice at a moved spot leaves residual delta, which
is exactly what the p.108 band rebalance is for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    Basket,
    BasketLeg,
    ProjectedOptionAnalytics,
)
from algotrading.infra.risk.multileg import BasketRisk, basket_risk
from algotrading.infra.storage import ParquetStore

from .contract import SignalKind
from .s3_gamma import GammaConfig, GammaStrategy

# The WS-1F analytics grid table the dollar-Greeks and forward (spot) are read back from.
_ANALYTICS_TABLE = "projected_option_analytics"
# The persisted signal layer table and the per-name IV-rank kind string (the strategy-layer
# ``SignalKind.IV_RANK`` value; infra mirrors it as a constant, blind to this enum).
_SIGNALS_TABLE = "strategy_signals"
_IV_RANK_KIND = SignalKind.IV_RANK.value
# The ATM-call cell the call leg prices against — also the cell whose forward_price is the
# name's as-of spot (carry == 0 ⇒ forward == spot).
_ATM_CALL_BAND = "atm"
_SURFACE_CALL = "call"


def _resolved_dollar_delta(risk: BasketRisk) -> float | None:
    """The basket's net dollar delta, or ``None`` if any leg failed to price.

    ``basket_risk`` returns ``0.0`` (not ``None``) for a basket whose every leg gapped — the
    empty sum — and labels the gaps separately. Sizing a hedge off that ``0.0`` would silently
    treat an *unpriced* call as delta-flat, so any gap (or a ``None`` aggregate) collapses to a
    labelled ``None`` the strategy refuses to size against.
    """
    if risk.gaps or risk.dollar_delta is None:
        return None
    return risk.dollar_delta


@dataclass(frozen=True, slots=True)
class StoreBackedGammaData:
    """Resolve S3's cheapest name, call delta, and name spot from the persisted store, as of a date.

    Holds the store, the S3 :class:`GammaConfig`, the ``reference_tenor`` the per-name IV-rank
    signal is published at (the entry gate's reference tenor, p.36), and an optional ``provider``
    that narrows the provider-partitioned grid + signal reads to one source (``None`` reads
    across providers — set it to the book's source, e.g. ``"ibkr"``, to avoid cross-provider
    ambiguity). All three protocol reads are as-of; none touches a wall clock.
    """

    store: ParquetStore
    config: GammaConfig
    reference_tenor: str
    provider: str | None = None

    def cheapest_name(self, as_of: date) -> str | None:
        """The constituent with the lowest banked IV rank as of ``as_of``, or ``None``.

        Reads the ``strategy_signals`` partition for ``(as_of, index, provider)``, keeps the
        per-name IV-rank readings at ``reference_tenor`` (one per name by construction), and
        returns the subject of the minimum (lowest IV rank = cheapest vol = the course's best
        entry). ``None`` when no per-name IV-rank reading was banked — a labelled absence S3
        refuses to guess a name against.
        """
        rows = self.store.read(
            _SIGNALS_TABLE, trade_date=as_of, underlying=self.config.index, provider=self.provider
        )
        candidates = [
            row
            for row in rows
            if row.signal_kind == _IV_RANK_KIND
            and row.tenor_label == self.reference_tenor
            and row.subject is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r.value).subject

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        """Net dollar delta of ``legs`` off the as-of grid, or ``None`` if any cannot price.

        Reads each distinct underlying's grid rows for ``as_of`` and feeds them, with the legs,
        to the pure :func:`~algotrading.infra.risk.multileg.basket_risk`; its aggregate
        ``dollar_delta`` is ``None`` exactly when a contributing leg could not be resolved (a
        labelled gap), which the strategy turns into a refusal rather than a wrong hedge.
        """
        rows = self._analytics_rows({leg.underlying for leg in legs}, as_of)
        risk = basket_risk(
            self._scratch_basket(tuple(legs), as_of, underlying=legs[0].underlying if legs else ""),
            analytics_rows=rows,
            spot_by_underlying={},
        )
        return _resolved_dollar_delta(risk)

    def share_unit_dollar_delta(self, name: str, as_of: date) -> float | None:
        """Dollar delta of one long share of ``name`` — its as-of spot (grid forward, carry==0).

        Reads ``name``'s ATM-call grid cell for ``as_of`` (the same cell the call leg prices
        against) and returns its ``forward_price``: with the pipeline's pinned ``carry == 0`` the
        forward equals the spot, and a share's linear delta is ``1 × spot``. ``None`` when that
        cell is absent (no spot to size against).
        """
        rows = self._analytics_rows({name}, as_of)
        for row in rows:
            if (
                row.delta_band == _ATM_CALL_BAND
                and row.surface_side == _SURFACE_CALL
                and row.tenor_label == self.config.option_tenor
            ):
                return row.forward_price
        return None

    def _analytics_rows(
        self, underlyings: set[str], as_of: date
    ) -> list[ProjectedOptionAnalytics]:
        """Every grid row for the given underlyings on ``as_of``, narrowed to this provider."""
        rows: list[ProjectedOptionAnalytics] = []
        for underlying in sorted(underlyings):
            rows.extend(
                row
                for row in self.store.read(
                    _ANALYTICS_TABLE,
                    trade_date=as_of,
                    underlying=underlying,
                    provider=self.provider,
                )
                if row.underlying == underlying
            )
        return rows

    def _scratch_basket(
        self, legs: tuple[BasketLeg, ...], as_of: date, *, underlying: str
    ) -> Basket:
        """A throwaway basket wrapping ``legs`` so ``basket_risk`` can aggregate their deltas."""
        return Basket(
            basket_id="s3-hedge-sizing",
            trade_date=as_of,
            underlying=underlying,
            legs=legs,
            provider=self.provider,
        )


def gamma_strategy(
    store: ParquetStore,
    config: GammaConfig,
    *,
    reference_tenor: str,
    provider: str | None = None,
) -> GammaStrategy:
    """Build a store-backed S3 strategy object ready to run in paper/live.

    Wires the pure :class:`~algotrading.strategy.s3_gamma.GammaStrategy` to a
    :class:`StoreBackedGammaData` over ``store``. The ``config`` is the typed S3 record — in
    production its ``index`` / ``option_tenor`` come from the universe bundle and its entry
    threshold / band from the strategy config, never ``.py`` literals; ``reference_tenor`` is
    the tenor the per-name IV-rank signal is published at (p.36).
    """
    return GammaStrategy(
        config=config,
        data=StoreBackedGammaData(
            store=store, config=config, reference_tenor=reference_tenor, provider=provider
        ),
    )
