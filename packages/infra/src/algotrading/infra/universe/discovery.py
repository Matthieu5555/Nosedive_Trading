"""Normalize a discovered option chain into canonical contracts — pure, no broker SDK.

Given the raw option-parameter sets a chain-discovery API returns for one underlying (exchange,
trading class, multiplier, the set of expirations and strikes), expand them into canonical
:class:`OptionContract`s: both rights at every listed (expiry, strike) inside the monitored
maturity window. Data-quality failures (non-numeric or non-positive multiplier, unparseable
expiry, invalid strike) are raised as :class:`UniverseError` naming the instrument — never
silently dropped. ``OptionParams`` is a plain broker-agnostic record, so this module stays free
of any broker SDK type, like the rest of the package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .contracts import EXPIRY_FMT, OptionContract, Right, Underlying
from .master import UniverseError


@dataclass(frozen=True)
class OptionParams:
    """One raw option-parameter set from chain discovery (one exchange / trading class)."""

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
    """Expand raw chain parameters into canonical option contracts within the maturity window.

    Both rights are emitted at every (expiry, strike) the chain lists. Expiries outside
    ``[min_days, max_days]`` from ``as_of`` are skipped. Identical contracts across overlapping
    parameter sets collapse later in ``build_universe`` (the canonical key is the identity).
    """
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
