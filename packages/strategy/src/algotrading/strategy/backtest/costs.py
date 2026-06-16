from __future__ import annotations

from dataclasses import dataclass

from algotrading.infra.pricing import price
from algotrading.infra.risk.valuation import ContractValuationInput, pricing_state_for

from .data import HeldContract


@dataclass(frozen=True, slots=True)
class TransactionCostModel:

    commission_per_contract: float = 0.0
    slippage_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.commission_per_contract < 0.0:
            raise ValueError(
                "TransactionCostModel.commission_per_contract must be non-negative, "
                f"got {self.commission_per_contract}"
            )
        if self.slippage_rate < 0.0:
            raise ValueError(
                "TransactionCostModel.slippage_rate must be non-negative, "
                f"got {self.slippage_rate}"
            )

    def entry_cost(
        self, held: HeldContract, valuation: ContractValuationInput | None
    ) -> float:
        contracts = abs(held.quantity)
        commission = self.commission_per_contract * contracts
        if valuation is None:
            return commission
        unit_price = price(pricing_state_for(valuation)).price
        notional = abs(unit_price) * valuation.multiplier * contracts
        return commission + self.slippage_rate * notional


NO_COST = TransactionCostModel()
