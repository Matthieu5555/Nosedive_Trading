"""Live IBKR option-chain discovery: resolve an underlying's conId, then fetch its chain params.

Imports ``ib_async`` and is kept out of the package ``__init__`` (like ``ibkr_adapter``) so that
importing the collectors layer never drags the broker client. Returns broker-agnostic
:class:`~algotrading.infra.universe.OptionParams` records that the pure normalizer
(:func:`~algotrading.infra.universe.normalize_option_params`) turns into canonical contracts —
so no broker SDK type ever reaches the ``universe`` package.

Discovery is two calls: ``qualifyContracts`` to resolve the underlying's ``conId``, then
``reqSecDefOptParams`` (which requires that conId) to obtain each listed exchange/trading-class
chain's expirations, strikes and multiplier.
"""

from __future__ import annotations

from algotrading.core.log import get_logger
from algotrading.infra.universe import OptionParams, Underlying
from ib_async import IB, Index, Stock

_log = get_logger(__name__)


class DiscoveryError(RuntimeError):
    """Raised when an underlying cannot be resolved or its chain cannot be discovered."""


def _underlying_contract(underlying: Underlying) -> Index | Stock:
    """Map a canonical underlying to the ib_async contract used to resolve its conId."""
    if underlying.security_type == "IND":
        return Index(underlying.symbol, underlying.exchange, underlying.currency)
    return Stock(underlying.symbol, underlying.exchange, underlying.currency)


class IbkrUniverseDiscovery:
    """Discover option-chain parameters for an underlying via ib_async."""

    def __init__(self, ib: IB) -> None:
        self._ib = ib

    def fetch(self, underlying: Underlying) -> tuple[OptionParams, ...]:
        """Resolve the underlying conId, then request its option-chain parameter sets."""
        qualified = self._ib.qualifyContracts(_underlying_contract(underlying))
        if not qualified:
            raise DiscoveryError(f"could not qualify underlying {underlying.symbol!r}")
        con_id = qualified[0].conId
        # futFopExchange="" requests listed (non-FOP) option params; the conId from qualification
        # is mandatory so IBKR returns chains for this exact underlying, not a name collision.
        chains = self._ib.reqSecDefOptParams(
            underlying.symbol, "", underlying.security_type, con_id
        )
        params = tuple(
            OptionParams(
                exchange=chain.exchange,
                trading_class=chain.tradingClass,
                multiplier=chain.multiplier,
                expirations=tuple(sorted(chain.expirations)),
                strikes=tuple(sorted(float(strike) for strike in chain.strikes)),
            )
            for chain in chains
        )
        _log.info(
            "discovered option chains",
            extra={"underlying": underlying.symbol, "con_id": con_id, "chains": len(params)},
        )
        return params
