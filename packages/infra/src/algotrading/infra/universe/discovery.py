from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .contracts import EXPIRY_FMT, OptionContract, Right, Underlying
from .errors import UniverseError


@dataclass(frozen=True)
class OptionParams:

    exchange: str
    trading_class: str
    multiplier: str
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]


def _coerce_multiplier(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise UniverseError(f"non-numeric option multiplier {raw!r}") from None
    if value <= 0:
        raise UniverseError(f"option multiplier must be > 0, got {value}")
    return value


def normalize_option_params(
    params: Sequence[OptionParams],
    *,
    underlying: Underlying,
    as_of: date,
    maturity_window: tuple[int, int],
) -> tuple[OptionContract, ...]:
    min_days, max_days = maturity_window
    contracts: list[OptionContract] = []
    for chain in params:
        multiplier = _coerce_multiplier(chain.multiplier)
        for expiry_str in chain.expirations:
            try:
                expiry = datetime.strptime(expiry_str, EXPIRY_FMT).date()
            except ValueError:
                raise UniverseError(f"unparseable expiry {expiry_str!r}") from None
            if not (min_days <= (expiry - as_of).days <= max_days):
                continue
            for strike in chain.strikes:
                for right in (Right.CALL, Right.PUT):
                    try:
                        contracts.append(
                            OptionContract(
                                symbol=underlying.symbol,
                                expiry=expiry,
                                strike=Decimal(str(strike)),
                                right=right,
                                multiplier=multiplier,
                                exchange=chain.exchange,
                                currency=underlying.currency,
                                trading_class=chain.trading_class,
                            )
                        )
                    except (ValueError, ArithmeticError) as exc:
                        raise UniverseError(
                            f"invalid contract {underlying.symbol} {expiry} {strike} {right}: {exc}"
                        ) from exc
    return tuple(contracts)
