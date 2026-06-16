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

_ANALYTICS_TABLE = "projected_option_analytics"
_SIGNALS_TABLE = "strategy_signals"
_IV_RANK_KIND = SignalKind.IV_RANK.value
_ATM_CALL_BAND = "atm"
_SURFACE_CALL = "call"


def _resolved_dollar_delta(risk: BasketRisk) -> float | None:
    if risk.gaps or risk.dollar_delta is None:
        return None
    return risk.dollar_delta


@dataclass(frozen=True, slots=True)
class StoreBackedGammaData:

    store: ParquetStore
    config: GammaConfig
    reference_tenor: str
    provider: str | None = None

    def cheapest_name(self, as_of: date) -> str | None:
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
        rows = self._analytics_rows({leg.underlying for leg in legs}, as_of)
        risk = basket_risk(
            self._scratch_basket(tuple(legs), as_of, underlying=legs[0].underlying if legs else ""),
            analytics_rows=rows,
            spot_by_underlying={},
        )
        return _resolved_dollar_delta(risk)

    def share_unit_dollar_delta(self, name: str, as_of: date) -> float | None:
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
    return GammaStrategy(
        config=config,
        data=StoreBackedGammaData(
            store=store, config=config, reference_tenor=reference_tenor, provider=provider
        ),
    )
