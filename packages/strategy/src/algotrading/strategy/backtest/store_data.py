from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    BasketLeg,
    ProjectedOptionAnalytics,
)
from algotrading.infra.risk.valuation import ContractValuationInput
from algotrading.infra.storage import ParquetStore

from ..signal_data import signal_snapshot_from_store
from ..signals import SignalSnapshot
from .data import HeldContract

_ANALYTICS_TABLE = "projected_option_analytics"
_DAYS_PER_YEAR = 365.0


def _option_right_for_band(delta_band: str, target_delta: float) -> str:
    if delta_band.endswith("p"):
        return "P"
    if delta_band.endswith("c"):
        return "C"
    return "P" if target_delta < 0.0 else "C"


def _contract_key(underlying: str, option_right: str, strike: float) -> str:
    return f"{underlying}|OPT|{option_right}|{strike:.4f}"


@dataclass(frozen=True, slots=True)
class StoreBackedBacktestData:

    store: ParquetStore
    index: str
    reference_tenor: str
    multiplier: float
    currency: str
    provider: str
    discount_factor: float = 1.0

    def signals(self, as_of: date) -> SignalSnapshot:
        return signal_snapshot_from_store(
            self.store,
            as_of,
            index=self.index,
            provider=self.provider,
            reference_tenor=self.reference_tenor,
        )

    def concretize_leg(self, leg: BasketLeg, as_of: date) -> HeldContract | None:
        row = self._cell_row(leg, as_of)
        if row is None:
            return None
        option_right = _option_right_for_band(row.delta_band, row.target_delta)
        contract_key = _contract_key(leg.underlying, option_right, row.strike)
        expiry = as_of + timedelta(days=round(row.maturity_years * _DAYS_PER_YEAR))
        return HeldContract(
            contract_key=contract_key,
            quantity=leg.quantity,
            expiry=expiry,
            leg=leg,
        )

    def valuation(
        self, held: HeldContract, as_of: date
    ) -> ContractValuationInput | None:
        row = self._cell_row(held.leg, as_of)
        if row is None:
            return None
        if not math.isfinite(row.implied_vol) or row.implied_vol <= 0.0:
            return None
        if not math.isfinite(row.maturity_years) or row.maturity_years <= 0.0:
            return None
        option_right = _option_right_for_band(row.delta_band, row.target_delta)
        return ContractValuationInput(
            contract_key=held.contract_key,
            underlying=held.leg.underlying,
            option_right=option_right,
            exercise_style="european",
            strike=row.strike,
            maturity_years=row.maturity_years,
            spot=row.forward_price,
            carry=0.0,
            volatility=row.implied_vol,
            discount_factor=self.discount_factor,
            multiplier=self.multiplier,
            currency=self.currency,
        )

    def _cell_row(
        self, leg: BasketLeg, as_of: date
    ) -> ProjectedOptionAnalytics | None:
        surface_side = leg.surface_side or SURFACE_SIDE_COMBINED
        tenor_label = leg.tenor_label
        delta_band = leg.delta_band
        for row in self.store.read(
            _ANALYTICS_TABLE,
            trade_date=as_of,
            underlying=leg.underlying,
            provider=self.provider,
        ):
            if (
                row.underlying == leg.underlying
                and row.tenor_label == tenor_label
                and row.delta_band == delta_band
                and row.surface_side == surface_side
            ):
                return row
        return None
