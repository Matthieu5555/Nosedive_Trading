"""The store-backed :class:`DispersionMarketData` — S1's as-of I/O adapter for paper/live.

:class:`~algotrading.strategy.s1_dispersion.DispersionStrategy` is pure over an injected
:class:`~algotrading.strategy.s1_dispersion.DispersionMarketData`; this module is the
implementor that satisfies that protocol against the persisted store, so a paper or live
context can run the *same* strategy object over real data. It is the only place S1's data path
touches the store, the membership ranking, or the basket risker — all of which are already
built infra; the adapter only composes them (it adds no risk math of its own).

Every read is keyed by ``as_of`` (the look-ahead anchor): membership through the as-of-gated
``top_n_by_weight`` resolver, and the grid dollar-deltas through a ``trade_date``-narrowed
``store.read`` fed into the pure ``basket_risk``. The adapter holds no clock — the calling
context supplies the date — so it preserves the strategy's research == backtest == paper ==
live invariant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    Basket,
    BasketLeg,
    ProjectedOptionAnalytics,
)
from algotrading.infra.risk.multileg import BasketRisk, basket_risk
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import BasketMember, top_n_by_weight

from .s1_dispersion import DispersionConfig, DispersionStrategy

# The WS-1F analytics grid table the dollar-Greeks are read back from (registry table name).
_ANALYTICS_TABLE = "projected_option_analytics"


def _resolved_dollar_delta(risk: BasketRisk) -> float | None:
    """The basket's net dollar delta, or ``None`` if any leg failed to price.

    ``basket_risk`` returns ``0.0`` (not ``None``) for a basket whose every leg gapped — the
    empty sum — and labels the gaps separately. Sizing a hedge off that ``0.0`` would silently
    treat an *unpriced* basket as delta-flat, so any gap (or a ``None`` aggregate) collapses to
    a labelled ``None`` the strategy refuses to size against.
    """
    if risk.gaps or risk.dollar_delta is None:
        return None
    return risk.dollar_delta


@dataclass(frozen=True, slots=True)
class StoreBackedDispersionData:
    """Resolve S1's membership and grid dollar-deltas from the persisted store, as of a date.

    Holds the store, the S1 :class:`DispersionConfig`, and an optional ``provider`` that
    narrows the provider-partitioned grid read to one source (``None`` reads across providers —
    set it to the book's source, e.g. ``"ibkr"``, to avoid cross-provider ambiguity). All three
    protocol reads are as-of; none touches a wall clock.
    """

    store: ParquetStore
    config: DispersionConfig
    provider: str | None = None

    def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
        """The point-in-time top-``n`` constituents by index weight, as of ``as_of``."""
        return top_n_by_weight(
            self.store, self.config.index, as_of, self.config.top_n
        )

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        """Net dollar delta of ``legs`` off the as-of grid, or ``None`` if any cannot price.

        Reads each distinct underlying's grid rows for ``as_of`` and feeds them, with the legs,
        to the pure :func:`~algotrading.infra.risk.multileg.basket_risk`; its aggregate
        ``dollar_delta`` is ``None`` exactly when a contributing leg could not be resolved (a
        labelled gap), which the strategy turns into a refusal rather than a wrong hedge.
        """
        rows = self._analytics_rows({leg.underlying for leg in legs}, as_of)
        risk = basket_risk(
            self._scratch_basket(tuple(legs), as_of),
            analytics_rows=rows,
            spot_by_underlying={},
        )
        return _resolved_dollar_delta(risk)

    def forward_unit_dollar_delta(self, as_of: date) -> float | None:
        """Dollar delta of one synthetic short-forward unit on the index, as of ``as_of``.

        One unit is short an ATM call (``atm``) + long an ATM put (``atmp``) at the ATM-forward
        strike on the index, both off the ``combined`` surface (the forward-backing reference,
        ADR 0048 §3). Priced through ``basket_risk`` on the index grid rows.
        """
        unit_legs = (
            BasketLeg(
                instrument_kind="option",
                side="short",
                quantity=-1.0,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band="atm",
                surface_side=SURFACE_SIDE_COMBINED,
            ),
            BasketLeg(
                instrument_kind="option",
                side="long",
                quantity=1.0,
                underlying=self.config.index,
                tenor_label=self.config.straddle_tenor,
                delta_band="atmp",
                surface_side=SURFACE_SIDE_COMBINED,
            ),
        )
        rows = self._analytics_rows({self.config.index}, as_of)
        risk = basket_risk(
            self._scratch_basket(unit_legs, as_of),
            analytics_rows=rows,
            spot_by_underlying={},
        )
        return _resolved_dollar_delta(risk)

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

    def _scratch_basket(self, legs: tuple[BasketLeg, ...], as_of: date) -> Basket:
        """A throwaway basket wrapping ``legs`` so ``basket_risk`` can aggregate their deltas."""
        return Basket(
            basket_id="s1-hedge-sizing",
            trade_date=as_of,
            underlying=self.config.index,
            legs=legs,
            provider=self.provider,
        )


def dispersion_strategy(
    store: ParquetStore, config: DispersionConfig, *, provider: str | None = None
) -> DispersionStrategy:
    """Build a store-backed S1 strategy object ready to run in paper/live.

    Wires the pure :class:`~algotrading.strategy.s1_dispersion.DispersionStrategy` to a
    :class:`StoreBackedDispersionData` over ``store``. The ``config`` is the typed S1 record —
    in production its ``top_n`` is ``UniverseConfig.dispersion_top_n`` and its ``index`` /
    ``straddle_tenor`` come from the universe bundle, never ``.py`` literals.
    """
    return DispersionStrategy(
        config=config,
        data=StoreBackedDispersionData(store=store, config=config, provider=provider),
    )
