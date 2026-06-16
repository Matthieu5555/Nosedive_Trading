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

_ANALYTICS_TABLE = "projected_option_analytics"


def _resolved_dollar_delta(risk: BasketRisk) -> float | None:
    if risk.gaps or risk.dollar_delta is None:
        return None
    return risk.dollar_delta


@dataclass(frozen=True, slots=True)
class StoreBackedDispersionData:

    store: ParquetStore
    config: DispersionConfig
    provider: str | None = None

    def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
        return top_n_by_weight(
            self.store, self.config.index, as_of, self.config.top_n
        )

    def net_dollar_delta(self, legs: Sequence[BasketLeg], as_of: date) -> float | None:
        rows = self._analytics_rows({leg.underlying for leg in legs}, as_of)
        risk = basket_risk(
            self._scratch_basket(tuple(legs), as_of),
            analytics_rows=rows,
            spot_by_underlying={},
        )
        return _resolved_dollar_delta(risk)

    def forward_unit_dollar_delta(self, as_of: date) -> float | None:
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
    return DispersionStrategy(
        config=config,
        data=StoreBackedDispersionData(store=store, config=config, provider=provider),
    )
