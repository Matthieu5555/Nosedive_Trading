from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from algotrading.infra.risk.greeks import PositionRisk, position_risk
from algotrading.infra.risk.valuation import ContractValuationInput

from .data import BacktestData, HeldContract

_BACKTEST_PORTFOLIO_ID = "backtest"


@dataclass(frozen=True, slots=True)
class PricedBook:

    as_of: date
    lines: tuple[PositionRisk, ...]
    valuations: Mapping[str, ContractValuationInput]
    unpriced: tuple[str, ...]


@dataclass
class BacktestBook:

    held: list[HeldContract] = field(default_factory=list)

    def add(self, contracts: list[HeldContract]) -> None:
        self.held.extend(contracts)

    def expire(self, as_of: date) -> list[HeldContract]:
        rolled = [c for c in self.held if c.expiry <= as_of]
        self.held = [c for c in self.held if c.expiry > as_of]
        return rolled

    @property
    def open_contract_count(self) -> float:
        return float(len(self.held))

    def price(self, data: BacktestData, as_of: date) -> PricedBook:
        lines: list[PositionRisk] = []
        valuations: dict[str, ContractValuationInput] = {}
        unpriced: list[str] = []
        for contract in self.held:
            valuation = data.valuation(contract, as_of)
            if valuation is None:
                unpriced.append(contract.contract_key)
                continue
            lines.append(
                position_risk(
                    portfolio_id=_BACKTEST_PORTFOLIO_ID,
                    quantity=contract.quantity,
                    valuation=valuation,
                )
            )
            valuations[contract.contract_key] = valuation
        return PricedBook(
            as_of=as_of,
            lines=tuple(lines),
            valuations=valuations,
            unpriced=tuple(unpriced),
        )
